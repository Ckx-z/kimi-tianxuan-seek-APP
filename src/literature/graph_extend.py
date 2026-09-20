"""知识图谱扩展（v1.9.7 P3）：实体节点 + 文献↔文献关系。

在既有 NetworkX 侧车图（`graph_ingest`：literature/reaction/monomer/solvent/catalyst/
outcome）之外，向 SQLite 单库的 `graph_nodes` / `graph_edges` / `relations` 写入
**可 SQL 查询的关系层**：

1. **实体节点与边**（本地、零成本）
   - `tech:<canonical>`：表征手段（复用 `knowledge.normalize_technique` 归一化）
   - `metric:<name>`：数值指标（来自 `entry_metrics`，带 value/unit/technique）
   - `prop:<name>`：材料属性（来自 `property` 条目）
   - 边：`uses_technique` / `reports_metric` / `reports_property`
2. **文献↔文献**
   - `cites`：按 DOI 调 **Crossref**（用户已确认）取 `reference[]`，为被引文献建
     `ext:<doi>` 节点（无需本地 PDF），边带 `source=crossref` 与是否本地已有该文献
   - `similar_to`：用已有向量能力（`literature.embedding`）算余弦近邻，边带
     `source=embedding` + `confidence=相似度`（**明确标注是算法推荐，不是真实引用**——
     对应方案里 zotero-refs 的「边按可信度分层」）

设计原则：**幂等**（同一 `(subject,predicate,object)` 不重复）、**可追溯**（每条边带 source）、
**可撤销**（`clear_source()` 按来源批量删除）。Crossref 失败不影响其它边（尽力而为）。
"""

from __future__ import annotations

import json
import logging
import math
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

CROSSREF_API = "https://api.crossref.org/works/"
CROSSREF_TIMEOUT = 20
MAX_REFERENCES = 60          # 单篇最多取多少条参考文献
SIMILAR_TOP_K = 5            # 每篇最多写入多少条相似边
SIMILAR_MIN_SCORE = 0.72     # 相似度阈值（低于此不写边）
SIMILAR_CANDIDATES = 400     # 参与比较的候选文献上限（按入库时间取最近若干）


def paper_node(paper_id: str) -> str:
    return f"paper:{paper_id}"


def external_node(doi: str) -> str:
    return f"ext:{normalize_doi(doi)}"


def normalize_doi(doi: str) -> str:
    d = str(doi or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d


def _lib():
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db


def _knowledge():
    try:
        from literature import knowledge
    except ImportError:  # pragma: no cover
        from src.literature import knowledge  # type: ignore
    return knowledge


def _upsert_node(conn, node_id: str, node_type: str, name: str,
                 meta: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO graph_nodes(node_id,node_type,name,meta_json) VALUES(?,?,?,?)"
        " ON CONFLICT(node_id) DO UPDATE SET node_type=excluded.node_type,"
        " name=excluded.name, meta_json=excluded.meta_json",
        (node_id, node_type, name,
         json.dumps(meta or {}, ensure_ascii=False, default=str)))


def _upsert_edge(conn, subject: str, predicate: str, object_: str, *,
                 source: str, confidence: float | None = None,
                 meta: dict | None = None) -> bool:
    """幂等写边（返回是否新插入）。同时写 `relations` 便于统一查询。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO graph_edges(subject,predicate,object,source,"
        "confidence,meta_json) VALUES(?,?,?,?,?,?)",
        (subject, predicate, object_, source, confidence,
         json.dumps(meta or {}, ensure_ascii=False, default=str)))
    inserted = bool(cur.rowcount)
    conn.execute(
        "INSERT OR IGNORE INTO relations(subject,predicate,object,source,"
        "confidence,meta_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (subject, predicate, object_, source, confidence,
         json.dumps(meta or {}, ensure_ascii=False, default=str),
         _lib()._now()))
    return inserted


# ---------------------------------------------------------------- 1) 实体节点

def sync_entities(conn, paper_id: str) -> dict:
    """从结构化条目抽取实体节点与边（technique / metric / property）。"""
    pid = str(paper_id)
    subject = paper_node(pid)
    row = conn.execute("SELECT id FROM items WHERE paper_id=?", (pid,)).fetchone()
    if row is None:
        return {"paper_id": pid, "error": "文献不存在"}
    item_id = int(row["id"])
    _upsert_node(conn, subject, "paper", pid, {"paper_id": pid})

    n_tech = n_metric = n_prop = 0
    seen_tech: set[str] = set()
    # technique 存在 entries.payload_json 里（表只保留通用列），用 json_extract 取；
    # entry_metrics 另存规范化后的 technique，两者都覆盖到。
    tech_rows = list(conn.execute(
        "SELECT DISTINCT json_extract(payload_json,'$.technique') AS t"
        " FROM entries WHERE item_id=?", (item_id,)))
    tech_rows += list(conn.execute(
        "SELECT DISTINCT technique AS t FROM entry_metrics WHERE item_id=?",
        (item_id,)))
    for r in tech_rows:
        canon = _knowledge().normalize_technique(r["t"] or "")
        if not canon or canon in seen_tech:
            continue
        seen_tech.add(canon)
        node = f"tech:{canon}"
        _upsert_node(conn, node, "technique", canon)
        if _upsert_edge(conn, subject, "uses_technique", node, source="entry"):
            n_tech += 1

    for r in conn.execute(
            "SELECT DISTINCT name, unit FROM entry_metrics WHERE item_id=?", (item_id,)):
        name = (r["name"] or "").strip()
        if not name:
            continue
        node = f"metric:{name}"
        _upsert_node(conn, node, "metric", name, {"unit": r["unit"] or ""})
        if _upsert_edge(conn, subject, "reports_metric", node, source="entry",
                        meta={"unit": r["unit"] or ""}):
            n_metric += 1

    for r in conn.execute(
            "SELECT DISTINCT json_extract(payload_json,'$.property_name') AS pn"
            " FROM entries WHERE item_id=? AND kind='property'", (item_id,)):
        name = (r["pn"] or "").strip()
        if not name:
            continue
        node = f"prop:{name}"
        _upsert_node(conn, node, "property", name)
        if _upsert_edge(conn, subject, "reports_property", node, source="entry"):
            n_prop += 1

    conn.commit()
    return {"paper_id": pid, "techniques": n_tech, "metrics": n_metric,
            "properties": n_prop}


# ---------------------------------------------------------------- 2) cites（Crossref）

def fetch_crossref_references(doi: str, *, timeout: int = CROSSREF_TIMEOUT) -> list[dict]:
    """按 DOI 取 Crossref `reference[]`（网络失败返回空列表，不抛）。"""
    d = normalize_doi(doi)
    if not d:
        return []
    url = CROSSREF_API + urllib.parse.quote(d) + "?mailto=cof-assistant@example.com"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "cof-assistant/1.0 (mailto:cof-assistant@example.com)",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.info("Crossref 取参考文献失败 %s: %s", d, exc)
        return []
    refs = ((data or {}).get("message") or {}).get("reference") or []
    out = []
    for r in refs[:MAX_REFERENCES]:
        if isinstance(r, dict) and r.get("DOI"):
            out.append({"doi": normalize_doi(r["DOI"]),
                        "title": str(r.get("article-title") or r.get("volume-title") or ""),
                        "year": r.get("year")})
    return out


def sync_citations(conn, paper_id: str, doi: str, *,
                   refs: list[dict] | None = None, fetch: bool = True) -> dict:
    """写入 `cites` 边：本地文献 → 被引文献（可能不在本地库，用 `ext:<doi>` 节点）。"""
    pid = str(paper_id)
    subject = paper_node(pid)
    if refs is None:
        refs = fetch_crossref_references(doi) if (fetch and doi) else []
    n_new = n_dup = n_local = 0
    for r in refs:
        d = normalize_doi(r.get("doi") or "")
        if not d:
            continue
        local = conn.execute("SELECT paper_id FROM items WHERE doi=?", (d,)).fetchone()
        if local is not None:
            obj = paper_node(str(local["paper_id"]))
            n_local += 1
        else:
            obj = external_node(d)
            _upsert_node(conn, obj, "paper", r.get("title") or d,
                         {"doi": d, "year": r.get("year"), "external": True})
        if _upsert_edge(conn, subject, "cites", obj, source="crossref",
                        confidence=1.0, meta={"doi": d, "year": r.get("year")}):
            n_new += 1
        else:
            n_dup += 1
    conn.commit()
    return {"paper_id": pid, "references": len(refs), "new_edges": n_new,
            "duplicate_edges": n_dup, "resolved_local": n_local}


# ---------------------------------------------------------------- 3) similar_to（向量）

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed(texts: list[str], embedder=None):
    """取向量（可注入 embedder 便于测试）；不可用时返回 None。"""
    if embedder is not None:
        return embedder(texts)
    try:
        try:
            from literature import embedding
        except ImportError:
            from src.literature import embedding  # type: ignore
        return embedding.embed_texts(texts)
    except Exception as exc:
        logger.info("向量不可用（跳过相似边）: %s", exc)
        return None


def sync_similar(conn, *, embedder=None, top_k: int = SIMILAR_TOP_K,
                 min_score: float = SIMILAR_MIN_SCORE,
                 candidates: int = SIMILAR_CANDIDATES,
                 paper_id: str | None = None) -> dict:
    """写入 `similar_to` 边（**算法推荐**：source=embedding + confidence=相似度）。"""
    if paper_id:
        rows = conn.execute(
            "SELECT paper_id,title,abstract FROM items WHERE paper_id=? AND deleted_at IS NULL",
            (str(paper_id),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT paper_id,title,abstract FROM items WHERE deleted_at IS NULL"
            " ORDER BY added_at DESC LIMIT ?", (max(2, int(candidates)),)).fetchall()
    docs = [(str(r["paper_id"]), f"{r['title'] or ''}。{r['abstract'] or ''}".strip())
            for r in rows]
    docs = [(pid, text) for pid, text in docs if text]
    if len(docs) < 2:
        return {"papers": len(docs), "edges": 0, "reason": "样本不足"}

    vecs = _embed([t for _pid, t in docs], embedder=embedder)
    if not vecs or len(vecs) != len(docs):
        return {"papers": len(docs), "edges": 0, "reason": "向量不可用"}

    n_new = 0
    for i, (pid, _t) in enumerate(docs):
        scored = []
        for j, (oid, _t2) in enumerate(docs):
            if i == j:
                continue
            s = _cosine(vecs[i], vecs[j])
            if s >= min_score:
                scored.append((s, oid))
        scored.sort(reverse=True)
        for s, oid in scored[:top_k]:
            if _upsert_edge(conn, paper_node(pid), "similar_to", paper_node(oid),
                            source="embedding", confidence=round(s, 4)):
                n_new += 1
    conn.commit()
    return {"papers": len(docs), "edges": n_new}


# ---------------------------------------------------------------- 查询 / 维护

def neighbors(conn, paper_id: str, *, limit: int = 30) -> dict:
    """某文献的关联（引用 / 被引 / 相似），供 UI「关联」面板使用。"""
    node = paper_node(paper_id)
    out = {"paper_id": str(paper_id), "cites": [], "cited_by": [], "similar": []}
    for r in conn.execute(
            "SELECT e.object AS node, e.source, e.confidence, n.name, n.meta_json"
            " FROM graph_edges e LEFT JOIN graph_nodes n ON n.node_id=e.object"
            " WHERE e.subject=? AND e.predicate='cites' LIMIT ?", (node, limit)):
        meta = json.loads(r["meta_json"] or "{}")
        out["cites"].append({"node": r["node"], "title": r["name"] or "",
                             "doi": meta.get("doi") or "",
                             "external": bool(meta.get("external")),
                             "source": r["source"], "confidence": r["confidence"]})
    for r in conn.execute(
            "SELECT e.subject AS node, e.source, e.confidence, n.name"
            " FROM graph_edges e LEFT JOIN graph_nodes n ON n.node_id=e.subject"
            " WHERE e.object=? AND e.predicate='cites' LIMIT ?", (node, limit)):
        out["cited_by"].append({"node": r["node"], "title": r["name"] or "",
                                "source": r["source"]})
    for r in conn.execute(
            "SELECT e.object AS node, e.confidence, n.name"
            " FROM graph_edges e LEFT JOIN graph_nodes n ON n.node_id=e.object"
            " WHERE e.subject=? AND e.predicate='similar_to'"
            " ORDER BY e.confidence DESC LIMIT ?", (node, limit)):
        out["similar"].append({"node": r["node"], "title": r["name"] or "",
                               "score": r["confidence"], "source": "embedding"})
    return out


def clear_source(conn, source: str) -> int:
    """按来源撤销（如撤掉全部 crossref 边或 embedding 边）。"""
    cur = conn.execute("DELETE FROM graph_edges WHERE source=?", (source,))
    conn.execute("DELETE FROM relations WHERE source=?", (source,))
    conn.commit()
    return int(cur.rowcount or 0)


def graph_stats(conn) -> dict:
    def one(sql: str, params: tuple = ()) -> int:
        return int(conn.execute(sql, params).fetchone()[0])
    return {
        "nodes": one("SELECT COUNT(*) FROM graph_nodes"),
        "edges": one("SELECT COUNT(*) FROM graph_edges"),
        "papers": one("SELECT COUNT(*) FROM graph_nodes WHERE node_type='paper'"),
        "techniques": one("SELECT COUNT(*) FROM graph_nodes WHERE node_type='technique'"),
        "metrics": one("SELECT COUNT(*) FROM graph_nodes WHERE node_type='metric'"),
        "by_predicate": {
            r["predicate"]: r["c"] for r in conn.execute(
                "SELECT predicate, COUNT(*) c FROM graph_edges GROUP BY predicate")},
    }
