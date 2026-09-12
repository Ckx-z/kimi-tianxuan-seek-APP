"""密钥泄漏回归防线（v1.9.3）。

背景：本仓库是 **public**，而历史上曾把「GitHub PAT 的 4 字符前缀」写进日报
（不可用，但已是坏习惯）；更危险的是本地发版脚本里散落过多份明文 token。
本测试扫描 **git 已跟踪文件**，一旦出现完整形态的密钥就失败，从源头防止泄漏。

设计要点：
- 只扫已跟踪文件（`git ls-files`），未跟踪的本地脚本（gitignored）不在范围；
- 允许明显的占位/截断写法（如 `github_pat_11CB...`、`sk-xxxx`）——
  完整 token 形态（前缀 + ≥20 位字符）才会命中；
- git 不可用时跳过（不阻塞无 git 环境下的测试）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 完整密钥形态（前缀 + 足够长度的字符），占位/截断写法不会命中
SECRET_PATTERNS = [
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github_classic", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("openai_like", re.compile(r"sk-[A-Za-z0-9]{24,}")),
    ("anthropic_like", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("dashscope_like", re.compile(r"sk-[A-Za-z0-9]{20,}")),
]
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt",
                 ".yml", ".yaml", ".ps1", ".bat", ".cjs", ".mjs", ".cfg",
                 ".ini", ".toml", ".sh"}
SKIP_DIR_PARTS = {"node_modules", ".git", "dist", "dist-backend", "build",
                  "release", "__pycache__"}


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=PROJECT_ROOT,
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return []
    if out.returncode != 0:
        return []
    return [PROJECT_ROOT / line.strip() for line in out.stdout.splitlines()
            if line.strip()]


def test_no_full_secrets_in_tracked_files():
    files = _tracked_files()
    if not files:
        pytest.skip("git 不可用或无跟踪文件，跳过密钥扫描")
    hits: list[str] = []
    scanned = 0
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIR_PARTS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        scanned += 1
        for name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                rel = path.relative_to(PROJECT_ROOT)
                hits.append(f"{rel}: {name} 疑似完整密钥（{match.group(0)[:12]}…）")
    assert not hits, (
        "已在 git 跟踪文件中发现疑似完整密钥，请立即移除并轮换：\n  "
        + "\n  ".join(sorted(set(hits))))
    assert scanned > 50, f"扫描文件数异常偏少（{scanned}），检查 git ls-files 输出"


def test_gitignore_covers_local_artifacts():
    """本地发版脚本/打包产物目录必须保持 gitignore（防止 token 与巨物入库）。"""
    gi = PROJECT_ROOT / ".gitignore"
    assert gi.is_file(), ".gitignore 缺失"
    text = gi.read_text(encoding="utf-8", errors="ignore")
    for entry in ("build/", "dist-backend/", ".tmp_xtb/", "*.local.json"):
        assert entry in text, f".gitignore 缺少 {entry}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
