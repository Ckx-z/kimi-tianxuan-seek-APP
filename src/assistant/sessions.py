"""科研助手会话存储：jsonl 追加式（参照 openhanako lib/session-jsonl.ts 思路）。

落盘：runtime_config.user_data_root()/assistant/sessions/sess_<uuid12>.jsonl
（frozen 时自动落 %APPDATA%/COF-Film-Recommend/data/assistant/sessions/）。

行格式：
- 首行 {"kind": "meta", "session_id", "title", "context", "created_at"}
- 消息行 {"kind": "message", "role", "content", "tool_events",
         "attachments", "created_at"}
  （attachments 为附件元信息列表 {upload_id, filename, ext, kind, size}，
   文件本体存 user_data_root/assistant/uploads/）

meta 更新（标题 / context）采用整体重写（文件小，读改写即可）；
消息一律 append。损坏行跳过不崩。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

SESSIONS_DIR = runtime_config.user_data_root() / "assistant" / "sessions"

_ID_RE = re.compile(r"^sess_[0-9a-f]{12}$")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"


def _path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.jsonl"


def _valid_id(session_id: str) -> bool:
    return bool(isinstance(session_id, str) and _ID_RE.match(session_id))


def _read_lines(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue  # 损坏行跳过
            if isinstance(obj, dict):
                out.append(obj)
    except Exception as exc:
        logger.warning("会话读取失败 %s: %s", path.name, exc)
    return out


def _parse(lines: list[dict], session_id: str) -> dict | None:
    meta = next((l for l in lines if l.get("kind") == "meta"), None)
    if meta is None:
        return None
    messages = []
    for l in lines:
        if l.get("kind") != "message":
            continue
        msg = {
            "role": l.get("role") or "user",
            "content": l.get("content") or "",
            "created_at": l.get("created_at") or "",
        }
        if l.get("tool_events"):
            msg["tool_events"] = l["tool_events"]
        if l.get("attachments"):
            msg["attachments"] = l["attachments"]
        messages.append(msg)
    last_at = messages[-1]["created_at"] if messages else meta.get("created_at", "")
    return {
        "session_id": session_id,
        "title": meta.get("title") or "新会话",
        "context": meta.get("context") or {},
        "created_at": meta.get("created_at") or "",
        "updated_at": last_at,
        "messages": messages,
        "report": meta.get("report"),  # v1.7.0：一对话一报告指针（无则 None）
    }


def create_session(title: str | None = None, context: dict | None = None) -> dict:
    """建档，返回 {"session_id", "title"}。context 原样存入 meta。"""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_id = _new_id()
    meta = {
        "kind": "meta",
        "session_id": session_id,
        "title": (title or "").strip() or "新会话",
        "context": context if isinstance(context, dict) else {},
        "created_at": _now(),
    }
    with _path(session_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
    return {"session_id": session_id, "title": meta["title"]}


def load_session(session_id: str) -> dict | None:
    """载入完整会话；不存在 / id 非法 / 无 meta 返回 None。"""
    if not _valid_id(session_id):
        return None
    path = _path(session_id)
    if not path.is_file():
        return None
    return _parse(_read_lines(path), session_id)


def list_sessions() -> list[dict]:
    """会话列表（updated_at 倒序）：{session_id, title, updated_at, message_count}。"""
    out: list[dict] = []
    if not SESSIONS_DIR.is_dir():
        return out
    for p in SESSIONS_DIR.glob("sess_*.jsonl"):
        if not _valid_id(p.stem):
            continue
        sess = _parse(_read_lines(p), p.stem)
        if sess is None:
            continue
        out.append({
            "session_id": sess["session_id"],
            "title": sess["title"],
            "updated_at": sess["updated_at"],
            "message_count": len(sess["messages"]),
        })
    out.sort(key=lambda s: s["updated_at"], reverse=True)
    return out


def append_message(session_id: str, role: str, content: str,
                   tool_events: list | None = None,
                   attachments: list | None = None) -> dict | None:
    """追加一条消息，返回该消息 dict；会话不存在返回 None。

    attachments：附件元信息列表（{upload_id, filename, ext, kind, size}），
    随消息持久化；文件本体在 uploads/ 目录，不进 jsonl。
    """
    if load_session(session_id) is None:
        return None
    msg = {
        "kind": "message",
        "role": role,
        "content": content or "",
        "tool_events": list(tool_events or []),
        "attachments": list(attachments or []),
        "created_at": _now(),
    }
    with _path(session_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


_UNSET = object()


def update_meta(session_id: str, title: str | None = None,
                context: dict | None = None, merge_context: bool = True,
                report=_UNSET) -> dict | None:
    """更新 meta（标题 / context / report 指针），整体重写文件。

    report：dict（写入指针）或 None（清除指针）；缺省 _UNSET 表示不动。
    会话不存在返回 None。
    """
    sess = load_session(session_id)
    if sess is None:
        return None
    new_context = sess["context"]
    if context is not None:
        new_context = {**sess["context"], **context} if merge_context else context
    meta = {
        "kind": "meta",
        "session_id": session_id,
        "title": (title or "").strip() or sess["title"],
        "context": new_context,
        "created_at": sess["created_at"] or _now(),
    }
    if report is not _UNSET:
        if isinstance(report, dict) and report:
            meta["report"] = report
        else:
            meta.pop("report", None)
    elif sess.get("report"):
        meta["report"] = sess["report"]
    lines = [json.dumps(meta, ensure_ascii=False)]
    for m in sess["messages"]:
        row = {
            "kind": "message",
            "role": m["role"],
            "content": m["content"],
            "tool_events": m.get("tool_events") or [],
            "attachments": m.get("attachments") or [],
            "created_at": m.get("created_at") or "",
        }
        lines.append(json.dumps(row, ensure_ascii=False))
    _path(session_id).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_session(session_id)


def delete_session(session_id: str) -> bool:
    """删除会话（v1.7.0）：物理删除 jsonl；uploads/ 附件文件保留不删。

    会话不存在 / id 非法返回 False。
    """
    if not _valid_id(session_id):
        return False
    path = _path(session_id)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except Exception as exc:  # 删除失败（占用等）如实返回 False
        logger.warning("会话删除失败 %s: %s", session_id, exc)
        return False
    return True
