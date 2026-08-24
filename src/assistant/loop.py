"""Agent 主循环：LLM ↔ 工具调用，max 5 轮防死循环。

产出 SSE 事件 dict（契约见 api/routers/assistant.py 头注释）：
token / tool_call / tool_result / error；done 由路由层在持久化后补发。

双路径：
- 路径 A（function calling）：llm_bridge.chat_completion_with_tools；
  端点不支持 / 报错（4xx）/ 返回格式乱 → FunctionCallingUnsupported →
  降级路径 B（已产生的工具事件保留，历史消息清洗成纯文本角色后继续）。
- 路径 B（两段式计划-执行）：提示词要求模型输出 JSON 指令
  {"tool": "...", "args": {...}} 或 {"reply": "..."}，解析（正则提取 +
  失败重问一次容错）后循环执行，结果以 user 角色回填。

会话级记忆压缩：历史消息（user+assistant 计）超 20 条时，早期轮次经 LLM
压缩成「对话纪要」（保留工具结果要点：分数、结论、用户决定），上下文 =
纪要 + 最近 10 条；LLM 不可用时降级硬截断。用户级长期记忆注入见
memory.py（system prompt 的「用户记忆」段，可在设置页关闭）。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Iterator

from . import confirm as confirm_module
from . import context as context_module
from . import llm_bridge, memory as memory_module, persona, registry
from .llm_bridge import FunctionCallingUnsupported, LLMCallError

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5       # 工具调用轮次上限（防死循环）
_COMPRESS_THRESHOLD = 20  # 历史消息（user+assistant 计）超过即触发会话级压缩
_RECENT_KEEP = 10         # 压缩后保留的最近消息条数（早期轮次进纪要）
_SUMMARY_MAX_TOKENS = 800
_HISTORY_MAX_CHARS = 2000  # 单条历史消息限长
_TOKEN_CHUNK = 16         # 伪流式吐字粒度（HTTP 非流式，切片推送）

_PLAN_PROMPT = (
    "你是一个计划-执行代理。每一轮只输出一个 JSON 对象，不要输出任何其他文字，"
    "不要用 markdown 代码块包裹：\n"
    "- 需要调用工具：{\"tool\": \"工具名\", \"args\": {...}}\n"
    "- 已经能回答用户：{\"reply\": \"完整回答文本\"}\n\n"
    "可用工具：\n" + registry.describe_tools() + "\n\n"
    "工具执行结果会以用户消息的形式回给你，再决定下一步。"
    "回答必须遵守 system prompt 里的引用纪律：数据、文献、历史实验的论断"
    "只能来自工具返回，查不到就说“系统内未查到”。"
)

_PLAN_RETRY_HINT = (
    "格式错误。请严格只输出一个 JSON 对象（不要任何其他文字）："
    "{\"tool\": \"工具名\", \"args\": {...}} 或 {\"reply\": \"回答\"}。"
)


def _stream_text(text: str) -> Iterator[dict]:
    """把完整文本切成 token 事件（伪流式，前端无感差异）。"""
    for i in range(0, len(text), _TOKEN_CHUNK):
        yield {"type": "token", "text": text[i:i + _TOKEN_CHUNK]}


def _history_attachment_note(m: dict) -> str:
    """历史消息的附件提示（附件内容不重复注入，只保留存在性说明）。"""
    names = [str(a.get("filename") or "附件")
             for a in (m.get("attachments") or []) if isinstance(a, dict)]
    return f"\n[附件：{'、'.join(names)}]" if names else ""


def build_user_content(user_message: str,
                       attachments: list | None = None) -> str | list:
    """组装本轮用户消息 content。

    - 无附件：纯文本原样返回；
    - 文档类附件：提取文本追加到文本尾部（注入上下文）；
    - 图片附件：转 OpenAI vision 格式（content 为 text + image_url 分片列表，
      base64 data URL）；路径 A 直接发出，端点/模型不支持时由 loop 降级
      路径 B，_sanitize_for_plain 会把图片分片替换为「不支持看图」提示。
    附件文本提取失败（损坏/无文本）不阻塞对话：以一条说明代替。
    """
    from . import attachments as att_module  # 延迟 import，避免循环依赖

    metas = [m for m in (attachments or []) if isinstance(m, dict)]
    if not metas:
        return user_message
    text = user_message
    images: list[dict] = []
    for meta in metas[:attachments_max()]:
        filename = str(meta.get("filename") or "附件")
        if meta.get("kind") == "image":
            try:
                url = att_module.image_data_url(meta)
            except Exception as exc:
                text += f"\n\n[图片附件 {filename} 读取失败：{exc}]"
                continue
            text += f"\n\n[用户上传了图片：{filename}]"
            images.append({"type": "image_url", "image_url": {"url": url}})
        else:
            try:
                doc_text = att_module.extract_text(meta)
                text += (f"\n\n【附件 {filename} 内容】\n{doc_text}")
            except Exception as exc:
                text += f"\n\n[附件 {filename} 无法提取文本：{exc}]"
    if not images:
        return text
    return [{"type": "text", "text": text}] + images


def attachments_max() -> int:
    from . import attachments as att_module
    return att_module.MAX_ATTACHMENTS_PER_MESSAGE


def build_messages(session: dict, user_message: str,
                   attachments: list | None = None) -> list[dict]:
    """组装 messages = [system(人格+领域规则+记忆+上下文), ...历史, user]。

    历史超 _COMPRESS_THRESHOLD 条时压缩为「纪要 + 最近 N 条」；
    LLM 不可用时降级硬截断（只留最近 N 条），不报错。
    """
    context_block = context_module.build_context_block(session.get("context"))
    messages = [{"role": "system",
                 "content": persona.build_system_prompt(
                     context_block,
                     memory_block=memory_module.injection_block())}]
    messages.extend(_history_block(session))
    messages.append({
        "role": "user",
        "content": build_user_content(user_message, attachments),
    })
    return messages


_SUMMARY_PROMPT = (
    "你是会话摘要助手。把以下早期对话压缩成一段「对话纪要」：保留工具结果要点"
    "（打分分数、查询结论、用户做出的决定与偏好），丢弃寒暄与重复内容。"
    "200 字以内，直接输出纪要文本，不要标题与前后缀。"
)

# 纪要缓存：session_id -> {"covered": 已压缩的早期消息条数, "text": 纪要}
# 进程内即可——重启后重算一次，无副作用。
_SUMMARY_CACHE: dict[str, dict] = {}


def _session_summary(session_id: str, early: list[dict]) -> str | None:
    """早期轮次 → 对话纪要（缓存：覆盖条数一致时复用，不重复调 LLM）。

    LLM 未配置 / 调用失败返回 None，调用方降级为硬截断。
    """
    if not early:
        return None
    cache = _SUMMARY_CACHE.get(session_id)
    if cache and cache.get("covered") == len(early):
        return cache.get("text")
    lines = []
    for m in early:
        role = "用户" if m.get("role") == "user" else "助手"
        lines.append(f"{role}：{str(m.get('content') or '')[:_HISTORY_MAX_CHARS]}")
    text = llm_bridge.chat_text(
        [{"role": "system", "content": _SUMMARY_PROMPT},
         {"role": "user", "content": "\n".join(lines)}],
        max_tokens=_SUMMARY_MAX_TOKENS)
    if not text or not text.strip():
        return None
    summary = text.strip()
    _SUMMARY_CACHE[session_id] = {"covered": len(early), "text": summary}
    return summary


def _history_block(session: dict) -> list[dict]:
    """历史消息块：超阈值时「纪要（system）+ 最近 N 条」，否则全量。"""
    raw = [m for m in (session.get("messages") or [])
           if m.get("role") in ("user", "assistant")]
    summary = None
    if len(raw) > _COMPRESS_THRESHOLD:
        early, raw = raw[:-_RECENT_KEEP], raw[-_RECENT_KEEP:]
        summary = _session_summary(str(session.get("session_id") or ""), early)
        # summary 为 None → 早期轮次直接丢弃（硬截断降级）
    out: list[dict] = []
    if summary:
        out.append({"role": "system",
                    "content": "# 本会话早期对话纪要（早期轮次已压缩）\n" + summary})
    for m in raw:
        content = str(m.get("content") or "")[:_HISTORY_MAX_CHARS]
        out.append({
            "role": m["role"],
            "content": content + _history_attachment_note(m),
        })
    return out


def _tool_event_pair(name: str, args: dict, result: dict) -> Iterator[dict]:
    """一次工具调用的两个 SSE 事件。"""
    yield {"type": "tool_call", "name": name, "args": args}
    yield {"type": "tool_result", "name": name,
           "summary": registry.summary_of(result),
           "is_error": bool(result.get("is_error"))}


def _confirm_gate(session_id: str, name: str, args: dict) -> dict | None:
    """写操作确认门：需要确认时生成令牌并返回 tool_confirm 事件，否则 None。

    命中确认时调用方应：先发 tool_call 事件（卡片可见），再发本事件，
    然后终止本轮循环（工具未执行，等 /chat/confirm 续跑）。
    """
    impact = registry.confirm_impact(name, args)
    if impact is None:
        return None
    args_summary = json.dumps(args or {}, ensure_ascii=False, default=str)
    if len(args_summary) > 200:
        args_summary = args_summary[:200] + "…"
    rec = confirm_module.create(session_id, name, args or {}, impact,
                                args_summary=args_summary)
    return {"type": "tool_confirm", "confirm_token": rec["token"],
            "name": name, "args": args or {}, "args_summary": args_summary,
            "impact": impact, "expires_in": confirm_module.TTL_SECONDS}


def _final_answer(messages: list[dict]) -> Iterator[dict]:
    """工具轮次用尽后的收尾：逼模型基于已获取信息直接作答。"""
    closing = list(messages) + [{
        "role": "user",
        "content": "工具调用轮次已用完。请基于已获得的信息直接给出最终回答；"
                   "信息不足的部分如实说明。",
    }]
    text = llm_bridge.chat_text(closing)
    if text and text.strip():
        yield from _stream_text(text.strip())
    else:
        yield from _stream_text(
            "已达到本次对话的工具调用上限，且收尾回答生成失败。"
            "已获取的工具结果见上方过程记录，可换个问法继续。")


def _run_function_calling(messages: list[dict],
                          session_id: str = "") -> Iterator[dict]:
    """路径 A：OpenAI function calling。"""
    for _ in range(MAX_TOOL_ROUNDS):
        resp = llm_bridge.chat_completion_with_tools(
            messages, registry.list_tool_schemas())
        calls = resp.get("tool_calls") or []
        if not calls:
            content = str(resp.get("content") or "").strip()
            if not content:
                raise FunctionCallingUnsupported("响应无 content（格式乱）")
            yield from _stream_text(content)
            return
        messages.append({
            "role": "assistant",
            "content": resp.get("content") or "",
            "tool_calls": [{
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"],
                             "arguments": json.dumps(tc["args"],
                                                     ensure_ascii=False)},
            } for tc in calls],
        })
        for tc in calls:
            gate = _confirm_gate(session_id, tc["name"], tc["args"])
            if gate is not None:
                yield {"type": "tool_call", "name": tc["name"],
                       "args": tc["args"]}
                yield gate
                return  # 挂起等确认，工具未执行
            result = registry.execute(tc["name"], tc["args"])
            yield from _tool_event_pair(tc["name"], tc["args"], result)
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": result["text"]})
    yield from _final_answer(messages)


def _parse_directive(text: str) -> dict | None:
    """从模型输出提取 JSON 指令（容错：去围栏 + 截取首尾大括号）。

    返回 {"tool":..., "args":...} 或 {"reply":...}；无法解析返回 None。
    """
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # 去 markdown 围栏
        lines = [l for l in s.splitlines() if not l.strip().startswith("```")]
        s = "\n".join(lines).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if "reply" in obj or "tool" in obj:
        return obj
    return None


def _plain_content(content) -> str:
    """把可能为 vision 分片列表的 content 摊平成纯文本。

    图片分片（image_url）无法走纯文本端点，替换为「不支持看图」提示；
    文件名说明已在 build_user_content 里并入 text 分片。
    """
    if isinstance(content, list):
        texts: list[str] = []
        has_image = False
        for part in content:
            if not isinstance(part, dict):
                texts.append(str(part))
            elif part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
            elif part.get("type") == "image_url":
                has_image = True
        out = "".join(texts)
        if has_image:
            out += "\n[用户上传了图片，当前模型不支持看图，请基于已有文字信息回答]"
        return out
    return str(content or "")


def _sanitize_for_plain(messages: list[dict]) -> list[dict]:
    """降级路径 B 前清洗：tool 角色与 tool_calls 字段不是纯文本端点都认，
    转成 user 角色文本，assistant 的 tool_calls 字段剥掉；
    vision 分片列表 content 摊平为纯文本（图片替换为提示）。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "user",
                        "content": "工具返回：\n" + str(m.get("content") or "")})
        elif role == "assistant":
            out.append({"role": "assistant",
                        "content": _plain_content(m.get("content"))})
        else:
            out.append({"role": role, "content": _plain_content(m.get("content"))})
    return out


def _run_plan_execute(messages: list[dict],
                      session_id: str = "") -> Iterator[dict]:
    """路径 B（降级）：两段式计划-执行。"""
    work = _sanitize_for_plain(messages) + [
        {"role": "system", "content": _PLAN_PROMPT}]
    retried = False
    for _ in range(MAX_TOOL_ROUNDS):
        text = llm_bridge.chat_text(work)
        if text is None:
            yield {"type": "error",
                   "message": "LLM 调用失败（未配置 / 超时 / 响应为空），"
                              "请到设置页检查 LLM 配置后重试。"}
            return
        directive = _parse_directive(text)
        if directive is None:
            if not retried:
                retried = True
                work.append({"role": "assistant", "content": text})
                work.append({"role": "user", "content": _PLAN_RETRY_HINT})
                continue
            # 重问仍乱格式：把原文当回答吐出（不空转、不丢内容）
            yield from _stream_text(text.strip())
            return
        reply = directive.get("reply")
        if reply is not None:
            yield from _stream_text(str(reply))
            return
        name = str(directive.get("tool") or "").strip()
        args = directive.get("args")
        args = args if isinstance(args, dict) else {}
        gate = _confirm_gate(session_id, name, args)
        if gate is not None:
            yield {"type": "tool_call", "name": name, "args": args}
            yield gate
            return  # 挂起等确认，工具未执行
        result = registry.execute(name, args)
        yield from _tool_event_pair(name, args, result)
        work.append({"role": "assistant", "content": text})
        work.append({
            "role": "user",
            "content": f"工具 {name} 返回：\n{result['text']}\n\n"
                       "请继续：输出下一个 JSON 指令。"})
    yield from _final_answer(work)


def run(session: dict, user_message: str,
        attachments: list | None = None) -> Iterator[dict]:
    """agent 主入口。session 为 sessions.load_session 的返回（不含本轮
    用户消息，由本函数拼入）。attachments 为附件元信息列表（文档提取文本
    注入上下文，图片走 vision 格式、报错自动降级纯文本提示）。
    LLM 未配置 / 调用失败一律走 error 事件。
    命中写类工具时发 tool_confirm 事件并挂起（工具未执行），等前端
    /chat/confirm 确认后经 run_resume 续跑。"""
    session_id = str(session.get("session_id") or "")
    messages = build_messages(session, user_message, attachments)
    try:
        yield from _run_function_calling(messages, session_id)
        return
    except FunctionCallingUnsupported as exc:
        logger.info("function calling 不可用，降级两段式: %s", exc)
    except LLMCallError as exc:
        yield {"type": "error", "message": f"LLM 调用失败：{exc}"}
        return
    except Exception as exc:  # 兜底：任何意外都走 error 事件，不裸 500
        logger.exception("agent loop 异常")
        yield {"type": "error",
               "message": f"助手内部错误：{type(exc).__name__}: {exc}"}
        return
    yield from _run_plan_execute(messages, session_id)


def _resume_messages(session: dict, name: str, args: dict,
                     result: dict | None, rejected: bool) -> list[dict]:
    """确认续跑的消息重建：system + 历史 + 工具结果（或用户拒绝说明）。

    历史只含持久化的 user/assistant 文本；被确认的工具调用以合成的
    assistant(tool_calls) + tool 消息对拼在末尾（路径 A 原生消费；
    路径 B 经 _sanitize_for_plain 转成 user 文本）。
    """
    context_block = context_module.build_context_block(session.get("context"))
    messages = [{"role": "system",
                 "content": persona.build_system_prompt(
                     context_block,
                     memory_block=memory_module.injection_block())}]
    messages.extend(_history_block(session))
    if rejected:
        brief = json.dumps(args or {}, ensure_ascii=False, default=str)[:300]
        messages.append({
            "role": "user",
            "content": f"用户拒绝了写操作 {name}（参数：{brief}），该操作"
                       "未执行。请如实告知用户操作已取消（不要假装已执行），"
                       "并询问接下来想怎么做。"})
        return messages
    call_id = f"call_cfm_{uuid.uuid4().hex[:10]}"
    messages.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args or {},
                                                 ensure_ascii=False)},
        }],
    })
    messages.append({"role": "tool", "tool_call_id": call_id,
                     "content": str((result or {}).get("text") or "")})
    return messages


def run_resume(session: dict, name: str, args: dict,
               result: dict | None = None,
               rejected: bool = False) -> Iterator[dict]:
    """二次确认后的续跑入口（由 /chat/confirm 调用）。

    rejected=True 时注入"用户拒绝了该操作"让助手继续对话；否则把已执行
    的 tool result 回注对话继续。续跑中再次命中写工具会再次发
    tool_confirm 并挂起（多次确认串联）。"""
    session_id = str(session.get("session_id") or "")
    messages = _resume_messages(session, name, args, result, rejected)
    try:
        yield from _run_function_calling(messages, session_id)
        return
    except FunctionCallingUnsupported as exc:
        logger.info("续跑 function calling 不可用，降级两段式: %s", exc)
    except LLMCallError as exc:
        yield {"type": "error", "message": f"LLM 调用失败：{exc}"}
        return
    except Exception as exc:
        logger.exception("agent 续跑异常")
        yield {"type": "error",
               "message": f"助手内部错误：{type(exc).__name__}: {exc}"}
        return
    yield from _run_plan_execute(messages, session_id)
