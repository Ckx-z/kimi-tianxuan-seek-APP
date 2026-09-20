"""C2 引用图分析测试：PageRank / 社区发现 / 桥梁文献 / 时间线 / 上限保护 / 缓存。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import graph_analytics, graph_extend, library_db  # noqa: E402

PAPERS = {
    "1": {"title": "Hub paper", "year": 2026, "doi": "10.1/a", "source": "builtin"},
    "2": {"title": "Cited A", "year": 2025, "doi": "10.1/b", "source": "builtin"},
    "3": {"title": "Cited B", "year": 2025, "doi": "10.1/c", "source": "builtin"},
    "4": {"title": "Cited C", "year": 2024, "doi": "10.1/d", "source": "builtin"},
    "5": {"title": "Isolated", "year": 2024, "doi": "10.1/e", "source": "builtin"},
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, res = tmp_path / "data", tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
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
    # 构造：1→2/3/4 引用；2→3；2、3→4（**4 被 1/2/3 三篇引用 → 被引最多**）；5 孤立
    for obj in ("paper:2", "paper:3", "paper:4"):
        graph_extend._upsert_edge(c, "paper:1", "cites", obj, source="crossref",
                                  confidence=1.0)
    graph_extend._upsert_edge(c, "paper:2", "cites", "paper:3", source="crossref")
    graph_extend._upsert_edge(c, "paper:2", "cites", "paper:4", source="crossref")
    graph_extend._upsert_edge(c, "paper:3", "cites", "paper:4", source="crossref")
    graph_extend._upsert_edge(c, "paper:2", "similar_to", "paper:3",
                              source="embedding", confidence=0.9)
    c.commit()
    yield c
    c.close()


def test_pagerank_ranks_most_cited_first(conn):
    """语义：边 引用者→被引者，PageRank 奖励**入边** → 被引最多的文献排第一。"""
    out = graph_analytics.pagerank(conn)
    assert out["cached"] is False and out["nodes"] >= 4
    assert out["results"], "应有结果"
    assert out["results"][0]["paper_id"] == "4"     # 被 1/2/3 引用
    assert out["results"][0]["rank"] == 1
    assert out["results"][0]["score"] > 0
    # 落库到 graph_metrics（供 UI / 后续复用）
    row = conn.execute("SELECT value FROM graph_metrics WHERE node_id='paper:4'"
                       " AND metric='pagerank'").fetchone()
    assert row is not None and row["value"] > 0


def test_pagerank_cache_and_force(conn):
    first = graph_analytics.pagerank(conn)
    assert first["cached"] is False
    again = graph_analytics.pagerank(conn)          # 命中缓存
    assert again["cached"] is True
    assert [r["paper_id"] for r in again["results"]] == \
           [r["paper_id"] for r in first["results"]]
    forced = graph_analytics.pagerank(conn, force=True)
    assert forced["cached"] is False


def test_communities_found(conn):
    out = graph_analytics.communities(conn)
    assert out["count"] >= 1 and out["communities"]
    sizes = [c["size"] for c in out["communities"]]
    assert max(sizes) >= 3                          # 1,2,3,4 至少构成一个簇
    top = out["communities"][0]
    assert top["papers"] and "paper_id" in top["papers"][0]
    # 社区号写入 graph_metrics
    row = conn.execute("SELECT value FROM graph_metrics WHERE node_id='paper:1'"
                       " AND metric='community'").fetchone()
    assert row is not None


def test_bridges_returns_connecting_papers(conn):
    out = graph_analytics.bridges(conn)
    assert out["results"], "应有桥梁文献"
    ids = [r["paper_id"] for r in out["results"]]
    assert "1" in ids or "2" in ids                 # 连接者
    assert out["approximated_with_k"] >= 1
    assert all("degree" in r for r in out["results"])


def test_timeline_buckets(conn):
    out = graph_analytics.timeline(conn)
    assert out["years"] == 3 and out["total"] == 5
    by_year = {b["year"]: b["count"] for b in out["buckets"]}
    assert by_year == {2026: 1, 2025: 2, 2024: 2}
    assert out["recent"][0]["year"] == 2026


def test_materialize_cap_refuses_large_graph(conn):
    g, info = graph_analytics.materialize(conn, limit=2)     # 故意设很小
    assert g is None and info["capped"] is True
    assert "超过上限" in info["message"]
    # 分析入口同样返回 capped，而不是抛异常/吃内存
    out = graph_analytics.pagerank(conn, force=True)
    assert out.get("capped") is not True              # 默认上限下正常
    g2, info2 = graph_analytics.materialize(conn)
    assert g2 is not None and info2["nodes"] >= 4


def test_empty_graph_message(env):
    """没有任何边时给出可操作提示，而不是空结果。"""
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    c.execute("DELETE FROM graph_edges")
    c.commit()
    out = graph_analytics.pagerank(c, force=True)
    assert out["results"] == [] and "还没有" in out["message"]
    assert graph_analytics.communities(c, force=True)["communities"] == []
    assert graph_analytics.bridges(c, force=True)["results"] == []
    c.close()


def test_summary_shape(conn):
    s = graph_analytics.summary(conn)
    assert set(s.keys()) == {"pagerank", "communities", "bridges", "timeline"}
    assert s["timeline"]["total"] == 5
