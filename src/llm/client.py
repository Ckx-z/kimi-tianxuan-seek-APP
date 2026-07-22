"""OpenAI 兼容统一 LLM 客户端。

配置链（优先级从高到低）：
1. ``config/llm_settings.local.json``（gitignored，由 ``save_settings`` 写入）
2. 环境变量 ``COF_LLM_BASE_URL`` / ``COF_LLM_API_KEY`` / ``COF_LLM_MODEL``
3. ``minimax/config/secrets.local.json`` 的 longcat 条目（只读默认种子，让已配
   用户开箱即用）

红线：API key 绝不打印、绝不回显、绝不写入任何入库文件。
缓存：``chat_completion`` 按 (model, temperature, max_tokens, messages) 哈希缓存到
``data/llm_cache/``（gitignored）；命中免调用；``nocache=True`` 跳过缓存（调试用）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

import requests

ROOT = Path(__file__).resolve().parents[2]
LOCAL_SETTINGS = ROOT / "config" / "llm_settings.local.json"
MINIMAX_SECRETS = ROOT / "minimax" / "config" / "secrets.local.json"
CACHE_DIR = ROOT / "data" / "llm_cache"

TIMEOUT = 120  # seconds（推理型模型长输出需要更长等待）


# ---------------------------------------------------------------------------
# 配置链
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _from_local() -> dict:
    d = _read_json(LOCAL_SETTINGS)
    if d.get("base_url") and d.get("api_key"):
        return {
            "base_url": d.get("base_url"),
            "api_key": d.get("api_key"),
            "model": d.get("model") or "",
            "source": "local_settings",
        }
    return {}


def _from_env() -> dict:
    base_url = os.environ.get("COF_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("COF_LLM_API_KEY", "").strip()
    model = os.environ.get("COF_LLM_MODEL", "").strip()
    if base_url and api_key:
        return {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "source": "env",
        }
    return {}


def _from_longcat_seed() -> dict:
    """只读 minimax secrets.local.json 的 longcat 条目作为默认种子。"""
    d = _read_json(MINIMAX_SECRETS)
    longcat = (d.get("providers") or {}).get("longcat") or {}
    base_url = (longcat.get("base_url") or "").strip()
    api_key = (longcat.get("api_key") or "").strip()
    model = (longcat.get("chat_model") or "").strip()
    if base_url and api_key:
        return {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "source": "longcat_seed",
        }
    return {}


def _resolve() -> dict:
    """按优先级解析当前生效配置；未配置返回 {}。"""
    for loader in (_from_local, _from_env, _from_longcat_seed):
        cfg = loader()
        if cfg:
            return cfg
    return {}


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) >= 8:
        return key[:4] + "***" + key[-4:]
    return "***"


# ---------------------------------------------------------------------------
# 公共 API（签名钉死，任务 C/D 依赖）
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """是否已有可用配置（base_url + api_key）。"""
    return bool(_resolve())


def get_settings() -> dict:
    """返回当前生效设置（api_key 掩码，绝不回显原文）。"""
    cfg = _resolve()
    if not cfg:
        return {
            "configured": False,
            "base_url": "",
            "model": "",
            "api_key_masked": "",
            "source": "",
        }
    return {
        "configured": True,
        "base_url": cfg["base_url"],
        "model": cfg.get("model", ""),
        "api_key_masked": _mask(cfg["api_key"]),
        "source": cfg["source"],
    }


def save_settings(base_url: str, api_key: str, model: str) -> None:
    """写入 config/llm_settings.local.json（gitignored，密钥只落这里）。"""
    LOCAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "base_url": (base_url or "").strip(),
        "api_key": (api_key or "").strip(),
        "model": (model or "").strip(),
    }
    LOCAL_SETTINGS.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _http_chat(cfg: dict, messages: list, max_tokens: int, temperature: float) -> str:
    """底层 HTTP 调用（测试时 monkeypatch 此函数，不依赖真实网络）。"""
    payload = {
        "model": cfg.get("model") or "",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    resp = requests.post(
        _chat_url(cfg["base_url"]),
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    message = data["choices"][0].get("message") or {}
    content = message.get("content")
    # 部分端点把文本放在顶层 content，或 content 为分片列表
    if content is None:
        content = data.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if content is None:
        raise ValueError("响应中无 content 字段（可能 max_tokens 被推理消耗殆尽）")
    if isinstance(content, str) and not content.strip():
        raise ValueError("响应 content 为空（推理模型需更大 max_tokens）")
    return content


def _cache_key(cfg: dict, messages: list, max_tokens: int, temperature: float) -> str:
    blob = json.dumps(
        {
            "model": cfg.get("model") or "",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def chat_completion(
    messages: list,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    nocache: bool = False,
) -> Optional[str]:
    """OpenAI 兼容 chat 调用。

    未配置或调用失败时返回 None（绝不抛异常）。命中缓存免调用；
    ``nocache=True`` 跳过缓存读写（调试用）。
    """
    cfg = _resolve()
    if not cfg:
        return None

    key = _cache_key(cfg, messages, max_tokens, temperature)
    cache_file = CACHE_DIR / f"{key}.json"

    if not nocache and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            content = cached.get("content")
            if isinstance(content, str):
                return content
        except Exception:
            pass

    try:
        content = _http_chat(cfg, messages, max_tokens, temperature)
    except Exception:
        return None

    if not isinstance(content, str):
        return None

    if not nocache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"content": content}, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass  # 缓存失败不影响主流程

    return content


def test_connection() -> tuple:
    """最小调用验证连通性，返回 (成功与否, 信息)。不抛异常。"""
    cfg = _resolve()
    if not cfg:
        return False, "未配置 LLM（请在设置页填写 base_url / api_key / model）"
    try:
        content = _http_chat(
            cfg,
            [{"role": "user", "content": "ping，请只回复 pong"}],
            max_tokens=256,
            temperature=0.0,
        )
        if isinstance(content, str) and content.strip():
            return True, f"连接成功（{cfg.get('model') or '默认模型'}）：{content.strip()[:50]}"
        return False, "连接成功但响应为空"
    except Exception as exc:  # 注意：异常信息不含密钥（密钥只在 header）
        return False, f"连接失败：{type(exc).__name__}: {exc}"
