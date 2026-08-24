"""DFT 结果缓存：key = 规范化单体对（排序后）+ 方法档位。

缓存落在 user_data_root()/dft_cache/<sha1>.json；模块级 CACHE_DIR 供测试
monkeypatch 到 tmp 目录（与 prediction_log / favorites 的测试口径一致）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

CACHE_DIR = runtime_config.user_data_root() / "dft_cache"


def cache_key(canon_a: str, canon_b: str, method: str) -> str:
    """规范化单体对（排序，A/B 无序）+ 方法 → sha1。"""
    pair = "|".join(sorted([canon_a, canon_b]))
    return hashlib.sha1(f"{pair}::{method}".encode()).hexdigest()


def load_cache(key: str) -> dict | None:
    """命中返回缓存结果 dict（自动补 cached=True 由调用方处理）；否则 None。"""
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("DFT 缓存读取失败 %s: %s", path, exc)
        return None


def save_cache(key: str, result: dict) -> None:
    """写缓存；任何失败静默（缓存不是主流程）。"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("DFT 缓存写入失败: %s", exc)
