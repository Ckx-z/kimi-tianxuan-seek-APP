"""文献库 → GNN 反馈队列的批量桥接（v1.9.7 P4）。

把「文献里的成膜正样本」批量推入既有 GNN 反馈队列（`src/predictor/gnn_feedback`），
解决两个既有短板：① 以前只能逐条点「GNN反馈」；② 反馈与文献/条目**无溯源关系**。

能力：
- `collect_candidates`：按集合/标签筛出 `film_outcome` 条目（含醛/胺 SMILES、
  film_label、evidence、来源），**只取能成网的组合**（交给队列自身校验）；
- `enqueue`：批量 `submit(source='literature_pdf', refs=[paper:…, entry:…, doi:…])`，
  **按 entry_id 去重**（同一结论文献条目只入队一次），并记录权重分层
  （用户确认过的条目权重高、LLM 未复核的权重低 → 供重训时 `feedback_pos_w` 参考）；
- `links_for_paper` / `mark_used`：溯源查询与「已用于 GNN 第 N 版」标记（供 UI 徽标）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 权重分层（借鉴方案：用户确认过的正样本更可信）
WEIGHT_USER = 2.0        # source=user / 已人工确认
WEIGHT_LLM = 1.0         # LLM 提取、未复核

LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS gnn_feedback_links (
  feedback_id TEXT PRIMARY KEY,
  entry_id TEXT, paper_id TEXT,
  label REAL, weight REAL, source TEXT,
  created_at TEXT, used_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_gfl_paper ON gnn_feedback_links(paper_id);
CREATE INDEX IF NOT EXISTS idx_gfl_entry ON gnn_feedback_links(entry_id);
"""


def ensure_schema(conn) -> None:
    conn.executescript(LINKS_TABLE)
    conn.commit()


def _feedback():
    try:
        from src.predictor import gnn_feedback
    except ImportError:  # pragma: no cover
        from predictor import gnn_feedback  # type: ignore
    return gnn_feedback


def _now():
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db._now()


def collect_candidates(conn, *, collection_id: str | None = None,
                       tag: str | None = None, paper_id: str | None = None,
                       limit: int = 500, skip_enqueued: bool = True) -> list[dict]:
    """筛出可入队的文献正样本（film_outcome 且 label=1.0，含醛/胺 SMILES）。"""
    ensure_schema(conn)
    where = ["e.kind='film_outcome'", "e.paper_id IS NOT NULL"]
    params: list = []
    if paper_id:
        where.append("e.paper_id=?")
        params.append(str(paper_id))
    if collection_id:
        where.append("EXISTS(SELECT 1 FROM collection_items ci WHERE ci.paper_id=e.paper_id"
                     " AND ci.collection_id=?)")
        params.append(collection_id)
    if tag:
        where.append("EXISTS(SELECT 1 FROM item_tags it WHERE it.paper_id=e.paper_id"
                     " AND it.tag=?)")
        params.append(tag)
    rows = conn.execute(
        f"SELECT e.entry_id, e.paper_id, e.payload_json, e.source, e.evidence, i.doi"
        f" FROM entries e LEFT JOIN items i ON i.paper_id=e.paper_id"
        f" WHERE {' AND '.join(where)} LIMIT ?", [*params, max(1, int(limit))]).fetchall()
    out: list[dict] = []
    enqueued = {r["entry_id"] for r in conn.execute(
        "SELECT entry_id FROM gnn_feedback_links")}
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            continue
        try:
            label = float(payload.get("film_label"))
        except (TypeError, ValueError):
            continue
        if label != 1.0:                       # 只推「成膜」正样本
            continue
        ald = str(payload.get("ald_smiles") or "").strip()
        amine = str(payload.get("amine_smiles") or "").strip()
        if not ald or not amine:
            continue
        if skip_enqueued and r["entry_id"] in enqueued:
            continue
        out.append({
            "entry_id": r["entry_id"], "paper_id": str(r["paper_id"]),
            "ald_smiles": ald, "amine_smiles": amine, "label": label,
            "evidence": (r["evidence"] or "")[:200],
            "doi": str(r["doi"] or ""),
            "weight": WEIGHT_USER if (r["source"] or "") == "user" else WEIGHT_LLM,
            "confirmed": (r["source"] or "") == "user",
            "already_enqueued": r["entry_id"] in enqueued,
        })
    return out


def enqueue(conn, *, collection_id: str | None = None, tag: str | None = None,
            paper_id: str | None = None, limit: int = 200,
            dry_run: bool = False) -> dict:
    """批量提交到 GNN 反馈队列（幂等：同一 entry_id 只入队一次）。"""
    cands = collect_candidates(conn, collection_id=collection_id, tag=tag,
                               paper_id=paper_id, limit=limit)
    if dry_run:
        return {"dry_run": True, "candidates": len(cands),
                "weights": {"user": sum(1 for c in cands if c["confirmed"]),
                            "llm": sum(1 for c in cands if not c["confirmed"])},
                "sample": cands[:5]}
    fb = _feedback()
    submitted = skipped = failed = 0
    ids: list[str] = []
    for c in cands:
        note = f"文献正样本 #{c['paper_id']}（{c['evidence'][:80]}）"
        refs = [f"paper:{c['paper_id']}", f"entry:{c['entry_id']}"]
        if c["doi"]:
            refs.append(f"doi:{c['doi']}")
        try:
            rec = fb.submit(c["ald_smiles"], c["amine_smiles"], c["label"],
                            note=note, source="literature_pdf", refs=refs)
        except ValueError as exc:              # 例如不能成网/标签非法
            skipped += 1
            logger.info("跳过 %s: %s", c["entry_id"], exc)
            continue
        except Exception as exc:               # pragma: no cover
            failed += 1
            logger.warning("提交失败 %s: %s", c["entry_id"], exc)
            continue
        conn.execute(
            "INSERT OR REPLACE INTO gnn_feedback_links(feedback_id,entry_id,paper_id,"
            "label,weight,source,created_at,used_version) VALUES(?,?,?,?,?,?,?,NULL)",
            (rec["feedback_id"], c["entry_id"], c["paper_id"], c["label"], c["weight"],
             "user" if c["confirmed"] else "llm", _now()))
        ids.append(rec["feedback_id"])
        submitted += 1
    conn.commit()
    return {"submitted": submitted, "skipped": skipped, "failed": failed,
            "feedback_ids": ids, "candidates": len(cands)}


def links_for_paper(conn, paper_id: str) -> list[dict]:
    """该文献已入队的反馈（供 UI 徽标 / 溯源）。"""
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT feedback_id,entry_id,paper_id,label,weight,source,created_at,used_version"
        " FROM gnn_feedback_links WHERE paper_id=? ORDER BY created_at DESC",
        (str(paper_id),))]


def mark_used(conn, version: str, feedback_ids: list[str] | None = None) -> int:
    """标记这些反馈已用于某个 GNN 版本（`None` = 全部未标记的）。"""
    ensure_schema(conn)
    if feedback_ids:
        marks = ",".join("?" * len(feedback_ids))
        cur = conn.execute(
            f"UPDATE gnn_feedback_links SET used_version=? WHERE feedback_id IN ({marks})",
            [str(version), *feedback_ids])
    else:
        cur = conn.execute(
            "UPDATE gnn_feedback_links SET used_version=? WHERE used_version IS NULL",
            (str(version),))
    conn.commit()
    return int(cur.rowcount or 0)


def summary(conn) -> dict:
    """总览：已入队条数、权重分布、按版本分布、涉及文献数。"""
    ensure_schema(conn)
    rows = list(conn.execute(
        "SELECT weight, used_version, COUNT(*) c FROM gnn_feedback_links"
        " GROUP BY weight, used_version"))
    # 注意：按 (weight, version) 分组后**必须累加**到各自的字典键上，
    # 直接用字典推导会让同版本不同权重的计数互相覆盖（实测少算一半）。
    by_weight: dict[str, int] = {}
    by_version: dict[str, int] = {}
    for r in rows:
        by_weight[str(r["weight"])] = by_weight.get(str(r["weight"]), 0) + int(r["c"])
        key = r["used_version"] or "未使用"
        by_version[key] = by_version.get(key, 0) + int(r["c"])
    return {
        "links": sum(int(r["c"]) for r in rows),
        "papers": int(conn.execute(
            "SELECT COUNT(DISTINCT paper_id) c FROM gnn_feedback_links").fetchone()["c"]),
        "by_weight": by_weight,
        "by_version": by_version,
    }
