"""G1+G2 测试：助手「文献图谱」工具（lit_graph）。

覆盖：工具已注册 / 结构化参数拼 DSL / 各 mode 返回 / 只读与非法输入防护 /
analytics 指标 / 与 query_graphrag 并存（两套图各有工具）。
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

from assistant import registry  # noqa: E402
from assistant.tools import litgraph  # noqa: E402
from literature import graph_analytics, graph_extend, library_db  # noqa: E402

PAPERS = {
    "1": {"title": "Hub paper", "doi": "10.1/a", "year": 2024, "source": "builtin"},
    "2": {"title": "Cited A", "doi": "10.1/b", "year": 2023, "source": "builtin"},
    "3": {"title": "Cited B", "doi": "10.1/c", "year": 2022, "source": "builtin"},
}
ENTRIES = [
    {"entry_id": "e1", "paper_id": "1", "kind": "characterization", "technique": "PXRD",
     "metrics": [{"name": "Langmuir surface area", "value": 3336, "unit": "m2/g"}]},
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
    library_db.ensure_ready()
    conn = library_db.connect(data / "literature" / "library.db")
    # 图：1→2→3 引用链；1 用 PXRD；指标来自条目
    for s, o in (("1", "2"), ("2", "3")):
        graph_extend._upsert_edge(conn, f"paper:{s}", "cites", f"paper:{o}",
                                  source="crossref", confidence=1.0)
    graph_extend._upsert_edge(conn, "paper:1", "similar_to", "paper:3",
                              source="embedding", confidence=0.8)
    graph_extend.sync_entities(conn, "1")
    graph_analytics.ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO graph_metrics(node_id,metric,value,rank,"
                 "computed_at) VALUES('paper:2','pagerank',0.5,1,'t')")
    conn.commit()
    conn.close()
    yield {"data": data}
    library_db.reset_ready_cache()


# ---------------------------------------------------------------- 注册

def test_tool_registered_alongside_graphrag():
    names = {t["function"]["name"] for t in registry.list_tool_schemas()}
    assert "lit_graph" in names, f"应已注册，实际 {sorted(names)}"
    assert "query_graphrag" in names, "原有化学反应网络工具必须保留（两套图并存）"
    desc = registry.describe_tools()
    assert "lit_graph" in desc


# ---------------------------------------------------------------- DSL 拼装

def test_build_dsl_from_structured_args():
    assert litgraph._build_dsl("neighbors", {"paper_id": "1529", "depth": 2,
                                             "predicates": "cites"}) == \
        "neighbors 1529 depth=2 pred=cites"
    assert litgraph._build_dsl("path", {"from_paper": "1", "to_paper": "3",
                                        "max_depth": 4}) == "path 1 3 max=4"
    assert litgraph._build_dsl("by_metric", {"name": "比表面积", "min": 2000}) == \
        "by_metric 比表面积 min=2000"
    assert litgraph._build_dsl("top", {"metric": "pagerank", "limit": 5}) == \
        "top pagerank limit=5"
    with pytest.raises(ValueError):
        litgraph._build_dsl("neighbors", {})
    with pytest.raises(ValueError):
        litgraph._build_dsl("bogus", {})


# ---------------------------------------------------------------- 各 mode

def test_neighbors_and_path(env):
    out = litgraph.lit_graph_tool({"mode": "neighbors", "paper_id": "1", "depth": 2})
    assert "error" not in out and out["op"] == "neighbors"
    assert {"1", "2", "3"} <= set(out["nodes"].keys())
    # 边带来源，助手可区分"确证引用"与"算法推荐"
    sources = {e["source"] for e in out["edges"]}
    assert "crossref" in sources

    p = litgraph.lit_graph_tool({"mode": "path", "from_paper": "1", "to_paper": "3"})
    assert p["found"] is True and p["path"][0] == "1" and p["path"][-1] == "3"


def test_cites_of_and_by_entity_and_by_metric(env):
    c = litgraph.lit_graph_tool({"mode": "cites_of", "paper_id": "1", "direction": "both"})
    assert c["op"] == "cites_of" and c["count"] >= 1

    e = litgraph.lit_graph_tool({"mode": "by_entity", "kind": "technique", "value": "PXRD"})
    assert "1" in e["papers"]

    m = litgraph.lit_graph_tool({"mode": "by_metric", "name": "比表面积", "min": 2000})
    assert m["count"] == 1 and m["metrics"][0]["value"] == 3336


def test_top_and_analytics(env):
    t = litgraph.lit_graph_tool({"mode": "top", "metric": "pagerank", "limit": 5})
    assert t["op"] == "top" and t["results"][0]["paper_id"] == "2"

    tl = litgraph.lit_graph_tool({"mode": "analytics", "kind": "timeline"})
    assert tl["total"] >= 1
    pr = litgraph.lit_graph_tool({"mode": "analytics", "kind": "pagerank", "limit": 3})
    assert "results" in pr


def test_readonly_and_bad_input_are_refused(env):
    bad = litgraph.lit_graph_tool({"mode": "top", "dsl": "drop table items"})
    assert "error" in bad, "写操作必须被拒绝"
    unknown = litgraph.lit_graph_tool({"mode": "bogus"})
    assert "error" in unknown and "hint" in unknown
    none = litgraph.lit_graph_tool({})
    assert "error" in none


def test_dsl_passthrough(env):
    out = litgraph.lit_graph_tool({"mode": "top", "dsl": "top pagerank limit=2"})
    assert out["dsl"] == "top pagerank limit=2" and out["op"] == "top"
