"""C4 受限图查询 DSL 测试：解析 / 六个算子 / 上限保护 / 只读安全 / 自然语言翻译。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import graph_extend, graph_query, library_db  # noqa: E402

PAPERS = {
    "1": {"title": "P1", "doi": "10.1/1", "year": 2020, "source": "builtin"},
    "2": {"title": "P2", "doi": "10.1/2", "year": 2021, "source": "builtin"},
    "3": {"title": "P3", "doi": "10.1/3", "year": 2022, "source": "builtin"},
    "4": {"title": "P4", "doi": "10.1/4", "year": 2023, "source": "builtin"},
}
ENTRIES = [
    {"entry_id": "e1", "paper_id": "1", "kind": "characterization", "technique": "PXRD",
     "metrics": [{"name": "比表面积", "value": 2105, "unit": "m2/g"}]},
    {"entry_id": "e2", "paper_id": "2", "kind": "characterization", "technique": "PXRD",
     "metrics": [{"name": "比表面积", "value": 800, "unit": "m2/g"}]},
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
    graph_extend.sync_entities(c, "1")
    graph_extend.sync_entities(c, "2")
    for s, o in (("1", "2"), ("2", "3"), ("3", "4")):
        graph_extend._upsert_edge(c, f"paper:{s}", "cites", f"paper:{o}",
                                  source="crossref", confidence=1.0)
    graph_extend._upsert_edge(c, "paper:4", "similar_to", "paper:1",
                              source="embedding", confidence=0.8)
    graph_extend._upsert_edge(c, "paper:1", "reports_metric", "metric:比表面积",
                              source="entry")
    # 排行用指标（graph_metrics 由 C2 模块定义，先确保建表）
    from literature import graph_analytics
    graph_analytics.ensure_schema(c)
    c.execute("INSERT OR REPLACE INTO graph_metrics(node_id,metric,value,rank,"
              "computed_at) VALUES('paper:1','pagerank',0.5,1,'t')")
    c.execute("INSERT OR REPLACE INTO graph_metrics(node_id,metric,value,rank,"
              "computed_at) VALUES('paper:2','pagerank',0.2,2,'t')")
    c.commit()
    yield c
    c.close()


# ---------------------------------------------------------------- 解析

def test_parse_all_ops():
    assert graph_query.parse("neighbors 1529 depth=2 pred=cites,sim")["predicates"] == \
        ("cites", "similar_to")
    assert graph_query.parse("path 1 4 max=5")["max_depth"] == 5
    e = graph_query.parse("by_entity technique PXRD limit=5")
    assert e["kind"] == "technique" and e["value"] == "PXRD" and e["limit"] == 5
    m = graph_query.parse("by_metric 比表面积 min=1000 max=3000")
    assert m["min"] == 1000.0 and m["max"] == 3000.0
    assert graph_query.parse("cites_of 1 dir=in")["direction"] == "in"
    assert graph_query.parse("top pagerank limit=3")["metric"] == "pagerank"


def test_parse_rejects_bad_input():
    for bad in ("", "drop table items", "neighbors", "neighbors 1 pred=bogus",
                "path 1", "by_entity unknown X", "by_metric", "cites_of 1 dir=sideways",
                "top bogus", "neighbors 1 depth=abc", "top pagerank extra=1"):
        with pytest.raises(ValueError):
            graph_query.parse(bad)


def test_caps_enforced():
    """越界的 depth/limit 会被**夹取到上限**（而不是报错），防止大图拖垮查询。"""
    assert graph_query.parse("neighbors 1 depth=99")["depth"] == graph_query.MAX_DEPTH
    assert graph_query.parse("path 1 2 max=99")["max_depth"] == graph_query.MAX_PATH
    capped = graph_query.parse(f"by_metric x limit={graph_query.MAX_LIMIT + 999}")
    assert capped["limit"] == graph_query.MAX_LIMIT
    assert graph_query.parse("neighbors 1 depth=2")["depth"] == 2


# ---------------------------------------------------------------- 执行

def test_execute_neighbors_depth_and_predicates(conn):
    out = graph_query.execute(conn, "neighbors 1 depth=1 pred=cites")
    assert set(out["nodes"].keys()) == {"1", "2"}          # 只走 cites 一跳
    deep = graph_query.execute(conn, "neighbors 1 depth=3")
    assert {"1", "2", "3", "4"} <= set(deep["nodes"].keys())
    assert any(e["predicate"] == "similar_to" for e in deep["edges"])


def test_execute_path_found_and_not_found(conn):
    # 1 与 4 之间有**直连的相似边** → 最短路径 1 跳（BFS 取最短，符合预期）
    out = graph_query.execute(conn, "path 1 4 max=4")
    assert out["found"] is True
    assert out["path"] == ["1", "4"] and out["length"] == 1
    # 多跳：4 →(3 或 1)→ 2 需要 2 跳
    multi = graph_query.execute(conn, "path 4 2 max=4")
    assert multi["found"] is True and multi["length"] == 2
    miss = graph_query.execute(conn, "path 1 999 max=2")
    assert miss["found"] is False and "未找到" in miss["message"]


def test_execute_by_entity_and_by_metric(conn):
    ent = graph_query.execute(conn, "by_entity technique PXRD")
    assert set(ent["papers"].keys()) == {"1", "2"}
    met = graph_query.execute(conn, "by_metric 比表面积 min=1000")
    assert met["count"] == 1 and met["metrics"][0]["value"] == 2105
    node = graph_query.execute(conn, "by_entity technique PXRD limit=1")
    assert len(node["papers"]) <= 1


def test_execute_cites_of_directions(conn):
    out = graph_query.execute(conn, "cites_of 2 dir=both")
    dirs = {(r["direction"], r["target"]) for r in out["relations"]}
    assert ("out", "paper:3") in dirs and ("in", "paper:1") in dirs
    only_in = graph_query.execute(conn, "cites_of 2 dir=in")
    assert all(r["direction"] == "in" for r in only_in["relations"])


def test_execute_top_joins_titles(conn):
    out = graph_query.execute(conn, "top pagerank limit=5")
    assert out["count"] == 2
    assert out["results"][0]["paper_id"] == "1"
    assert out["results"][0]["title"] == "P1"              # 附带标题便于阅读


def test_query_is_read_only(conn):
    """只读安全：任何算子都不应改变数据。"""
    before = conn.execute("SELECT COUNT(*) c FROM graph_edges").fetchone()["c"]
    for q in ("neighbors 1 depth=2", "path 1 4", "by_entity technique PXRD",
              "by_metric 比表面积", "cites_of 1 dir=both", "top pagerank"):
        graph_query.execute(conn, q)
    after = conn.execute("SELECT COUNT(*) c FROM graph_edges").fetchone()["c"]
    assert before == after


# ---------------------------------------------------------------- 自然语言

def test_translate_nl_accepts_only_whitelisted(conn):
    good = graph_query.translate_nl("和 1529 相关的文献有哪些",
                                    chat=lambda m: "neighbors 1529 depth=2")
    assert good == "neighbors 1529 depth=2"
    # 模型输出 Markdown 代码块也能取出
    assert graph_query.translate_nl("排行",
                                    chat=lambda m: "```\ntop pagerank limit=5\n```") == \
        "top pagerank limit=5"
    # 非白名单 → 拒绝
    with pytest.raises(ValueError):
        graph_query.translate_nl("删掉所有文献", chat=lambda m: "delete from items")
    # 明确不支持
    with pytest.raises(ValueError):
        graph_query.translate_nl("随便", chat=lambda m: "UNSUPPORTED")
    # 模型调用失败 → 明确报错
    def boom(m):
        raise TimeoutError("t")
    with pytest.raises(ValueError):
        graph_query.translate_nl("q", chat=boom)
