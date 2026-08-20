"""科研助手 LLM 桥接：function calling（路径 A）+ 两段式降级（路径 B）。

纪律（继承 openhanako 的 SDK 门面思路）：所有 LLM 调用收敛到
src/llm/client.py 门面，密钥只存在于其配置链内，本模块不打印、不落盘。

路径 A（function calling）：client.chat_completion 签名已钉死不支持 tools，
故本模块复用 client 的配置解析与超时口径，自行组装带 tools 的 OpenAI 兼容
请求；端点不支持 / 报错 / 返回格式无法识别时抛 ``FunctionCallingUnsupported``
由 loop 降级到路径 B。

路径 B（两段式计划-执行）：直接调 client.chat_completion（测试打桩点），
模型按提示词输出 JSON 指令。longcat 为推理型模型：max_tokens 给足 +
extra_body 关闭 thinking（同 src/utils/cas_lookup.py 的实测做法）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests

try:
    from src.llm import client as llm_client
except ImportError:  # pragma: no cover
    from llm import client as llm_client  # type: ignore

logger = logging.getLogger(__name__)

# 推理型模型（longcat）：给足输出预算并关闭 thinking，避免 token 被推理耗尽
AGENT_MAX_TOKENS = 4000
AGENT_TEMPERATURE = 0.3
_THINKING_OFF = {"thinking": {"type": "disabled"}}


class FunctionCallingUnsupported(Exception):
    """端点不支持 tools 参数，或返回的 tool_calls 格式无法识别（→ 降级路径 B）。"""


class LLMCallError(Exception):
    """调用本身失败（网络 / 超时 / 5xx / 响应结构缺失）。不应触发降级重试。"""


def is_configured() -> bool:
    """透传门面配置状态（status 端点与 chat 前置检查用）。"""
    return llm_client.is_configured()


def chat_text(messages: list,
              max_tokens: int = AGENT_MAX_TOKENS,
              temperature: float = AGENT_TEMPERATURE) -> Optional[str]:
    """路径 B 纯文本调用。未配置 / 失败返回 None（门面语义，不抛异常）。"""
    return llm_client.chat_completion(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=dict(_THINKING_OFF),
    )


def _normalize_tool_calls(message: dict) -> list[dict]:
    """把 OpenAI tool_calls 规范化为 [{id, name, args(dict)}]。

    任何结构缺失 / arguments JSON 解析失败都抛 FunctionCallingUnsupported
    （格式乱 → 交给两段式的容错解析更稳）。
    """
    raw_calls = message.get("tool_calls")
    if not raw_calls:
        return []
    if not isinstance(raw_calls, list):
        raise FunctionCallingUnsupported("tool_calls 不是列表")
    out: list[dict] = []
    for i, tc in enumerate(raw_calls):
        if not isinstance(tc, dict):
            raise FunctionCallingUnsupported(f"tool_calls[{i}] 不是对象")
        fn = tc.get("function") or {}
        name = fn.get("name")
        if not name or not isinstance(name, str):
            raise FunctionCallingUnsupported(f"tool_calls[{i}] 缺 function.name")
        args_raw = fn.get("arguments", "{}")
        if isinstance(args_raw, dict):
            args = args_raw
        elif isinstance(args_raw, str):
            try:
                args = json.loads(args_raw or "{}")
            except Exception as exc:
                raise FunctionCallingUnsupported(
                    f"tool_calls[{i}].arguments 不是合法 JSON: {exc}")
            if not isinstance(args, dict):
                raise FunctionCallingUnsupported(
                    f"tool_calls[{i}].arguments 不是 JSON 对象")
        else:
            raise FunctionCallingUnsupported(
                f"tool_calls[{i}].arguments 类型无法识别")
        out.append({
            "id": str(tc.get("id") or f"call_{i}"),
            "name": name,
            "args": args,
        })
    return out


def chat_completion_with_tools(messages: list, tools: list,
                               max_tokens: int = AGENT_MAX_TOKENS,
                               temperature: float = AGENT_TEMPERATURE
                               ) -> dict:
    """路径 A：OpenAI function calling。

    返回 {"content": str | None, "tool_calls": [{id, name, args}]}。
    未配置 → LLMCallError；端点拒绝 tools（4xx）/ 返回格式乱 →
    FunctionCallingUnsupported；网络 / 超时 / 5xx → LLMCallError。
    """
    cfg = llm_client._resolve()  # 门面内部配置解析（密钥不出此模块）
    if not cfg:
        raise LLMCallError("LLM 未配置")

    payload: dict[str, Any] = {
        "model": cfg.get("model") or "",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "tools": tools,
        "tool_choice": "auto",
    }
    payload.update(_THINKING_OFF)

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=llm_client.TIMEOUT,
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise LLMCallError(f"LLM 连接失败: {type(exc).__name__}") from exc
    except requests.RequestException as exc:
        raise LLMCallError(f"LLM 请求失败: {type(exc).__name__}") from exc

    if resp.status_code in (400, 404, 405, 422):
        # 端点不认识 tools / tool_choice 参数的典型表现
        raise FunctionCallingUnsupported(
            f"端点拒绝 tools 请求（HTTP {resp.status_code}），降级两段式")
    if resp.status_code != 200:
        raise LLMCallError(f"LLM 响应 HTTP {resp.status_code}")

    try:
        data = resp.json()
        message = (data.get("choices") or [{}])[0].get("message") or {}
    except Exception as exc:
        raise LLMCallError(f"LLM 响应解析失败: {type(exc).__name__}") from exc

    content = message.get("content")
    if isinstance(content, list):  # 分片列表兼容（同 client._http_chat 口径）
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    tool_calls = _normalize_tool_calls(message)
    if (content is None or (isinstance(content, str) and not content.strip())) \
            and not tool_calls:
        raise FunctionCallingUnsupported(
            "响应既无 content 也无 tool_calls（格式无法识别），降级两段式")
    return {"content": content, "tool_calls": tool_calls}
