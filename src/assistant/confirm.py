"""写操作二次确认：一次性令牌（绑定会话 + 工具 + 参数摘要，5 分钟过期）。

流程：
1. agent loop 命中写类工具 → create() 生成令牌，随 tool_confirm SSE 事件
   下发给前端（含影响说明与参数摘要），工具本体不执行；
2. 用户在前端确认卡上点「确认执行」/「取消」→ POST /api/assistant/chat/confirm
   携带令牌 → consume() 校验并取出原始参数；
3. 校验通过才执行工具（参数以服务端存档为准，不信客户端回显）。

令牌纪律：一次性（consume 成功即销毁）；过期 / 会话不符 / 参数摘要
不符一律拒绝。存储为进程内内存表（参照 dft/jobs.py 口径）——服务重启
后令牌失效，用户重新发起即可，无副作用。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid

TTL_SECONDS = 300          # 令牌有效期：5 分钟
_MAX_PENDING = 500         # 内存表上限（防膨胀，超出淘汰最旧）

_LOCK = threading.Lock()
_PENDING: dict[str, dict] = {}


def _digest(name: str, args: dict) -> str:
    """工具名 + 参数的稳定性摘要（排序序列化后 sha256）。"""
    payload = json.dumps({"name": name, "args": args or {}},
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prune_locked(now: float) -> None:
    """清理过期令牌；仍超上限时淘汰最旧。"""
    expired = [t for t, r in _PENDING.items()
               if now - float(r.get("created_at") or 0) > TTL_SECONDS]
    for t in expired:
        _PENDING.pop(t, None)
    if len(_PENDING) > _MAX_PENDING:
        oldest = sorted(_PENDING.items(),
                        key=lambda kv: float(kv[1].get("created_at") or 0))
        for t, _ in oldest[: len(_PENDING) - _MAX_PENDING]:
            _PENDING.pop(t, None)


def create(session_id: str, name: str, args: dict,
           impact: str, args_summary: str = "") -> dict:
    """登记一条待确认写操作，返回令牌记录（含 token）。"""
    args = args if isinstance(args, dict) else {}
    token = f"cfm_{uuid.uuid4().hex[:16]}"
    rec = {
        "token": token,
        "session_id": session_id or "",
        "name": name,
        "args": dict(args),
        "digest": _digest(name, args),
        "impact": impact or "执行写操作",
        "args_summary": args_summary or "",
        "created_at": time.time(),
    }
    with _LOCK:
        _PENDING[token] = rec
        _prune_locked(rec["created_at"])
    return rec


def consume(token: str, session_id: str,
            args: dict | None = None) -> tuple[dict | None, str | None]:
    """校验并取出待确认记录。返回 (record, None) 或 (None, 中文错误)。

    - 令牌不存在 / 已使用 → 拒绝；
    - 过期 → 拒绝并销毁；
    - 会话不符 → 拒绝（令牌保留，合法会话仍可确认）；
    - 客户端回显 args 与存档摘要不符 → 拒绝（令牌保留，视为篡改尝试）；
    - 全部通过 → 销毁令牌（一次性）并返回记录。
    """
    token = (token or "").strip()
    with _LOCK:
        rec = _PENDING.get(token)
    if rec is None:
        return None, "确认令牌不存在或已被使用（每次确认仅生效一次）"
    if time.time() - float(rec.get("created_at") or 0) > TTL_SECONDS:
        with _LOCK:
            _PENDING.pop(token, None)
        return None, "确认已过期（5 分钟有效），请让助手重新发起该操作"
    if rec.get("session_id") != (session_id or ""):
        return None, "确认令牌不属于当前会话，已拒绝"
    if args is not None and _digest(str(rec.get("name")), args) != rec.get("digest"):
        return None, "参数与发起时不一致，已拒绝执行（请让助手重新发起）"
    with _LOCK:
        _PENDING.pop(token, None)
    return rec, None


def pending_count() -> int:
    """当前待确认条数（测试 / 调试探针用）。"""
    with _LOCK:
        return len(_PENDING)
