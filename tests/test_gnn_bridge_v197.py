"""文献库 → GNN 反馈队列批量桥接（v1.9.7 P4）测试。

不打真实预测器：`gnn_feedback.submit` 走注入打桩，只验证筛选、幂等、权重分层与溯源。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import gnn_bridge, library_db  # noqa: E402

PAPERS = {
    "1": {"title": "Film-forming COF A", "doi": "10.1000/a", "source": "builtin"},
    "2": {"title": "Non-film COF B", "doi": "10.1000/b", "source": "builtin"},
}
ALD = "O=Cc1ccc(C=O)cc1"
AMINE = "Nc1ccc(N)cc1"
ENTRIES = [
    # 正样本（LLM 提取，未复核）
    {"entry_id": "e1", "paper_id": "1", "kind": "film_outcome", "film_label": 1.0,
     "ald_smiles": ALD, "amine_smiles": AMINE, "evidence": "完整成膜", "source": "llm_extract"},
    # 正样本（用户确认 → 高权重）
    {"entry_id": "e2", "paper_id": "1", "kind": "film_outcome", "film_label": 1.0,
     "ald_smiles": ALD, "amine_smiles": "Nc1ccccc1", "evidence": "用户确认成膜", "source": "user"},
    # 负样本（不应入队）
    {"entry_id": "e3", "paper_id": "2", "kind": "film_outcome", "film_label": 0.0,
     "ald_smiles": ALD, "amine_smiles": AMINE, "evidence": "失败", "source": "user"},
    # 缺 SMILES（跳过）
    {"entry_id": "e4", "paper_id": "1", "kind": "film_outcome", "film_label": 1.0,
     "evidence": "无 SMILES", "source": "user"},
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    res = tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
    (data / "literature" / "knowledge_entries.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in ENTRIES), encoding="utf-8")

    patched = []
    for mod_name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(mod_name)
    assert patched
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    library_db.reset_ready_cache()
    yield {"data": data}
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    yield c
    c.close()


class FakeFeedback:
    """打桩的 GNN 反馈队列（只接受能成网的组合，模拟真实校验）。"""

    def __init__(self, reject_amine: str | None = None):
        self.rows: list[dict] = []
        self.reject_amine = reject_amine

    def submit(self, ald_smiles, amine_smiles, label, note="",
               source="score_correction", refs=None):
        if self.reject_amine and amine_smiles == self.reject_amine:
            raise ValueError("该醛胺组合不能成网")
        rec = {"feedback_id": f"fb_{len(self.rows) + 1:03d}", "source": source,
               "ald_smiles": ald_smiles, "amine_smiles": amine_smiles,
               "label": float(label), "note": note, "refs": list(refs or [])}
        self.rows.append(rec)
        return rec


def test_collect_candidates_filters_and_weights(conn):
    cands = gnn_bridge.collect_candidates(conn)
    ids = [c["entry_id"] for c in cands]
    assert ids == ["e1", "e2"]                      # 只见正样本且有 SMILES
    weights = {c["entry_id"]: c["weight"] for c in cands}
    assert weights["e2"] == gnn_bridge.WEIGHT_USER  # 用户确认 → 高权重
    assert weights["e1"] == gnn_bridge.WEIGHT_LLM
    assert all(c["paper_id"] == "1" for c in cands)


def test_collect_candidates_by_collection_and_tag(conn):
    library_db.assign_collection(conn, "1", "col_cof_film", add=True)
    assert len(gnn_bridge.collect_candidates(conn, collection_id="col_cof_film")) == 2
    assert gnn_bridge.collect_candidates(conn, collection_id="col_review") == []
    library_db.set_tag(conn, "2", "负样本", add=True)
    assert gnn_bridge.collect_candidates(conn, tag="负样本") == []


def test_enqueue_is_idempotent_and_records_links(conn, monkeypatch):
    fb = FakeFeedback()
    monkeypatch.setattr(gnn_bridge, "_feedback", lambda: fb)
    out = gnn_bridge.enqueue(conn)
    assert out["submitted"] == 2 and out["skipped"] == 0
    # 溯源 refs 含 paper/entry/doi
    assert fb.rows[0]["refs"][0] == "paper:1"
    assert fb.rows[0]["refs"][1] == "entry:e1"
    assert any(r.startswith("doi:") for r in fb.rows[0]["refs"])
    assert fb.rows[0]["source"] == "literature_pdf"

    links = gnn_bridge.links_for_paper(conn, "1")
    assert len(links) == 2
    assert {l["weight"] for l in links} == {gnn_bridge.WEIGHT_USER, gnn_bridge.WEIGHT_LLM}

    # 再跑一次：已入队的不重复提交
    again = gnn_bridge.enqueue(conn)
    assert again["submitted"] == 0 and again["candidates"] == 0
    assert len(fb.rows) == 2


def test_enqueue_dry_run_and_rejection(conn, monkeypatch):
    fb = FakeFeedback()
    monkeypatch.setattr(gnn_bridge, "_feedback", lambda: fb)
    dry = gnn_bridge.enqueue(conn, dry_run=True)
    assert dry["dry_run"] and dry["candidates"] == 2 and fb.rows == []
    assert dry["weights"] == {"user": 1, "llm": 1}

    # 队列拒绝不可成网组合 → 计入 skipped，不影响其它条目
    fb2 = FakeFeedback(reject_amine="Nc1ccccc1")
    monkeypatch.setattr(gnn_bridge, "_feedback", lambda: fb2)
    out = gnn_bridge.enqueue(conn)
    assert out["submitted"] == 1 and out["skipped"] == 1


def test_mark_used_and_summary(conn, monkeypatch):
    fb = FakeFeedback()
    monkeypatch.setattr(gnn_bridge, "_feedback", lambda: fb)
    out = gnn_bridge.enqueue(conn)
    marked = gnn_bridge.mark_used(conn, "gnn_v9")
    assert marked == 2
    links = gnn_bridge.links_for_paper(conn, "1")
    assert all(l["used_version"] == "gnn_v9" for l in links)
    s = gnn_bridge.summary(conn)
    assert s["links"] == 2 and s["papers"] == 1
    assert s["by_version"].get("gnn_v9") == 2
    # 指定 feedback_ids 标记
    assert gnn_bridge.mark_used(conn, "gnn_v10", [out["feedback_ids"][0]]) == 1
