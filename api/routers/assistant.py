"""「科研助手」Agent 路由（MVP）：状态探针 / 会话 CRUD / SSE 流式对话 / 附件上传。

SSE 事件契约（data 行为 JSON，钉死，前端按此消费）：
- {"type": "token", "text": "..."}                          流式文本
- {"type": "tool_call", "name": "...", "args": {...}}       开始调工具
- {"type": "tool_result", "name": "...", "summary": "...",
   "is_error": false}                                       工具返回摘要
  （用户取消的写操作附带 "cancelled": true）
- {"type": "tool_confirm", "confirm_token": "...",
   "name": "...", "args": {...}, "args_summary": "...",
   "impact": "...", "expires_in": 300}                      写操作二次确认
  （工具未执行，流随后正常 done 收尾；前端据此渲染确认卡）
- {"type": "done", "session_id": "..."}                     结束
- {"type": "error", "message": "..."}                       出错（LLM 未配置 /
  超时等一律走此事件，HTTP 恒 200，绝不裸 500）

写操作二次确认：命中写类工具时 loop 挂起并发 tool_confirm；前端确认后
POST /chat/confirm（session_id + confirm_token + decision）→ 本路由校验
令牌（一次性、绑定会话 + 参数摘要、5 分钟过期）→ 确认则执行工具并
SSE 续跑对话，取消则注入"用户拒绝了该操作"继续对话。

附件：POST /uploads 上传（multipart，图片 png/jpg/jpeg/webp，
文档 txt/md/json/csv/docx/pdf，单文件 ≤10MB）→ 返回 upload_id；
chat 请求 attachments 字段携带 upload_id 列表（≤3 个），
文档提取文本注入消息上下文，图片以 vision 格式发给 LLM（不支持时自动降级）。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..schemas import (AssistantChatRequest, AssistantConfirmRequest,
                       AssistantSessionCreate)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _imports():
    """延迟 import（src 路径引导依赖 api.deps；frozen/源码双兼容）。"""
    from .. import deps  # noqa: F401  # 确保 src/ 已入 sys.path
    try:
        from src.assistant import (attachments, confirm, llm_bridge, loop,
                                   registry, sessions)
    except ImportError:  # pragma: no cover
        from assistant import (attachments, confirm, llm_bridge, loop,  # type: ignore
                               registry, sessions)
    return attachments, confirm, llm_bridge, loop, registry, sessions


@router.get("/status")
def status():
    """助手可用性：LLM 未配置时 enabled=false 并给出引导文案。"""
    _attachments, _confirm, llm_bridge, _loop, _registry, _sessions = _imports()
    enabled = llm_bridge.is_configured()
    return {
        "enabled": enabled,
        "reason": "" if enabled else
        "未配置 LLM：请到设置页填写 base_url / api_key / model 后再使用科研助手。",
    }


@router.post("/uploads", status_code=201)
async def upload_attachment(file: UploadFile):
    """上传附件（图片/文档），返回元信息（含 upload_id）。

    类型/大小校验失败返回 400 中文原因；文件本体存
    user_data_root/assistant/uploads/（打包版不写安装目录）。
    """
    attachments, _confirm, _llm_bridge, _loop, _registry, _sessions = _imports()
    data = await file.read()
    try:
        return attachments.save_upload(file.filename or "attachment", data)
    except attachments.AttachmentError as exc:
        raise HTTPException(400, str(exc))


@router.post("/sessions")
def create_session(req: AssistantSessionCreate):
    """创建会话；带 context 时存入 meta（首轮对话注入 system prompt）。"""
    _attachments, _confirm, _llm_bridge, _loop, _registry, sessions = _imports()
    return sessions.create_session(title=req.title, context=req.context)


@router.get("/sessions")
def list_sessions():
    """会话列表（updated_at 倒序，含 message_count）。"""
    _attachments, _confirm, _llm_bridge, _loop, _registry, sessions = _imports()
    return {"sessions": sessions.list_sessions()}


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """完整会话（含 context 与 messages；消息可带 tool_events / attachments）。"""
    _attachments, _confirm, _llm_bridge, _loop, _registry, sessions = _imports()
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
    """SSE 流式对话。任何失败（含 LLM 未配置）都以 error 事件收尾。

    attachments 为 upload_id 列表（≤3 个）：文档提取文本注入消息上下文，
    图片以 vision 格式发出（端点/模型不支持时 loop 自动降级为文字提示）。
    消息文本可为空（有附件时用兜底文案）。
    """
    message = (req.message or "").strip()

    def gen():
        attachments, _confirm, llm_bridge, loop, _registry, sessions = _imports()
        try:
            # 解析附件元信息（无效 id 跳过，不阻塞对话）
            att_metas: list[dict] = []
            for uid in (req.attachments or [])[:attachments.MAX_ATTACHMENTS_PER_MESSAGE]:
                meta = attachments.get_meta(uid)
                if meta is not None:
                    att_metas.append(meta)

            effective_message = message
            if not effective_message:
                if not att_metas:
                    yield _sse({"type": "error", "message": "message 不能为空"})
                    return
                effective_message = "请查看我上传的附件并给出分析。"
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
                    title=effective_message[:20], context=req.context)
                sess = sessions.load_session(created["session_id"])
            session_id = sess["session_id"]

            reply_parts: list[str] = []
            tool_events: list[dict] = []
            errored = False
            for ev in loop.run(sess, effective_message, attachments=att_metas):
                if ev.get("type") == "token":
                    reply_parts.append(ev.get("text") or "")
                elif ev.get("type") in ("tool_call", "tool_result",
                                       "tool_confirm"):
                    tool_events.append(ev)
                elif ev.get("type") == "error":
                    errored = True
                yield _sse(ev)

            # 会话落盘（user + assistant 各一条；工具过程挂在 assistant 上，
            # 附件元信息随 user 消息持久化）
            sessions.append_message(session_id, "user", effective_message,
                                    attachments=att_metas)
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


@router.post("/chat/confirm")
def chat_confirm(req: AssistantConfirmRequest):
    """写操作二次确认：SSE 流式续跑（事件契约同 /chat）。

    令牌校验（confirm.consume）：一次性、绑定 session、5 分钟过期、
    客户端回显 args 与存档摘要不符即拒绝。确认 → 执行工具（参数以服务端
    存档为准）并把 tool result 回注对话续跑；取消 → 注入"用户拒绝了该
    操作"续跑。续跑中再命中写工具会再次发 tool_confirm 挂起。
    """
    def gen():
        _attachments, confirm, llm_bridge, loop, registry, sessions = _imports()
        try:
            if not llm_bridge.is_configured():
                yield _sse({"type": "error",
                            "message": "LLM 未配置：请到设置页填写 base_url / "
                                       "api_key / model 后再使用科研助手。"})
                return
            sess = sessions.load_session(req.session_id)
            if sess is None:
                yield _sse({"type": "error",
                            "message": f"会话不存在: {req.session_id}"})
                return
            rec, err = confirm.consume(req.confirm_token, req.session_id,
                                       req.args)
            if err is not None:
                yield _sse({"type": "error", "message": err})
                return

            decision = (req.decision or "confirm").strip().lower()
            rejected = decision != "confirm"
            name, args = str(rec["name"]), rec["args"]

            reply_parts: list[str] = []
            tool_events: list[dict] = []
            errored = False

            if rejected:
                ev_result = {"type": "tool_result", "name": name,
                             "summary": "用户取消了该操作，未执行。",
                             "is_error": False, "cancelled": True}
                tool_events.append(ev_result)
                yield _sse(ev_result)
                events = loop.run_resume(sess, name, args, rejected=True)
            else:
                result = registry.execute(name, args)
                ev_result = {"type": "tool_result", "name": name,
                             "summary": registry.summary_of(result),
                             "is_error": bool(result.get("is_error"))}
                tool_events.append(ev_result)
                yield _sse(ev_result)
                events = loop.run_resume(sess, name, args, result=result)

            for ev in events:
                if ev.get("type") == "token":
                    reply_parts.append(ev.get("text") or "")
                elif ev.get("type") in ("tool_call", "tool_result",
                                       "tool_confirm"):
                    tool_events.append(ev)
                elif ev.get("type") == "error":
                    errored = True
                yield _sse(ev)

            sessions.append_message(sess["session_id"], "assistant",
                                    "".join(reply_parts), tool_events)
            if not errored:
                yield _sse({"type": "done", "session_id": sess["session_id"]})
        except Exception as exc:  # 兜底：任何意外都走 error 事件，不裸 500
            logger.exception("assistant confirm 异常")
            yield _sse({"type": "error",
                        "message": f"助手内部错误：{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
