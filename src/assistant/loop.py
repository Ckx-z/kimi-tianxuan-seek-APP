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
"""

from __future__ import annotations

import json
import logging
from typing import Iterator

from . import context as context_module
from . import llm_bridge, persona, registry
from .llm_bridge import FunctionCallingUnsupported, LLMCallError

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5       # 工具调用轮次上限（防死循环）
_HISTORY_LIMIT = 20       # 带入的历史消息条数上限（MVP 简单截断）
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


def build_messages(session: dict, user_message: str) -> list[dict]:
    """组装 messages = [system(人格+领域规则+上下文), ...历史（截断）, user]。"""
    context_block = context_module.build_context_block(session.get("context"))
    messages = [{"role": "system",
                 "content": persona.build_system_prompt(context_block)}]
    history = (session.get("messages") or [])[-_HISTORY_LIMIT:]
    for m in history:
        if m.get("role") not in ("user", "assistant"):
            continue
        messages.append({
            "role": m["role"],
            "content": str(m.get("content") or "")[:_HISTORY_MAX_CHARS],
        })
    messages.append({"role": "user", "content": user_message})
    return messages


def _tool_event_pair(name: str, args: dict, result: dict) -> Iterator[dict]:
    """一次工具调用的两个 SSE 事件。"""
    yield {"type": "tool_call", "name": name, "args": args}
    yield {"type": "tool_result", "name": name,
           "summary": registry.summary_of(result),
           "is_error": bool(result.get("is_error"))}


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


def _run_function_calling(messages: list[dict]) -> Iterator[dict]:
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


def _sanitize_for_plain(messages: list[dict]) -> list[dict]:
    """降级路径 B 前清洗：tool 角色与 tool_calls 字段不是纯文本端点都认，
    转成 user 角色文本，assistant 的 tool_calls 字段剥掉。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "user",
                        "content": "工具返回：\n" + str(m.get("content") or "")})
        elif role == "assistant":
            out.append({"role": "assistant",
                        "content": str(m.get("content") or "")})
        else:
            out.append({"role": role, "content": str(m.get("content") or "")})
    return out


def _run_plan_execute(messages: list[dict]) -> Iterator[dict]:
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
        result = registry.execute(name, args)
        yield from _tool_event_pair(name, args, result)
        work.append({"role": "assistant", "content": text})
        work.append({
            "role": "user",
            "content": f"工具 {name} 返回：\n{result['text']}\n\n"
                       "请继续：输出下一个 JSON 指令。"})
    yield from _final_answer(work)


def run(session: dict, user_message: str) -> Iterator[dict]:
    """agent 主入口。session 为 sessions.load_session 的返回（不含本轮
    用户消息，由本函数拼入）。LLM 未配置 / 调用失败一律走 error 事件。"""
    messages = build_messages(session, user_message)
    try:
        yield from _run_function_calling(messages)
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
    yield from _run_plan_execute(messages)
