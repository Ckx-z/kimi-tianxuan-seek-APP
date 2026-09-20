"""知识图谱扩展（v1.9.7 P3）测试：实体节点 / cites（Crossref）/ similar_to（向量）。

不打真实网络与真实模型：Crossref 走注入 refs，向量走注入 embedder。
"""

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

from literature import graph_extend, library_db  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

PAPERS = {
    "1": {"title": "Fluorinated COF coatings for SPME", "doi": "10.1016/j.talanta.1",
          "abstract": "Fluorinated covalent organic frameworks for solid-phase microextraction",
          "journal": "Talanta", "year": 2026, "source": "builtin"},
    "2": {"title": "Triazine COFs for CO2 separation", "doi": "10.1000/triazine",
          "abstract": "Triazine-based covalent organic frameworks for gas separation and dyes",
          "journal": "JACS", "year": 2025, "source": "builtin"},
    "3": {"title": "Unrelated zeolite membrane study", "doi": "10.1000/zeolite",
          "abstract": "Zeolite membranes for pervaporation dehydration of alcohols",
          "journal": "JMS", "year": 2024, "source": "builtin"},
}
ENTRIES = [
    {"entry_id": "e1", "paper_id": "1", "kind": "characterization", "technique": "xrd",
     "metrics": [{"name": "比表面积", "value": 2105, "unit": "m2/g"}]},
    {"entry_id": "e2", "paper_id": "1", "kind": "property", "property_name": "接触角",
     "metrics": []},
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


# ---------------------------------------------------------------- 实体

def test_sync_entities_creates_nodes_and_edges(conn):
    out = graph_extend.sync_entities(conn, "1")
    assert out["techniques"] == 1 and out["metrics"] == 1 and out["properties"] == 1
    nodes = {r["node_id"]: r for r in conn.execute(
        "SELECT node_id,node_type,name FROM graph_nodes")}
    assert nodes["tech:PXRD"]["node_type"] == "technique"      # xrd → PXRD 归一化
    assert nodes["metric:比表面积"]["node_type"] == "metric"
    assert nodes["prop:接触角"]["node_type"] == "property"
    edges = {(r["subject"], r["predicate"], r["object"]) for r in conn.execute(
        "SELECT subject,predicate,object FROM graph_edges")}
    assert ("paper:1", "uses_technique", "tech:PXRD") in edges
    assert ("paper:1", "reports_metric", "metric:比表面积") in edges
    assert ("paper:1", "reports_property", "prop:接触角") in edges
    # 幂等：再跑一次不新增
    again = graph_extend.sync_entities(conn, "1")
    assert again["techniques"] == 0 and again["metrics"] == 0 and again["properties"] == 0


# ---------------------------------------------------------------- cites

def test_sync_citations_with_injected_refs(conn):
    refs = [
        {"doi": "10.1000/triazine", "title": "Triazine COFs", "year": 2025},   # 本地已有
        {"doi": "10.9999/external", "title": "External work", "year": 2020},   # 外部
    ]
    out = graph_extend.sync_citations(conn, "1", "10.1016/j.talanta.1", refs=refs)
    assert out["new_edges"] == 2 and out["resolved_local"] == 1
    nb = graph_extend.neighbors(conn, "1")
    dois = {c["node"]: c for c in nb["cites"]}
    assert "paper:2" in dois                     # 命中本地文献
    assert "ext:10.9999/external" in dois        # 外部文献建 ext 节点
    assert dois["paper:2"]["source"] == "crossref"
    # 反向（被引）
    nb2 = graph_extend.neighbors(conn, "2")
    assert any(x["node"] == "paper:1" for x in nb2["cited_by"])
    # DOI 归一化：大小写/URL 前缀都能对到同一条边
    assert graph_extend.normalize_doi("https://doi.org/10.9999/EXTERNAL") == "10.9999/external"
    again = graph_extend.sync_citations(conn, "1", "", refs=refs)
    assert again["new_edges"] == 0 and again["duplicate_edges"] == 2


def test_crossref_fetch_failure_is_silent(monkeypatch):
    monkeypatch.setattr(graph_extend.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    assert graph_extend.fetch_crossref_references("10.1000/x") == []


# ---------------------------------------------------------------- similar

def test_sync_similar_with_injected_embedder(conn):
    """向量注入：1 与 2 语义相近 → 写边；3 无关 → 不写。"""
    table = {"1": [1.0, 0.0, 0.0], "2": [0.98, 0.05, 0.0], "3": [0.0, 0.0, 1.0]}

    def fake_embed(texts):
        out = []
        for t in texts:
            out.append(next(v for pid, v in table.items() if pid in t or True)
                       if False else None)
        return out

    # 用标题匹配更稳妥：直接按 docs 顺序给向量
    order = [str(r["paper_id"]) for r in conn.execute(
        "SELECT paper_id FROM items WHERE deleted_at IS NULL ORDER BY added_at DESC")]
    def embedder(texts):
        return [table[pid] for pid in order[: len(texts)]]

    out = graph_extend.sync_similar(conn, embedder=embedder, min_score=0.7)
    assert out["edges"] >= 1
    nb = graph_extend.neighbors(conn, "1")
    partners = {s["node"] for s in nb["similar"]}
    assert "paper:2" in partners
    assert "paper:3" not in partners
    assert all(s["source"] == "embedding" for s in nb["similar"])
    # 幂等
    out2 = graph_extend.sync_similar(conn, embedder=embedder, min_score=0.7)
    assert out2["edges"] == 0


def test_similar_without_embedder_is_graceful(conn, monkeypatch):
    monkeypatch.setattr(graph_extend, "_embed", lambda texts, embedder=None: None)
    out = graph_extend.sync_similar(conn)
    assert out["edges"] == 0 and "向量" in out["reason"]


# ---------------------------------------------------------------- 维护 / 统计

def test_clear_source_and_stats(conn):
    graph_extend.sync_entities(conn, "1")
    graph_extend.sync_citations(conn, "1", "", refs=[{"doi": "10.1/x", "title": "X"}])
    removed = graph_extend.clear_source(conn, "crossref")
    assert removed == 1
    st = graph_extend.graph_stats(conn)
    assert st["techniques"] == 1 and st["metrics"] == 1
    assert "cites" not in st["by_predicate"]
    assert st["by_predicate"].get("uses_technique") == 1


# ---------------------------------------------------------------- API

def test_api_graph_sync_and_neighbors(env, monkeypatch):
    """API：entities 同步 + 邻居查询（citations 关掉网络，用 refs 注入走库层已验证）。"""
    r = client.post("/api/library/graph/sync",
                    params={"paper_id": "1", "kinds": "entities", "fetch": "false"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entities"][0]["techniques"] == 1
    assert body["stats"]["techniques"] == 1

    n = client.get("/api/library/graph/neighbors/1")
    assert n.status_code == 200
    assert n.json()["paper_id"] == "1"
    assert n.json()["cites"] == []

    s = client.get("/api/library/graph/stats")
    assert s.status_code == 200 and s.json()["nodes"] >= 1

    # 按来源撤销（entities 用 entry 作为 source）
    d = client.delete("/api/library/graph/source/entry")
    assert d.status_code == 200 and d.json()["removed"] >= 1
