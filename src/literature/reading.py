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
    conn.executescript(INBOX_SCHEMA)
    _ensure_image_column(conn)
    conn.commit()


def _ensure_image_column(conn) -> None:
    """v1.9.8：`item_annotations.image_file`（区域截图批注的图片路径）。

    SQLite 的 ALTER TABLE 没有 IF NOT EXISTS → 先查 PRAGMA 再决定是否加列，
    这样已有 item_annotations 的老库也能平滑升级。
    """
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(item_annotations)")}
    except Exception:  # pragma: no cover
        return
    if cols and "image_file" not in cols:
        conn.execute("ALTER TABLE item_annotations ADD COLUMN image_file TEXT")


def _annotation_root():
    """批注图片根目录：literature/annotations/（删除前用于校验路径归属）"""
    try:
        from src import runtime_config
    except ImportError:  # pragma: no cover
        import runtime_config  # type: ignore
    return runtime_config.user_data_root() / "literature" / "annotations"


def _annotation_dir(paper_id: str):
    """区域截图存放目录：literature/annotations/<paper_id>/"""
    d = _annotation_root() / str(paper_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 阅读收件箱
# 用户决策：**先读后入库** —— 外部上传的 PDF 先进收件箱（不进文献库、不占编号），
# 读完再一键「提取解析并入库」，届时才创建文献条目。

INBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_inbox (
  inbox_id TEXT PRIMARY KEY,
  filename TEXT, path TEXT, sha1 TEXT UNIQUE, size INTEGER,
  state TEXT DEFAULT 'pending',        -- pending | ingested | discarded
  paper_id TEXT,                       -- 入库后回填
  note TEXT,
  uploaded_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_state ON reading_inbox(state);
"""

INBOX_DIRNAME = "reading_inbox"
MAX_INBOX_BYTES = 80 * 1024 * 1024


def _inbox_dir():
    try:
        from src import runtime_config
    except ImportError:  # pragma: no cover
        import runtime_config  # type: ignore
    d = runtime_config.user_data_root() / "literature" / INBOX_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def add_inbox_file(conn, filename: str, data: bytes, *, note: str = "") -> dict:
    """把外部 PDF 收进阅读清单（按 sha1 去重；不创建文献条目）。"""
    import hashlib
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > MAX_INBOX_BYTES:
        raise ValueError(f"文件过大（上限 {MAX_INBOX_BYTES // 1024 // 1024} MB）")
    name = (filename or "paper.pdf").replace("\\", "/").split("/")[-1][:120]
    if not name.lower().endswith(".pdf"):
        raise ValueError("目前只支持 PDF")
    sha1 = hashlib.sha1(data).hexdigest()
    ensure_schema(conn)
    hit = conn.execute("SELECT * FROM reading_inbox WHERE sha1=?", (sha1,)).fetchone()
    if hit is not None:
        return {"inbox_id": hit["inbox_id"], "filename": hit["filename"],
                "state": hit["state"], "duplicate": True, "size": hit["size"]}
    inbox_id = f"inb_{uuid.uuid4().hex[:12]}"
    path = _inbox_dir() / f"{inbox_id}_{name}"
    path.write_bytes(data)
    now = _db()._now()
    conn.execute(
        "INSERT INTO reading_inbox(inbox_id,filename,path,sha1,size,state,note,"
        "uploaded_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (inbox_id, name, str(path), sha1, len(data), "pending", note, now, now))
    conn.commit()
    return {"inbox_id": inbox_id, "filename": name, "state": "pending",
            "duplicate": False, "size": len(data), "path": str(path)}


def list_inbox(conn, *, state: str | None = None) -> list[dict]:
    ensure_schema(conn)
    where, params = "1=1", []
    if state:
        where += " AND state=?"
        params.append(state)
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM reading_inbox WHERE {where} ORDER BY uploaded_at DESC", params)]


def get_inbox(conn, inbox_id: str) -> dict | None:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM reading_inbox WHERE inbox_id=?",
                       (inbox_id,)).fetchone()
    return dict(row) if row is not None else None


def set_inbox_state(conn, inbox_id: str, state: str, *,
                    paper_id: str | None = None) -> dict | None:
    """更新收件箱状态（pending / ingested / discarded）；入库时回填 paper_id。"""
    if state not in ("pending", "ingested", "discarded"):
        raise ValueError("state 必须是 pending | ingested | discarded")
    if get_inbox(conn, inbox_id) is None:
        return None
    conn.execute(
        "UPDATE reading_inbox SET state=?, paper_id=COALESCE(?,paper_id), updated_at=?"
        " WHERE inbox_id=?", (state, paper_id, _db()._now(), inbox_id))
    conn.commit()
    return get_inbox(conn, inbox_id)


def delete_inbox(conn, inbox_id: str, *, remove_file: bool = True) -> bool:
    rec = get_inbox(conn, inbox_id)
    if rec is None:
        return False
    if remove_file:
        try:
            from pathlib import Path
            Path(rec["path"]).unlink(missing_ok=True)
        except Exception:  # pragma: no cover
            logger.warning("删除收件箱文件失败: %s", rec.get("path"))
    conn.execute("DELETE FROM reading_inbox WHERE inbox_id=?", (inbox_id,))
    conn.commit()
    return True


# ---------------------------------------------------------------- 批注 CRUD

def add_annotation(conn, paper_id: str, *, type_: str = "highlight",
                   page: int | None = None, position: dict | None = None,
                   color: str = "", text: str = "", comment: str = "",
                   attachment_id: str | None = None,
                   image_data: bytes | None = None) -> dict:
    ensure_schema(conn)
    if type_ not in ("highlight", "note", "image"):
        raise ValueError("type 必须是 highlight | note | image")
    db = _db()
    anno_id = f"anno_{uuid.uuid4().hex[:12]}"
    now = db._now()
    # v1.9.8：区域截图批注（image_data 落盘 → image_file 记路径）
    image_file: str | None = None
    if image_data:
        if len(image_data) > 8 * 1024 * 1024:
            raise ValueError("截图过大（上限 8MB）")
        path = _annotation_dir(str(paper_id)) / f"{anno_id}.png"
        path.write_bytes(image_data)
        image_file = str(path)
    conn.execute(
        "INSERT INTO item_annotations(annotation_id,paper_id,attachment_id,type,page,"
        "position_json,color,text,comment,image_file,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (anno_id, str(paper_id), attachment_id, type_, page,
         json.dumps(position or {}, ensure_ascii=False), color, text, comment,
         image_file, now, now))
    conn.commit()
    return {"annotation_id": anno_id, "paper_id": str(paper_id), "type": type_,
            "page": page, "position": position or {}, "color": color,
            "text": text, "comment": comment, "image_file": image_file,
            "created_at": now}


def annotation_image_path(conn, annotation_id: str):
    """取批注截图路径（无图或文件已丢失 → None）。"""
    ensure_schema(conn)
    row = conn.execute("SELECT image_file FROM item_annotations WHERE annotation_id=?",
                       (annotation_id,)).fetchone()
    if row is None or not row["image_file"]:
        return None
    from pathlib import Path
    p = Path(row["image_file"])
    return p if p.is_file() else None


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
    """删除批注；截图批注**连带删除落盘 PNG**（避免 annotations 目录无限膨胀）。

    安全约束：只删「批注根目录之内」的文件 —— 库里若存了异常路径则不动手；
    图片清理失败也不影响批注本身的删除（返回仍按数据库行的删除结果）。
    """
    ensure_schema(conn)
    row = conn.execute("SELECT image_file FROM item_annotations WHERE annotation_id=?",
                       (annotation_id,)).fetchone()
    cur = conn.execute("DELETE FROM item_annotations WHERE annotation_id=?",
                       (annotation_id,))
    conn.commit()
    if not cur.rowcount:
        return False
    img = row["image_file"] if row is not None else None
    if img:
        try:
            from pathlib import Path
            p = Path(img)
            root = _annotation_root().resolve()
            if p.is_file() and root in p.resolve().parents:
                p.unlink()
        except Exception as e:      # 图片清理失败不影响批注删除本身
            logger.debug("删除批注图片失败 %s: %s", img, e)
    return True


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
    """阅读清单：按状态筛选，附解析状态（缺什么）。

    v1.9.8：**有 PDF 附件的排在最前**（能真正打开阅读的优先），其余按编号倒序，
    避免"清单很长但点开都是无附件"的体验。
    """
    ensure_schema(conn)
    # 注意：search_papers 单次最多 500 条，而「带附件的文献」可能落在很后面
    # （实测 1529/2/1 都在前 500 之外）→ 必须分页取全量后再排序。
    papers: list[dict] = []
    for offset in range(0, 5000, 500):
        chunk = _db().search_papers(conn, limit=500, offset=offset)["papers"]
        papers.extend(chunk)
        if len(chunk) < 500:
            break
    rows = []
    for p in papers:
        st = get_state(conn, p["paper_id"])
        if state and st != state:
            continue
        rows.append({**p, "reading_state": st,
                     "annotations": len(list_annotations(conn, p["paper_id"]))})
    rows.sort(key=lambda r: (0 if r.get("attachments_count") else 1,
                             -int(r["paper_id"]) if str(r["paper_id"]).isdigit() else 0))
    return rows[:max(1, int(limit))]


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
