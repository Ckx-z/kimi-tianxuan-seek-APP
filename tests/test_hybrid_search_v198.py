"""C1 混合检索（RRF）测试：融合数学 / 三态模式 / 来源标注 / 降级 / API。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import hybrid_search, library_db  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

PAPERS = {
    "1": {"title": "Fluorinated COF for SPME", "abstract": "fluorinated framework",
          "doi": "10.1/a", "source": "builtin"},
    "2": {"title": "BET surface area study", "abstract": "porosity of COFs",
          "doi": "10.1/b", "source": "builtin"},
    "3": {"title": "Unrelated zeolite", "abstract": "membranes", "doi": "10.1/c",
          "source": "builtin"},
}
ENTRIES = [
    {"entry_id": "e1", "paper_id": "1", "kind": "characterization", "technique": "SPME",
     "metrics": [{"name": "比表面积", "value": 2105, "unit": "m2/g"}]},
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, res = tmp_path / "data", tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
    (data / "literature" / "knowledge_entries.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in ENTRIES), encoding="utf-8")
    patched = []
    for name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(name)
    assert patched
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    monkeypatch.setenv("COF_DATA_DIR", str(data))
    library_db.reset_ready_cache()
    yield {"data": data}
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    # 造图：paper:1 用 SPME 技术 + 报道比表面积；1 与 2 相似
    from literature import graph_extend
    graph_extend.sync_entities(c, "1")
    graph_extend._upsert_edge(c, "paper:1", "similar_to", "paper:2",
                              source="embedding", confidence=0.9)
    c.commit()
    yield c
    c.close()


# ---------------------------------------------------------------- 融合数学

def test_rrf_fuse_scores_and_ranks():
    fused = hybrid_search.rrf_fuse([["a", "b"], ["b", "c"]],
                                   sources=["vector", "graph"], k=60)
    by = {e["paper_id"]: e for e in fused}
    # b 在两路都出现 → 分最高
    assert fused[0]["paper_id"] == "b"
    assert by["b"]["score"] > by["a"]["score"]
    assert by["b"]["ranks"] == {"vector": 2, "graph": 1}
    assert abs(by["a"]["score"] - 1 / 61) < 1e-6      # 分数保留 6 位小数
    assert fused[0]["contributions"]["vector"] > 0
    assert hybrid_search.rrf_fuse([]) == []


def test_rrf_fuse_weights_favour_one_route():
    fused = hybrid_search.rrf_fuse([["a"], ["b"]], weights=[3.0, 1.0],
                                   sources=["vector", "graph"])
    assert fused[0]["paper_id"] == "a"      # 权重高的那路胜出


# ---------------------------------------------------------------- 三态

def test_graph_route_hits_entity_then_paper(conn):
    ids, note = hybrid_search.graph_route(conn, "SPME")
    assert "1" in ids and not note
    # 指标名也能命中
    ids2, _ = hybrid_search.graph_route(conn, "比表面积")
    assert "1" in ids2


def test_hybrid_merges_both_routes_with_source_labels(conn):
    def fake_embedder(q, top_k):
        return [{"paper_id": "2", "score": 0.9}, {"paper_id": "3", "score": 0.5}]

    out = hybrid_search.hybrid_search(conn, "SPME", mode="hybrid", top_k=5,
                                      embedder=fake_embedder)
    assert out["mode"] == "hybrid" and out["degraded"] is False
    assert set(out["routes"]) == {"vector", "graph"}
    by = {r["paper_id"]: r for r in out["results"]}
    assert "1" in by and "2" in by
    # 来源标注：1 来自 graph，2 来自 vector（3 只有向量命中）
    assert by["1"]["sources"] == ["graph"] or "graph" in by["1"]["sources"]
    assert "vector" in by["2"]["sources"]
    assert by["1"]["title"].startswith("Fluorinated")


def test_mode_switching(conn):
    def fake_embedder(q, top_k):
        return [{"paper_id": "3", "score": 0.9}]

    v = hybrid_search.hybrid_search(conn, "SPME", mode="vector", embedder=fake_embedder)
    assert [r["paper_id"] for r in v["results"]] == ["3"]
    g = hybrid_search.hybrid_search(conn, "SPME", mode="graph")
    assert "1" in [r["paper_id"] for r in g["results"]]
    assert g["routes"] == ["graph"]
    with pytest.raises(ValueError):
        hybrid_search.hybrid_search(conn, "x", mode="bogus")


def test_degradation_when_vector_unavailable(conn, monkeypatch):
    """向量不可用 → 自动降级 graph-only 并在返回里注明，不整体失败。"""
    def boom(q, top_k):
        raise RuntimeError("no model")

    out = hybrid_search.hybrid_search(conn, "SPME", mode="hybrid", embedder=boom)
    assert out["degraded"] is True
    assert out["routes"] == ["graph"]
    assert any("向量" in n for n in out["notes"])
    assert out["results"], "仍应返回图路结果"


def test_empty_query(conn):
    out = hybrid_search.hybrid_search(conn, "   ")
    assert out["results"] == [] and out["total"] == 0
