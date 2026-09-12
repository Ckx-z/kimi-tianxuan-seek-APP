"""文献入知识图谱（v1.9.3 第 7 点）测试。

覆盖：
- 文献节点 upsert（node_type=literature，检索字段 system/innovation 对齐内置图）；
- 元数据更新合并（幂等：无变化返回 False）；
- 组节点 → 文献节点 reaction_cited_in 边；
- 录入确认（POST /confirm）即写文献节点，响应 graphrag_indexed=true；
- 空文献（无元数据）也能建节点（仅 paper_id），可被后续补解析充实。
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import graph_ingest, knowledge  # noqa: E402
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"
B5 = "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "ENTRIES_PATH",
                        tmp_path / "literature" / "knowledge_entries.jsonl")
    monkeypatch.setattr(graph_ingest, "_app_root", lambda: tmp_path)
    patched = []
    for mod_name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(mod_name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", tmp_path / "paper_titles.json")
            patched.append(mod)
    (tmp_path / "paper_titles.json").write_text(json.dumps({
        "1": {"title": "TFPT 膜文献", "doi": "10.1000/tfpt",
              "journal": "JACS", "year": 2025, "authors": ["Alice", "Bob"],
              "abstract": "TFPT 与 B5 在管壁扩散成膜。"},
    }, ensure_ascii=False), encoding="utf-8")
    for mod in patched:
        mod.reload()
    yield tmp_path
    for mod in patched:
        mod.reload()


def _load_graph(tmp_path: Path):
    fp = tmp_path / "data" / "graphrag_user" / "graph_user.pkl"
    assert fp.is_file(), "侧车图应已写入"
    with open(fp, "rb") as f:
        return pickle.load(f)


def _entry(group="G1"):
    return {
        "paper_id": "1",
        "kind": "film_outcome", "group_id": group,
        "experiment": f"{group}：TFPT+B5 成膜", "ald_smiles": TFPT,
        "amine_smiles": B5, "film_label": 1.0,
        "evidence": "壁上形成连续光滑的薄膜",
    }


# ---------------------------------------------------------------- 文献节点

def test_upsert_paper_node_creates_literature_node(isolate):
    assert graph_ingest.upsert_paper_node("1") is True
    G = _load_graph(isolate)
    nid = graph_ingest.paper_node_id("1")
    assert nid in G
    d = G.nodes[nid]
    assert d["node_type"] == "literature"
    assert d["source"] == "literature"
    assert d["title"] == "TFPT 膜文献"
    assert d["system"] == "TFPT 膜文献"        # query_graphrag 检索字段
    assert "管壁扩散成膜" in d["innovation"]   # query_graphrag 检索字段
    assert d["journal"] == "JACS"
    assert d["doi"] == "10.1000/tfpt"
    assert d["url"] == "https://doi.org/10.1000/tfpt"
    assert d["authors"] == "Alice，Bob"


def test_upsert_paper_node_is_idempotent_and_merges(isolate):
    assert graph_ingest.upsert_paper_node("1") is True
    assert graph_ingest.upsert_paper_node("1") is False        # 无变化
    assert graph_ingest.upsert_paper_node("1", {
        "title": "TFPT 膜文献", "doi": "10.1000/tfpt",
        "abstract": "更新后的摘要（补解析回填）",
    }) is True
    G = _load_graph(isolate)
    d = G.nodes[graph_ingest.paper_node_id("1")]
    assert "更新后的摘要" in d["innovation"]
    assert d["journal"] == "JACS"    # 未提供的字段保留


def test_upsert_paper_node_without_metadata(isolate):
    """文献库里没有该 paper_id 时也建节点（后续补解析可充实）。"""
    assert graph_ingest.upsert_paper_node("999") is True
    G = _load_graph(isolate)
    d = G.nodes[graph_ingest.paper_node_id("999")]
    assert d["paper_id"] == "999" and d["title"] == ""


def test_sync_group_links_reaction_to_paper_node(isolate):
    n = graph_ingest.sync_groups([_entry()])
    assert n == 1
    G = _load_graph(isolate)
    rnode = graph_ingest.lit_node_id("1", "G1")
    pnode = graph_ingest.paper_node_id("1")
    assert rnode in G and pnode in G
    ets = {d.get("edge_type")
           for d in G.get_edge_data(rnode, pnode, default={}).values()}
    assert "reaction_cited_in" in ets


def test_api_entries_also_creates_paper_node(isolate):
    entries = knowledge.add_entries("1", [_entry()])
    graph_ingest.sync_groups(entries)
    G = _load_graph(isolate)
    assert graph_ingest.paper_node_id("1") in G


# ---------------------------------------------------------------- 录入即入图

def test_confirm_intake_indexes_paper_node(isolate):
    r = client.post("/api/literature/confirm", json={
        "title": "New COF Paper", "authors": ["C"], "journal": "Nature",
        "year": 2026, "doi": "10.1000/new", "abstract": "新文献摘要",
        "reviewed_by": "user",
    })
    assert r.status_code == 201
    body = r.json()
    pid = body["paper_id"]
    assert body["graphrag_indexed"] is True
    G = _load_graph(isolate)
    nid = graph_ingest.paper_node_id(pid)
    assert nid in G
    assert G.nodes[nid]["title"] == "New COF Paper"
    assert G.nodes[nid]["innovation"] == "新文献摘要"
    assert "知识图谱" in body["message"]


def test_patch_paper_updates_graph_node(isolate):
    """补解析回填的元数据同步到文献节点；已有字段（abstract）按 rule 跳过。"""
    # 1) 已有 abstract：只补空字段 → 跳过，节点不变
    graph_ingest.upsert_paper_node("1")
    r = client.patch("/api/literature/papers/1",
                     json={"abstract": "不该覆盖的摘要"})
    assert r.status_code == 200
    assert "abstract" in r.json()["skipped"]
    G = _load_graph(isolate)
    assert "不该覆盖" not in G.nodes[graph_ingest.paper_node_id("1")]["innovation"]
    # 2) 空 abstract 的新文献：回填后节点 innovation 同步更新
    import json as _json
    lib = _json.loads((isolate / "paper_titles.json").read_text(encoding="utf-8"))
    lib["2"] = {"title": "只有标题的新文献", "doi": "10.1000/two"}
    (isolate / "paper_titles.json").write_text(
        _json.dumps(lib, ensure_ascii=False), encoding="utf-8")
    titles.reload()
    graph_ingest.upsert_paper_node("2")
    r2 = client.patch("/api/literature/papers/2",
                      json={"abstract": "补解析回填的新摘要内容"})
    assert r2.status_code == 200
    assert "abstract" in r2.json()["updated"]
    G2 = _load_graph(isolate)
    assert "补解析回填的新摘要内容" in \
        G2.nodes[graph_ingest.paper_node_id("2")]["innovation"]
