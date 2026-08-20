"""「科研助手」Agent 路由（MVP）：状态探针 / 会话 CRUD / SSE 流式对话。

SSE 事件契约（data 行为 JSON，钉死，前端按此消费）：
- {"type": "token", "text": "..."}                          流式文本
- {"type": "tool_call", "name": "...", "args": {...}}       开始调工具
- {"type": "tool_result", "name": "...", "summary": "...",
   "is_error": false}                                       工具返回摘要
- {"type": "done", "session_id": "..."}                     结束
- {"type": "error", "message": "..."}                       出错（LLM 未配置 /
  超时等一律走此事件，HTTP 恒 200，绝不裸 500）
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..schemas import AssistantChatRequest, AssistantSessionCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _imports():
    """延迟 import（src 路径引导依赖 api.deps；frozen/源码双兼容）。"""
    from .. import deps  # noqa: F401  # 确保 src/ 已入 sys.path
    try:
        from src.assistant import llm_bridge, loop, sessions
    except ImportError:  # pragma: no cover
        from assistant import llm_bridge, loop, sessions  # type: ignore
    return llm_bridge, loop, sessions


@router.get("/status")
def status():
    """助手可用性：LLM 未配置时 enabled=false 并给出引导文案。"""
    llm_bridge, _loop, _sessions = _imports()
    enabled = llm_bridge.is_configured()
    return {
        "enabled": enabled,
        "reason": "" if enabled else
        "未配置 LLM：请到设置页填写 base_url / api_key / model 后再使用科研助手。",
    }


@router.post("/sessions")
def create_session(req: AssistantSessionCreate):
    """创建会话；带 context 时存入 meta（首轮对话注入 system prompt）。"""
    _llm_bridge, _loop, sessions = _imports()
    return sessions.create_session(title=req.title, context=req.context)


@router.get("/sessions")
def list_sessions():
    """会话列表（updated_at 倒序，含 message_count）。"""
    _llm_bridge, _loop, sessions = _imports()
    return {"sessions": sessions.list_sessions()}


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """完整会话（含 context 与 messages；消息可带 tool_events）。"""
    _llm_bridge, _loop, sessions = _imports()
    sess = sessions.load_session(session_id)
    if sess is None:
        raise HTTPException(404, f"会话不存在: {session_id}")
    return {
        "session_id": sess["session_id"],
        "title": sess["title"],
        "context": sess["context"],
        "messages": sess["messages"],
    }


@router.post("/chat")
def chat(req: AssistantChatRequest):
    """SSE 流式对话。任何失败（含 LLM 未配置）都以 error 事件收尾。"""
    message = (req.message or "").strip()

    def gen():
        llm_bridge, loop, sessions = _imports()
        try:
            if not message:
                yield _sse({"type": "error", "message": "message 不能为空"})
                return
            if not llm_bridge.is_configured():
                yield _sse({"type": "error",
                            "message": "LLM 未配置：请到设置页填写 base_url / "
                                       "api_key / model 后再使用科研助手。"})
                return

            # 解析 / 创建会话
            if req.session_id:
                sess = sessions.load_session(req.session_id)
                if sess is None:
                    yield _sse({"type": "error",
                                "message": f"会话不存在: {req.session_id}"})
                    return
                if isinstance(req.context, dict) and req.context:
                    sess = sessions.update_meta(sess["session_id"],
                                                context=req.context)
            else:
                created = sessions.create_session(
                    title=message[:20], context=req.context)
                sess = sessions.load_session(created["session_id"])
            session_id = sess["session_id"]

            reply_parts: list[str] = []
            tool_events: list[dict] = []
            errored = False
            for ev in loop.run(sess, message):
                if ev.get("type") == "token":
                    reply_parts.append(ev.get("text") or "")
                elif ev.get("type") in ("tool_call", "tool_result"):
                    tool_events.append(ev)
                elif ev.get("type") == "error":
                    errored = True
                yield _sse(ev)

            # 会话落盘（user + assistant 各一条；工具过程挂在 assistant 上）
            sessions.append_message(session_id, "user", message)
            sessions.append_message(session_id, "assistant",
                                    "".join(reply_parts), tool_events)
            if not errored:
                yield _sse({"type": "done", "session_id": session_id})
        except Exception as exc:  # 兜底：任何意外都走 error 事件，不裸 500
            logger.exception("assistant chat 异常")
            yield _sse({"type": "error",
                        "message": f"助手内部错误：{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
