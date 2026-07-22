"""统一 LLM 基座（OpenAI 兼容客户端）。

对外签名（任务 C/D 依赖，勿改）：
    is_configured() -> bool
    chat_completion(messages, max_tokens=800, temperature=0.3, nocache=False) -> str | None
    get_settings() -> dict
    save_settings(base_url, api_key, model) -> None
    test_connection() -> tuple[bool, str]
"""

from src.llm.client import (
    chat_completion,
    get_settings,
    is_configured,
    save_settings,
    test_connection,
)

__all__ = [
    "chat_completion",
    "get_settings",
    "is_configured",
    "save_settings",
    "test_connection",
]
