"""混合检索（v1.9.8 C1）：RRF 融合 vector 与 graph 两路召回。

## 为什么做这个

此前文献检索是「启发式拼接」：FTS5 命中 + 图命中各查各的，没有统一的排序与来源说明。
调研指出 Zotero 生态里的 lit-monitor 用 **`--rag-mode vector/graph/hybrid` 三态 + RRF**，
这是我们相对薄弱的点（对方强在检索架构与引用图分析）。本模块把这一课补上：

- **vector 路**：复用既有 `literature.embedding.search`（条目级 bge-m3 / 在线向量）；
- **graph 路**：从 `graph_edges`/`graph_nodes` 出发 —— 命中的实体（技术/指标/属性）
  与其关联文献，加上 `cites`/`similar_to` 边的邻居；
- **RRF 融合**：`score(d) = Σ_r w_r / (k + rank_r(d))`（默认 k=60），
  **每篇文献保留各路贡献与名次**（来源可解释，对应方案里「命中带来源标注」）。

设计原则：**任一路不可用都不影响另一路**（向量模型缺失时自动降级为 graph-only，
并在返回里注明 `degraded`），绝不因单一依赖失败而让检索整体失效。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

RRF_K = 60                 # RRF 平滑常数（论文与主流实现均取 60）
MODES = ("vector", "graph", "hybrid")
ENTITY_TYPES = ("technique", "metric", "property")


def _lib():
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db


def rrf_fuse(rankings: list[list[str]], *, k: int = RRF_K,
             weights: list[float] | None = None,
             sources: list[str] | None = None) -> list[dict]:
    """倒数排名融合。

    rankings: 每路的 paper_id 有序列表（越靠前越相关）
    返回 [{paper_id, score, ranks: {source: rank}, contributions: {source: 1/(k+rank)}}]
    按 score 降序。
    """
    if not rankings:
        return []
    w = list(weights or [1.0] * len(rankings))
    names = list(sources or [f"route{i}" for i in range(len(rankings))])
    acc: dict[str, dict] = {}
    for route_idx, ids in enumerate(rankings):
        for rank, pid in enumerate(ids, start=1):
            if not pid:
                continue
            entry = acc.setdefault(str(pid), {"paper_id": str(pid), "score": 0.0,
                                              "ranks": {}, "contributions": {}})
            contrib = w[route_idx] / (k + rank)
            entry["score"] += contrib
            entry["ranks"][names[route_idx]] = rank
            entry["contributions"][names[route_idx]] = round(contrib, 6)
    out = sorted(acc.values(), key=lambda x: (-x["score"], x["paper_id"]))
    for e in out:
        e["score"] = round(e["score"], 6)
    return out


# ---------------------------------------------------------------- 两路召回

def vector_route(conn, query: str, *, top_k: int = 30,
                 embedder=None) -> tuple[list[str], str]:
    """向量路：条目级向量检索 → 去重后的 paper_id 列表（按相似度）。

    返回 (ids, note)。向量不可用时返回 ([], 原因)。
    """
    if embedder is None:
        try:
            try:
                from literature import embedding
            except ImportError:
                from src.literature import embedding  # type: ignore
            hits = embedding.search(query, top_k=top_k * 3)
        except Exception as exc:  # noqa: BLE001
            return [], f"向量检索不可用：{type(exc).__name__}: {exc}"
    else:
        try:
            hits = embedder(query, top_k * 3)
        except Exception as exc:  # noqa: BLE001
            return [], f"向量检索失败：{type(exc).__name__}: {exc}"

    ids: list[str] = []
    for h in hits or []:
        pid = str((h or {}).get("paper_id") or "")
        if pid and pid not in ids:
            ids.append(pid)
        if len(ids) >= top_k:
            break
    return ids, ""


def graph_route(conn, query: str, *, top_k: int = 30) -> tuple[list[str], str]:
    """图路：① 实体名匹配（技术/指标/属性）→ 关联文献；② 文献节点标题/摘要匹配。

    实体命中的文献优先（实体是领域知识，比字面匹配更可信）。
    """
    q = (query or "").strip()
    if not q:
        return [], "空查询"
    ids: list[str] = []
    try:
        # ① 实体 → 文献（按实体名 LIKE 匹配）
        rows = conn.execute(
            "SELECT node_id, node_type, name FROM graph_nodes"
            " WHERE node_type IN ('technique','metric','property') AND name LIKE ?"
            " LIMIT 40", (f"%{q}%",)).fetchall()
        nodes = [r["node_id"] for r in rows]
        for node in nodes:
            for r in conn.execute(
                    "SELECT DISTINCT subject FROM graph_edges WHERE object=?"
                    " AND subject LIKE 'paper:%' LIMIT 20", (node,)):
                pid = str(r["subject"]).split(":", 1)[1]
                if pid not in ids:
                    ids.append(pid)
            if len(ids) >= top_k:
                break
        if len(ids) < top_k:
            # ② 图上相似/引用邻居（把实体命中文献的邻居也带上，弱相关但有用）
            seed = list(ids)
            for pid in seed[:5]:
                node = f"paper:{pid}"
                for r in conn.execute(
                        "SELECT object FROM graph_edges WHERE subject=? AND predicate='similar_to'"
                        " ORDER BY confidence DESC LIMIT 5", (node,)):
                    o = str(r["object"])
                    if o.startswith("paper:"):
                        cand = o.split(":", 1)[1]
                        if cand not in ids:
                            ids.append(cand)
        if len(ids) < top_k:
            # ③ 字面兜底：文献标题/摘要（FTS 不可用时也能给结果）
            for r in conn.execute(
                    "SELECT paper_id FROM items WHERE deleted_at IS NULL AND"
                    " (title LIKE ? OR abstract LIKE ?) LIMIT ?",
                    (f"%{q}%", f"%{q}%", top_k)):
                pid = str(r["paper_id"])
                if pid not in ids:
                    ids.append(pid)
    except Exception as exc:  # noqa: BLE001
        return ids, f"图检索部分失败：{type(exc).__name__}: {exc}"
    return ids[:top_k], ""


# ---------------------------------------------------------------- 对外入口

def hybrid_search(conn, query: str, *, mode: str = "hybrid", top_k: int = 10,
                  embedder=None, k: int = RRF_K,
                  vector_weight: float = 1.0,
                  graph_weight: float = 1.0) -> dict:
    """三态检索（vector / graph / hybrid），返回带**来源标注**的结果。

    返回 {mode, degraded, notes, results:[{paper_id,title,score,ranks,contributions,sources}]}
    """
    m = (mode or "hybrid").lower()
    if m not in MODES:
        raise ValueError(f"mode 必须是 {list(MODES)} 之一")
    q = (query or "").strip()
    if not q:
        return {"mode": m, "query": q, "degraded": False, "notes": [],
                "results": [], "total": 0}

    rankings: list[list[str]] = []
    weights: list[float] = []
    names: list[str] = []
    notes: list[str] = []

    if m in ("vector", "hybrid"):
        ids, note = vector_route(conn, q, top_k=top_k * 3, embedder=embedder)
        if note:
            notes.append(note)
        if ids:
            rankings.append(ids)
            weights.append(vector_weight)
            names.append("vector")
    if m in ("graph", "hybrid"):
        ids, note = graph_route(conn, q, top_k=top_k * 3)
        if note:
            notes.append(note)
        if ids:
            rankings.append(ids)
            weights.append(graph_weight)
            names.append("graph")

    fused = rrf_fuse(rankings, k=k, weights=weights, sources=names)
    degraded = (m == "hybrid" and len(rankings) < 2)

    results = []
    for e in fused[:max(1, int(top_k))]:
        row = conn.execute(
            "SELECT title,journal,year,doi FROM items WHERE paper_id=?",
            (e["paper_id"],)).fetchone()
        results.append({
            **e,
            "title": (row["title"] if row else "") or "",
            "journal": (row["journal"] if row else "") or "",
            "year": (row["year"] if row else None),
            "doi": (row["doi"] if row else "") or "",
            "sources": sorted(e["ranks"].keys()),
        })
    return {"mode": m, "query": q, "degraded": degraded, "notes": notes,
            "routes": names, "results": results, "total": len(fused)}
