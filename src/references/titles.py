"""文献标题映射查询（P3 后端，收藏夹文献/页⑤依据回显支撑）。

数据源 data/paper_titles.json：{paper_id: {"title": ..., "doi": ...}}，
由 scripts/build_paper_titles.py 从旧项目结构化文献库
（tianxuan seek/data/structured_v2 + structured_v3，只读）批量构建，
共 1711 篇（structured / structured_new / structured_new3 的 YAML
无 title/doi 字段，未纳入）。

frozen（PyInstaller onedir）打包兼容 —— overlay 策略：
内置库在 _internal 目录（只读语义），打包后的 confirm/backfill 写入会失败，
因此读写分离：
- 读：user_data_root()/literature/paper_titles.json（用户库）存在则用它，
  否则用打包/源码内置库；
- 写（append_paper / backfill / confirm）：永远写用户库；用户库不存在时
  先全量复制内置库再追加（writable_titles_path 负责 copy-on-first-write）。
- 源码开发态（非 frozen 且未设 COF_DATA_DIR）行为不变：直接读写
  data/paper_titles.json。

首次查询时惰性加载并缓存；映射表缺失/损坏时所有查询安全返回 None。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()

# 内置库（打包资源 / 源码 data/ 下；frozen 时只读语义，绝不可写）
BUNDLED_PATH = PROJECT_ROOT / "data" / "paper_titles.json"

# 显式覆盖路径（测试/脚本 monkeypatch 用）：非 None 时读写都用它，
# overlay 逻辑整体旁路。默认 None = 按 overlay 规则动态解析。
TITLES_PATH: Path | None = None

_cache: dict[str, dict] | None = None


def user_titles_path() -> Path:
    """用户可写库路径：user_data_root()/literature/paper_titles.json。

    动态解析（每次调用重读环境），COF_DATA_DIR / frozen 语义变化即时生效。
    """
    return runtime_config.user_data_root() / "literature" / "paper_titles.json"


def _overlay_active() -> bool:
    """overlay 是否生效：frozen 打包态，或显式指定了 COF_DATA_DIR（分发语义）。

    源码开发态（二者皆无）返回 False —— 直接读写内置库，行为与历史上一致。
    """
    if runtime_config.is_frozen():
        return True
    return bool(os.environ.get("COF_DATA_DIR", "").strip())


def titles_path() -> Path:
    """当前生效的文献库【读】路径。

    优先级：显式覆盖（TITLES_PATH）> 用户库（overlay 生效且文件存在）> 内置库。
    用户库在首次写入后才出现，因此本函数每次调用都重新判断（不靠缓存路径，
    缓存的只是内容，写后 reload 即按新路径重载）。
    """
    if TITLES_PATH is not None:
        return Path(TITLES_PATH)
    user = user_titles_path()
    if _overlay_active() and user.exists():
        return user
    return Path(BUNDLED_PATH)


def writable_titles_path() -> Path:
    """当前生效的文献库【写】路径（copy-on-first-write）。

    - 显式覆盖：直接用（测试 monkeypatch 一处即生效，同历史行为）；
    - overlay 未生效（源码开发态）：写内置库（历史行为不变）；
    - overlay 生效：永远写用户库；用户库不存在时先全量复制内置库
      （内置库缺失则从空库开始），保证追加不丢既有条目。
    """
    if TITLES_PATH is not None:
        return Path(TITLES_PATH)
    if not _overlay_active():
        return Path(BUNDLED_PATH)
    user = user_titles_path()
    if not user.exists():
        user.parent.mkdir(parents=True, exist_ok=True)
        bundled = Path(BUNDLED_PATH)
        if bundled.exists():
            shutil.copy2(bundled, user)
            logger.info("文献库首次写入：已复制内置库到用户库 %s", user)
        else:
            user.write_text("{}\n", encoding="utf-8")
            logger.warning("内置文献库 %s 缺失，用户库从空库开始: %s", bundled, user)
    return user


def _read(path: Path) -> dict[str, dict]:
    """读单个文献库文件；缺失/损坏返回空表。"""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:
        logger.warning("文献标题映射加载失败 %s: %s", path, exc)
        return {}


def _load() -> dict[str, dict]:
    """加载映射表（带缓存）；缺失/损坏返回空表。

    overlay 生效且用户库存在时为**逐条合并**（内置库打底、用户库覆盖/新增），
    而非整库替换——未来版本升级包内内置库新增条目时，已产生用户库的
    老用户仍能看到新条目（2026-08-26 合并策略修正）。
    """
    global _cache
    if _cache is not None:
        return _cache
    if TITLES_PATH is not None:
        _cache = _read(Path(TITLES_PATH))
        return _cache
    base = _read(Path(BUNDLED_PATH))
    user = user_titles_path()
    if _overlay_active() and user.exists():
        merged = dict(base)
        merged.update(_read(user))
        _cache = merged
    else:
        _cache = base
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
