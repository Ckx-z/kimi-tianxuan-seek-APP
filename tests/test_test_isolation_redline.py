"""隔离红线：测试**绝不允许**写入仓库真实数据目录 / 随包数据。

事故背景（发生过两次，都很贵）：
1. v1.9.8 前：13 个用例长期写仓库 `data/paper_titles.json`（随包库），
   一度把 3 篇测试文献冻进安装包；
2. v2.0.1：`literature.resolver.INTAKE_PATH` 等**导入期常量**未被 conftest 刷新，
   跑一次 autobackfill 测试就往仓库 `data/literature/literature_intake.jsonl`
   追加审计行，并把占位文献 mirror 进真实 `library.db`（覆盖了 paper 1/2 的标题），
   审计流水累积 335 行全是测试垃圾。

本文件是**红线**：把「写盘路径必须落在隔离目录内」变成可执行的断言，
以后新增模块只要又用了导入期常量、conftest 没覆盖，这里就会红。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA = (PROJECT_ROOT / "data").resolve()

#: (模块名, 常量名) —— 会被写入的导入期路径常量
WRITE_PATHS: list[tuple[str, str]] = [
    ("literature.resolver", "INTAKE_PATH"),
    ("literature.figures", "FIGURES_DIR"),
    ("literature.figures", "INDEX_PATH"),
    ("literature.pdf_figures", "STAGING_DIR"),
    ("literature.knowledge", "ENTRIES_PATH"),
    ("literature.attachments", "PDFS_DIR"),
    ("literature.attachments", "INDEX_PATH"),
    ("literature.embedding", "EMB_PATH"),
]


def _import(mod_name: str):
    try:
        __import__(mod_name)
    except ImportError:
        try:
            __import__(f"src.{mod_name}")
            mod_name = f"src.{mod_name}"
        except ImportError:
            return None
    return sys.modules.get(mod_name)


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path = path.resolve()
    except Exception:  # pragma: no cover
        return False
    return path == parent or parent in path.parents


@pytest.mark.parametrize("mod_name,attr", WRITE_PATHS)
def test_literature_write_path_isolated(mod_name, attr):
    mod = _import(mod_name)
    if mod is None or not hasattr(mod, attr):
        pytest.skip(f"{mod_name}.{attr} 不存在（可选模块）")
    p = Path(getattr(mod, attr))
    assert not _is_inside(p, REAL_DATA), (
        f"{mod_name}.{attr} 仍指向仓库真实数据目录：{p}\n"
        "→ 请在 tests/conftest.py::_refresh_module_level_paths() 里补上该常量，"
        "否则这个用例跑起来会污染真实数据（历史事故见文件头注释）")


def test_repo_bundled_library_is_untouched_by_session():
    """随包库 `data/paper_titles.json` 在测试会话里必须是只读的（写盘走 overlay）。"""
    from src.literature import resolver  # noqa: PLC0415

    papers = Path(resolver._papers_path()) if hasattr(resolver, "_papers_path") else None
    if papers is None:
        pytest.skip("resolver._papers_path 不存在")
    assert not _is_inside(Path(papers).resolve(), REAL_DATA), (
        f"resolver._papers_path() 指向随包库：{papers}（测试会写坏随包数据）")
