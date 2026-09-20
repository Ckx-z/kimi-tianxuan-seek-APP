"""文献阅读模块后端（v1.9.8 R2）。

对标 Zotero：**批注（itemAnnotations）+ 阅读状态 + 阅读清单**，并给阅读器提供
「读完该做什么」的判定：**未入库→全量解析入库；已入库→只补缺失项**。

设计要点
- 批注对齐 Zotero `itemAnnotations`：`type(highlight|note|image)` + 页码 + 归一化坐标
  （`position_json` 存 0~1 相对坐标，缩放/换页后仍能定位）+ 颜色 + 原文选中文字 + 个人评论；
- 阅读状态落在 `items.extra_json.reading_state`（不新增列，避免迁移）；
- `ingest_plan()` 复用 `library_db.search_papers` 已有的三项判定（摘要/条目/图片），
  与列表徽标、R2「补缺解析」共用同一口径；
- 批注可一键导出为笔记（Markdown，含页码与引文），写入 `notes`（source='user'）。
"""

from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger(__name__)

STATES = ("unread", "reading", "read", "ingested")

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_annotations (
  annotation_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL,
  attachment_id TEXT,
  type TEXT NOT NULL,              -- highlight | note | image
  page INTEGER,
  position_json TEXT,              -- 归一化坐标 {x,y,w,h}（0~1）
  color TEXT,
  text TEXT,                       -- 原文选中文字
  comment TEXT,                    -- 个人评论
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_anno_paper ON item_annotations(paper_id);
CREATE INDEX IF NOT EXISTS idx_anno_attach ON item_annotations(attachment_id);
"""


def _db():
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------- 批注 CRUD

def add_annotation(conn, paper_id: str, *, type_: str = "highlight",
                   page: int | None = None, position: dict | None = None,
                   color: str = "", text: str = "", comment: str = "",
                   attachment_id: str | None = None) -> dict:
    ensure_schema(conn)
    if type_ not in ("highlight", "note", "image"):
        raise ValueError("type 必须是 highlight | note | image")
    db = _db()
    anno_id = f"anno_{uuid.uuid4().hex[:12]}"
    now = db._now()
    conn.execute(
        "INSERT INTO item_annotations(annotation_id,paper_id,attachment_id,type,page,"
        "position_json,color,text,comment,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (anno_id, str(paper_id), attachment_id, type_, page,
         json.dumps(position or {}, ensure_ascii=False), color, text, comment,
         now, now))
    conn.commit()
    return {"annotation_id": anno_id, "paper_id": str(paper_id), "type": type_,
            "page": page, "position": position or {}, "color": color,
            "text": text, "comment": comment, "created_at": now}


def list_annotations(conn, paper_id: str | None = None,
                     attachment_id: str | None = None) -> list[dict]:
    ensure_schema(conn)
    where, params = ["1=1"], []
    if paper_id:
        where.append("paper_id=?")
        params.append(str(paper_id))
    if attachment_id:
        where.append("attachment_id=?")
        params.append(attachment_id)
    rows = conn.execute(
        f"SELECT * FROM item_annotations WHERE {' AND '.join(where)}"
        f" ORDER BY page, created_at", params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["position"] = json.loads(d.pop("position_json") or "{}")
        except Exception:
            d["position"] = {}
        out.append(d)
    return out


def update_annotation(conn, annotation_id: str, *, color: str | None = None,
                      comment: str | None = None, page: int | None = None,
                      position: dict | None = None) -> dict | None:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM item_annotations WHERE annotation_id=?",
                       (annotation_id,)).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE item_annotations SET color=COALESCE(?,color),"
        " comment=COALESCE(?,comment), page=COALESCE(?,page),"
        " position_json=COALESCE(?,position_json), updated_at=? WHERE annotation_id=?",
        (color, comment, page,
         json.dumps(position, ensure_ascii=False) if position else None,
         _db()._now(), annotation_id))
    conn.commit()
    return next((a for a in list_annotations(conn) if a["annotation_id"] == annotation_id),
                None)


def delete_annotation(conn, annotation_id: str) -> bool:
    ensure_schema(conn)
    cur = conn.execute("DELETE FROM item_annotations WHERE annotation_id=?",
                       (annotation_id,))
    conn.commit()
    return bool(cur.rowcount)


# ---------------------------------------------------------------- 阅读状态

def get_state(conn, paper_id: str) -> str:
    row = conn.execute("SELECT extra_json FROM items WHERE paper_id=?",
                       (str(paper_id),)).fetchone()
    if row is None:
        return "unread"
    try:
        extra = json.loads(row["extra_json"] or "{}")
    except Exception:
        extra = {}
    state = str(extra.get("reading_state") or "unread")
    return state if state in STATES else "unread"


def set_state(conn, paper_id: str, state: str) -> str:
    if state not in STATES:
        raise ValueError(f"state 必须是 {list(STATES)} 之一")
    row = conn.execute("SELECT extra_json FROM items WHERE paper_id=?",
                       (str(paper_id),)).fetchone()
    if row is None:
        raise ValueError(f"文献不存在: {paper_id}")
    try:
        extra = json.loads(row["extra_json"] or "{}")
    except Exception:
        extra = {}
    extra["reading_state"] = state
    conn.execute("UPDATE items SET extra_json=?, updated_at=? WHERE paper_id=?",
                 (json.dumps(extra, ensure_ascii=False), _db()._now(), str(paper_id)))
    conn.commit()
    return state


# ---------------------------------------------------------------- 阅读清单 / 入库计划

def queue(conn, *, state: str | None = None, limit: int = 100) -> list[dict]:
    """阅读清单：按状态筛选，附解析状态（缺什么）。"""
    ensure_schema(conn)
    rows = []
    for p in _db().search_papers(conn, limit=max(1, min(limit, 500)))["papers"]:
        st = get_state(conn, p["paper_id"])
        if state and st != state:
            continue
        rows.append({**p, "reading_state": st,
                     "annotations": len(list_annotations(conn, p["paper_id"]))})
    return rows


def ingest_plan(conn, paper_id: str) -> dict:
    """读书后该做什么：未入库→全量；已入库→只补缺失项（与列表徽标同口径）。"""
    ensure_schema(conn)
    pid = str(paper_id)
    hit = next((p for p in _db().search_papers(conn, limit=500)["papers"]
                if p["paper_id"] == pid), None)
    if hit is None:
        # 列表只取前 500 篇，未命中时直接查该文献
        row = conn.execute(
            "SELECT COUNT(*) c FROM entries e JOIN items i ON i.id=e.item_id"
            " WHERE i.paper_id=?", (pid,)).fetchone()
        fig = conn.execute(
            "SELECT COUNT(*) c FROM figures f JOIN items i ON i.id=f.item_id"
            " WHERE i.paper_id=?", (pid,)).fetchone()
        ab = conn.execute("SELECT abstract FROM items WHERE paper_id=?", (pid,)).fetchone()
        if ab is None:
            return {"paper_id": pid, "error": "文献不存在"}
        entries_n, figures_n = int(row["c"]), int(fig["c"])
        has_abs = bool((ab["abstract"] or "").strip())
        missing = [n for n, ok in (("abstract", has_abs), ("entries", entries_n > 0),
                                   ("figures", figures_n > 0)) if not ok]
        hit = {"entries_count": entries_n, "figures_count": figures_n,
               "has_abstract": has_abs, "missing_parts": missing,
               "parse_status": ("parsed" if len(missing) <= 1
                                else "partial" if len(missing) == 2 else "none")}
    missing = list(hit.get("missing_parts") or [])
    state = get_state(conn, pid)
    parts = missing or ["meta", "entries", "figures"]
    return {
        "paper_id": pid,
        "reading_state": state,
        "parse_status": hit.get("parse_status"),
        "missing_parts": missing,
        "action": "ingest" if state != "ingested" or missing else "none",
        "parts": parts,                       # 传给 /parse 或补缺流程
        "full_parse": not missing,            # 无缺失纪录 → 视为首次全量
        "counts": {"entries": hit.get("entries_count"),
                   "figures": hit.get("figures_count"),
                   "has_abstract": hit.get("has_abstract")},
    }


# ---------------------------------------------------------------- 批注 → 笔记

def annotations_to_markdown(conn, paper_id: str, *, title: str = "") -> str:
    """批注导出为 Markdown（含页码与原文引文），可直接存为笔记。"""
    annos = list_annotations(conn, paper_id)
    if not annos:
        return ""
    lines = [f"# {title or ('文献 ' + str(paper_id) + ' 阅读批注')}", ""]
    for a in annos:
        tag = {"highlight": "高亮", "note": "便签", "image": "截图"}.get(a["type"], a["type"])
        head = f"## {tag}（第 {a['page']} 页）" if a.get("page") else f"## {tag}"
        lines.append(head)
        if a.get("text"):
            lines.append(f"> {a['text']}")
        if a.get("comment"):
            lines.append(a["comment"])
        lines.append("")
    return "\n".join(lines).strip()


def export_annotations_as_note(conn, paper_id: str, *, title: str = "") -> dict:
    """把批注写进 `notes`（source='user'），返回笔记记录；无批注则返回 {}。"""
    md = annotations_to_markdown(conn, paper_id, title=title)
    if not md:
        return {}
    return _db().add_note(conn, paper_id, md,
                          title=title or f"文献 {paper_id} 阅读批注",
                          source="user")
