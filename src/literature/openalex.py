"""文献元数据与被引补全（v1.9.8 C3）：OpenAlex / Semantic Scholar。

## 背景与目标

调研显示 Zotero 生态（zotero-refs 的多源融合、zotero-mcp 的 OpenAlex 引用）在
**外部元数据补全与全局被引指标**上比我们强；实测我们的真实数据里 `year` 几乎全空、
`cites` 只覆盖本地视角。本模块用两个**免费、无需 API key** 的服务补上：

| 源 | 端点 | 取什么 |
|---|---|---|
| **OpenAlex** | `https://api.openalex.org/works/doi:{doi}` | 被引量 `cited_by_count`、`publication_year`、`referenced_works` 数、标题/期刊 |
| **Semantic Scholar** | `https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}` | `citationCount` / `referenceCount` / `year`（作交叉校验与补漏） |

设计原则（沿用 zotero-refs 的经验）：
- **逐字段标注来源**：写入时记录哪个字段来自哪个源（`source` 落在 `graph_metrics`）；
- **只补空、不覆盖**：已有 year/journal 不覆盖（与解析回填同一语义）；
- **失败即跳过并如实记录**，绝不让单个源的限速/故障影响整批；
- **带节流**（默认 1 秒/篇，尊重免费额度）与**幂等**（已补全过的文献默认跳过）。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org/works/doi:"
SEMSCHOLAR = "https://api.semanticscholar.org/graph/v1/paper/DOI:"
MAILTO = "cof-assistant@example.com"      # OpenAlex 的 polite pool
TIMEOUT = 25
DEFAULT_DELAY = 1.0                       # 免费额度节流（秒/篇）


def _lib():
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db


def _http_json(url: str, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"cof-assistant/1.0 (mailto:{MAILTO})",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- 两个源

def fetch_openalex(doi: str, *, http=None) -> dict:
    """取 OpenAlex 记录（失败返回 {"error": ...}，不抛）。"""
    d = str(doi or "").strip().lower()
    if not d:
        return {"error": "无 DOI"}
    http = http or _http_json
    try:
        data = http(OPENALEX + urllib.parse.quote(d) + f"?mailto={MAILTO}")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(data, dict) or data.get("error"):
        return {"error": "OpenAlex 未命中或返回异常"}
    host = (data.get("primary_location") or {}).get("source") or {}
    return {
        "source": "openalex",
        "doi": d,
        "title": str(data.get("title") or data.get("display_name") or ""),
        "year": data.get("publication_year"),
        "journal": str(host.get("display_name") or ""),
        "cited_by_count": data.get("cited_by_count"),
        "references": len(data.get("referenced_works") or []),
    }


def fetch_s2(doi: str, *, http=None) -> dict:
    """取 Semantic Scholar 记录（失败返回 {"error": ...}，不抛）。"""
    d = str(doi or "").strip().lower()
    if not d:
        return {"error": "无 DOI"}
    http = http or _http_json
    fields = "title,year,citationCount,referenceCount,venue,externalIds"
    try:
        data = http(SEMSCHOLAR + urllib.parse.quote(d) + f"?fields={fields}")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(data, dict) or data.get("error"):
        return {"error": "Semantic Scholar 未命中或返回异常"}
    return {
        "source": "semantic_scholar",
        "doi": d,
        "title": str(data.get("title") or ""),
        "year": data.get("year"),
        "journal": str(data.get("venue") or ""),
        "cited_by_count": data.get("citationCount"),
        "references": data.get("referenceCount"),
    }


FETCHERS = {"openalex": fetch_openalex, "semantic_scholar": fetch_s2}


# ---------------------------------------------------------------- 落库

def _ensure_metrics(conn) -> None:
    """确保 graph_metrics 表存在（由 C2 的 graph_analytics 定义，两个模块共用）。"""
    try:
        try:
            from literature import graph_analytics
        except ImportError:
            from src.literature import graph_analytics  # type: ignore
        graph_analytics.ensure_schema(conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("初始化 graph_metrics 失败：%s", exc)


def _record_metric(conn, paper_id: str, metric: str, value: float,
                   source: str) -> None:
    _ensure_metrics(conn)
    conn.execute(
        "INSERT OR REPLACE INTO graph_metrics(node_id,metric,value,rank,computed_at)"
        " VALUES(?,?,?,?,?)",
        (f"paper:{paper_id}", metric, float(value), None,
         f"{_lib()._now()}|{source}"))


def enrich_paper(conn, paper_id: str, *, sources: tuple[str, ...] =
                 ("openalex", "semantic_scholar"), fill_year: bool = True,
                 fill_journal: bool = True, force: bool = False,
                 fetchers: dict | None = None) -> dict:
    """按 DOI 补全单篇文献：被引量/参考文献数（指标）+ **只补空**的 year/journal。"""
    pid = str(paper_id)
    _ensure_metrics(conn)
    row = conn.execute("SELECT doi,year,journal,title FROM items WHERE paper_id=?",
                       (pid,)).fetchone()
    if row is None:
        return {"paper_id": pid, "error": "文献不存在"}
    doi = str(row["doi"] or "").strip()
    if not doi:
        return {"paper_id": pid, "skipped": "无 DOI"}

    if not force:
        done = conn.execute(
            "SELECT 1 FROM graph_metrics WHERE node_id=? AND metric='cited_by_count'",
            (f"paper:{pid}",)).fetchone()
        if done is not None:
            return {"paper_id": pid, "skipped": "已补全过（force=true 可重算）"}

    f = {**FETCHERS, **(fetchers or {})}
    results: dict[str, dict] = {}
    updated: dict[str, str] = {}
    metrics: dict[str, float] = {}
    errors: list[str] = []

    for name in sources:
        fn = f.get(name)
        if fn is None:
            continue
        got = fn(doi)
        results[name] = got
        if got.get("error"):
            errors.append(f"{name}: {got['error']}")
            continue
        if isinstance(got.get("cited_by_count"), int):
            metrics["cited_by_count"] = float(got["cited_by_count"])
            _record_metric(conn, pid, "cited_by_count", got["cited_by_count"], name)
        if isinstance(got.get("references"), int):
            metrics["reference_count"] = float(got["references"])
            _record_metric(conn, pid, "reference_count", got["references"], name)
        if fill_year and not row["year"] and isinstance(got.get("year"), int):
            conn.execute("UPDATE items SET year=?, updated_at=? WHERE paper_id=?",
                         (got["year"], _lib()._now(), pid))
            updated["year"] = f"{got['year']}（{name}）"
        if fill_journal and not (row["journal"] or "").strip() and got.get("journal"):
            conn.execute("UPDATE items SET journal=?, updated_at=? WHERE paper_id=?",
                         (got["journal"], _lib()._now(), pid))
            updated["journal"] = f"{got['journal']}（{name}）"
    conn.commit()
    return {"paper_id": pid, "doi": doi, "sources": list(results.keys()),
            "metrics": metrics, "updated": updated, "errors": errors}


def enrich_batch(conn, *, limit: int = 20, only_missing_year: bool = True,
                 sources: tuple[str, ...] = ("openalex",),
                 delay: float = DEFAULT_DELAY, fetchers: dict | None = None,
                 sleep=None) -> dict:
    """批量补全（默认只处理**缺 year** 的文献；带节流）。

    真实网络下请保持 delay≥1s（免费额度礼貌使用）；测试注入 fetchers + sleep。
    """
    sleep = sleep or time.sleep
    where = ["deleted_at IS NULL", "doi IS NOT NULL", "doi != ''"]
    if only_missing_year:
        where.append("year IS NULL")
    rows = conn.execute(
        f"SELECT paper_id FROM items WHERE {' AND '.join(where)}"
        f" ORDER BY CAST(paper_id AS INTEGER) DESC LIMIT ?",
        (max(1, int(limit)),)).fetchall()
    out = {"targets": len(rows), "enriched": 0, "skipped": 0, "errors": 0,
           "details": []}
    for i, r in enumerate(rows):
        res = enrich_paper(conn, str(r["paper_id"]), sources=sources,
                           fetchers=fetchers)
        out["details"].append({k: res.get(k) for k in
                               ("paper_id", "skipped", "updated", "metrics")})
        if res.get("skipped"):
            out["skipped"] += 1
        elif res.get("error"):
            out["errors"] += 1
        else:
            out["enriched"] += 1
        if delay and i < len(rows) - 1:
            sleep(delay)
    return out


def status(conn) -> dict:
    """补全情况总览（多少篇有 DOI / 有被引量 / 缺 year）。"""
    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])
    return {
        "papers": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL"),
        "with_doi": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL"
                        " AND doi IS NOT NULL AND doi != ''"),
        "missing_year": one("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL"
                            " AND year IS NULL"),
        "enriched_cited_by": one("SELECT COUNT(DISTINCT node_id) FROM graph_metrics"
                                 " WHERE metric='cited_by_count'"),
        "top_cited": [{"paper_id": r["node_id"].split(":", 1)[1],
                       "cited_by": r["value"]}
                      for r in conn.execute(
                          "SELECT node_id,value FROM graph_metrics"
                          " WHERE metric='cited_by_count'"
                          " ORDER BY value DESC LIMIT 10")],
    }
