"""文献标题映射查询（P3 后端，收藏夹文献/页⑤依据回显支撑）。

数据源 data/paper_titles.json：{paper_id: {"title": ..., "doi": ...}}，
由 scripts/build_paper_titles.py 从旧项目结构化文献库
（tianxuan seek/data/structured_v2 + structured_v3，只读）批量构建，
共 1711 篇（structured / structured_new / structured_new3 的 YAML
无 title/doi 字段，未纳入）。

首次查询时惰性加载并缓存；映射表缺失/损坏时所有查询安全返回 None。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TITLES_PATH = PROJECT_ROOT / "data" / "paper_titles.json"

_cache: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    """加载映射表（带缓存）；缺失/损坏返回空表。"""
    global _cache
    if _cache is not None:
        return _cache
    try:
        obj = json.loads(TITLES_PATH.read_text(encoding="utf-8"))
        _cache = obj if isinstance(obj, dict) else {}
    except Exception as exc:
        logger.warning("文献标题映射加载失败 %s: %s", TITLES_PATH, exc)
        _cache = {}
    return _cache


def reload() -> None:
    """清缓存强制下次查询重载（测试/映射表更新后用）。"""
    global _cache
    _cache = None


def resolve_entry(paper_id) -> dict | None:
    """按 paper_id 取 {"title":..., "doi":...}；缺失返回 None。"""
    if paper_id is None:
        return None
    entry = _load().get(str(paper_id).strip())
    return dict(entry) if isinstance(entry, dict) else None


def resolve_title(paper_id) -> str | None:
    """按 paper_id 取文献标题；缺失/无标题返回 None。"""
    entry = resolve_entry(paper_id)
    if entry is None:
        return None
    title = str(entry.get("title") or "").strip()
    return title or None
