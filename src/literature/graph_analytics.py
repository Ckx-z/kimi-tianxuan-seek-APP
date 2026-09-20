"""引用图分析（v1.9.8 C2）：PageRank / 社区发现 / 桥梁文献 / 时间线。

## 为什么做这个

调研显示 Zotero 生态（zotero-mcp + OpenAlex）强在**引用网络的全局分析**，我们此前只有
「一跳邻居 + 相似边」。本模块把这部分补上，并沿用两条从调研里学到的工程约定：

1. **先落库、按上限物化进内存，超限拒绝而不是 OOM**（zotero-mcp 的
   `GRAPH_MATERIALIZATION_LIMIT` 做法）；
2. **结果缓存**（重算代价高）—— 写入 `graph_metrics` 表 + `settings` 时间戳，
   默认 TTL 1 小时，`force=True` 可强制重算。

分析对象：`paper:` 节点之间的 **`cites`（有向）** 与 **`similar_to`（无向，算法推荐）**。
PageRank 走有向引用图；社区/桥梁用无向图（引用+相似合并），并在返回里**注明用了哪些边**，
避免把"算法推荐"当成真实引用。
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

MAX_NODES = 20000          # 物化上限（超过则拒绝，只做统计不做算法）
CACHE_TTL = 3600           # 结果缓存秒数
BETWEENNESS_SAMPLES = 200  # 介数中心性近似采样（k），避免大图算不动

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_metrics (
  node_id TEXT, metric TEXT, value REAL, rank INTEGER, computed_at TEXT,
  PRIMARY KEY (node_id, metric)
);
CREATE INDEX IF NOT EXISTS idx_gm_metric ON graph_metrics(metric);
"""


def _lib():
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _settings_get(conn, key: str) -> dict | None:
    row = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value_json"])
    except Exception:
        return None


def _settings_put(conn, key: str, value: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO settings(key,value_json,updated_at)"
                 " VALUES(?,?,?)", (key, json.dumps(value, ensure_ascii=False),
                                    _lib()._now()))
    conn.commit()


# ---------------------------------------------------------------- 物化

def materialize(conn, *, kinds: tuple[str, ...] = ("cites", "similar_to"),
                limit: int = MAX_NODES, directed: bool = False):
    """把图上 paper↔paper 的边物化成 NetworkX 图（**带上限保护**）。

    返回 (G, info)。超限时返回 (None, {"capped": True, ...})。
    """
    import networkx as nx

    placeholders = ",".join("?" * len(kinds))
    rows = conn.execute(
        f"SELECT subject,predicate,object,confidence FROM graph_edges"
        f" WHERE predicate IN ({placeholders})"
        f" AND subject LIKE 'paper:%' AND object LIKE 'paper:%'",
        list(kinds)).fetchall()
    nodes = set()
    for r in rows:
        nodes.add(r["subject"])
        nodes.add(r["object"])
    if len(nodes) > limit:
        return None, {"capped": True, "nodes": len(nodes), "limit": limit,
                      "message": f"图规模 {len(nodes)} 超过上限 {limit}，已拒绝物化"
                                 f"（避免内存溢出）；可提高上限或缩小范围"}
    g = nx.DiGraph() if directed else nx.Graph()
    for r in rows:
        w = float(r["confidence"] or 1.0)
        g.add_edge(r["subject"], r["object"], weight=w, predicate=r["predicate"])
    return g, {"capped": False, "nodes": g.number_of_nodes(),
               "edges": g.number_of_edges(), "kinds": list(kinds),
               "directed": directed}


def _papers(conn, nodes: list[str]) -> dict[str, dict]:
    ids = [n.split(":", 1)[1] for n in nodes]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT paper_id,title,journal,year FROM items WHERE paper_id IN ({marks})",
        ids).fetchall()
    return {str(r["paper_id"]): {"title": r["title"] or "", "journal": r["journal"] or "",
                                 "year": r["year"]} for r in rows}


def _cache_valid(conn, key: str, ttl: int) -> bool:
    meta = _settings_get(conn, f"analytics_meta:{key}")
    if not meta:
        return False
    return (time.time() - float(meta.get("at") or 0)) < ttl


# ---------------------------------------------------------------- 分析

def pagerank(conn, *, top_k: int = 20, force: bool = False,
             ttl: int = CACHE_TTL) -> dict:
    """有向引用图上的 PageRank（无 cites 边时回退到无向混合图并注明）。"""
    ensure_schema(conn)
    if not force and _cache_valid(conn, "pagerank", ttl):
        cached = _settings_get(conn, "analytics:pagerank")
        if cached:
            return {**cached, "cached": True}
    import networkx as nx

    g, info = materialize(conn, kinds=("cites",), directed=True)
    used = "cites"
    if g is None:
        return {"cached": False, **info}
    if g.number_of_edges() == 0:
        g, info2 = materialize(conn, kinds=("cites", "similar_to"))
        used = "cites+similar_to（无 cites 边，回退为无向混合图）"
        if g is None:
            return {"cached": False, **info2}
    if g.number_of_nodes() == 0:
        return {"cached": False, "nodes": 0, "edges": 0, "results": [],
                "message": "图上还没有文献↔文献的边：请先执行图谱同步（cites/similar_to）"}

    scores = nx.pagerank(g, weight="weight") if g.is_directed() else \
        nx.pagerank(g.to_directed(), weight="weight")
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:max(1, int(top_k))]
    meta = _papers(conn, [n for n, _ in ranked])
    results = []
    for rank, (node, score) in enumerate(ranked, start=1):
        pid = node.split(":", 1)[1]
        results.append({"paper_id": pid, "score": round(float(score), 6), "rank": rank,
                        **meta.get(pid, {})})
        conn.execute("INSERT OR REPLACE INTO graph_metrics(node_id,metric,value,rank,"
                     "computed_at) VALUES(?,?,?,?,?)",
                     (node, "pagerank", float(score), rank, _lib()._now()))
    conn.commit()
    out = {"cached": False, "used_edges": used, "nodes": info.get("nodes"),
           "edges": info.get("edges"), "results": results}
    _settings_put(conn, "analytics:pagerank",
                  {k: v for k, v in out.items() if k != "cached"})
    _settings_put(conn, "analytics_meta:pagerank", {"at": time.time()})
    return out


def communities(conn, *, top_k: int = 10, force: bool = False,
                ttl: int = CACHE_TTL) -> dict:
    """社区发现（贪心模块度，引用+相似合并的无向图）。"""
    ensure_schema(conn)
    if not force and _cache_valid(conn, "communities", ttl):
        cached = _settings_get(conn, "analytics:communities")
        if cached:
            return {**cached, "cached": True}
    from networkx.algorithms.community import greedy_modularity_communities

    g, info = materialize(conn, kinds=("cites", "similar_to"))
    if g is None:
        return {"cached": False, **info}
    if g.number_of_nodes() == 0:
        return {"cached": False, "nodes": 0, "communities": [],
                "message": "图上还没有文献↔文献的边"}

    try:
        comms = list(greedy_modularity_communities(g, weight="weight"))
    except Exception as exc:  # noqa: BLE001
        return {"cached": False, "error": f"社区发现失败：{type(exc).__name__}: {exc}"}
    comms.sort(key=len, reverse=True)
    out_comms = []
    for idx, members in enumerate(comms[:max(1, int(top_k))]):
        nodes = [f"paper:{n.split(':', 1)[1]}" for n in members]
        meta = _papers(conn, nodes)
        members_sorted = sorted(nodes, key=lambda n: n)
        out_comms.append({
            "community_id": idx,
            "size": len(members),
            "papers": [{"paper_id": n.split(":", 1)[1],
                        **meta.get(n.split(":", 1)[1], {})}
                       for n in members_sorted[:10]],
        })
        for n in members_sorted:
            conn.execute("INSERT OR REPLACE INTO graph_metrics(node_id,metric,value,"
                         "rank,computed_at) VALUES(?,?,?,?,?)",
                         (n, "community", float(idx), idx, _lib()._now()))
    conn.commit()
    out = {"cached": False, "nodes": info.get("nodes"), "edges": info.get("edges"),
           "count": len(comms), "communities": out_comms,
           "used_edges": "cites+similar_to（无向合并）"}
    _settings_put(conn, "analytics:communities",
                  {k: v for k, v in out.items() if k != "cached"})
    _settings_put(conn, "analytics_meta:communities", {"at": time.time()})
    return out


def bridges(conn, *, top_k: int = 10, force: bool = False, ttl: int = CACHE_TTL,
            samples: int = BETWEENNESS_SAMPLES) -> dict:
    """桥梁文献：介数中心性（大图用 k 采样近似），跨社区连接者优先。"""
    ensure_schema(conn)
    if not force and _cache_valid(conn, "bridges", ttl):
        cached = _settings_get(conn, "analytics:bridges")
        if cached:
            return {**cached, "cached": True}
    import networkx as nx

    g, info = materialize(conn, kinds=("cites", "similar_to"))
    if g is None:
        return {"cached": False, **info}
    if g.number_of_nodes() == 0:
        return {"cached": False, "nodes": 0, "results": [],
                "message": "图上还没有文献↔文献的边"}
    k = min(samples, g.number_of_nodes())
    try:
        bc = nx.betweenness_centrality(g, k=k, weight="weight", seed=42)
    except Exception as exc:  # noqa: BLE001
        return {"cached": False, "error": f"介数中心性计算失败：{type(exc).__name__}: {exc}"}
    ranked = sorted(bc.items(), key=lambda kv: -kv[1])[:max(1, int(top_k))]
    meta = _papers(conn, [n for n, _ in ranked])
    results = []
    for rank, (node, score) in enumerate(ranked, start=1):
        pid = node.split(":", 1)[1]
        results.append({"paper_id": pid, "score": round(float(score), 6), "rank": rank,
                        "degree": int(g.degree(node)), **meta.get(pid, {})})
        conn.execute("INSERT OR REPLACE INTO graph_metrics(node_id,metric,value,rank,"
                     "computed_at) VALUES(?,?,?,?,?)",
                     (node, "betweenness", float(score), rank, _lib()._now()))
    conn.commit()
    out = {"cached": False, "used_edges": "cites+similar_to（无向）",
           "approximated_with_k": k, "results": results}
    _settings_put(conn, "analytics:bridges",
                  {k2: v for k2, v in out.items() if k2 != "cached"})
    _settings_put(conn, "analytics_meta:bridges", {"at": time.time()})
    return out


def timeline(conn, *, limit: int = 500) -> dict:
    """时间线：按年份统计文献量（来自 items.year，不依赖图）。"""
    rows = conn.execute(
        "SELECT year, COUNT(*) c FROM items WHERE deleted_at IS NULL AND year IS NOT NULL"
        " GROUP BY year ORDER BY year").fetchall()
    buckets = [{"year": int(r["year"]), "count": int(r["c"])} for r in rows]
    recent = conn.execute(
        "SELECT paper_id,title,year FROM items WHERE deleted_at IS NULL"
        " AND year IS NOT NULL ORDER BY year DESC, CAST(paper_id AS INTEGER) DESC"
        " LIMIT ?", (max(1, int(limit)),)).fetchall()
    return {"years": len(buckets), "total": sum(b["count"] for b in buckets),
            "buckets": buckets,
            "recent": [{"paper_id": str(r["paper_id"]), "title": r["title"] or "",
                        "year": r["year"]} for r in recent]}


def summary(conn) -> dict:
    """一次返回全部四项（供 UI 一个请求拿全）。"""
    return {"pagerank": pagerank(conn), "communities": communities(conn),
            "bridges": bridges(conn), "timeline": timeline(conn)}
