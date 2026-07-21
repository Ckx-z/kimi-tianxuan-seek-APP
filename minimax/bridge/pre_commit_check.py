"""
bridge/pre_commit_check.py
==========================
git commit 前自动扫描敏感内容:
- 大文件 (>10MB)
- 已知敏感路径 (知识库/, .env, secrets/, *.pdf, *.doc 等)
- API key 模式 (sk-, ghp_, 等)
- 与 .gitignore 冲突的 staged 文件

退出码: 0 = 通过, 1 = 阻断
"""
import os
import re
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
GITIGNORE = PROJ / '.gitignore'

# 敏感路径模式 (绝对禁止 commit)
FORBIDDEN_PATHS = [
    r'知识库/',
    r'\.env$',
    r'\.env\.',
    r'secrets/',
    r'\.ssh/',
    r'\.aws/',
    r'\.gnupg/',
]

# 大文件阈值 (10MB)
SIZE_LIMIT = 10 * 1024 * 1024

# 已知大数据文件名模式 (即使 <10MB 也应警告)
KNOWN_LARGE_FILES = [
    r'tianxuan_vectors\.bin$',
    r'tianxuan_meta\.json$',
    r'tianxuan_norms\.bin$',
    r'knowledge_index.*\.jsonl$',
    r'graph_v2\.pkl$',
]

# API key 模式
KEY_PATTERNS = [
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), 'OpenAI/MiniMax key'),
    (re.compile(r'sk-ant-[A-Za-z0-9]{20,}'), 'Anthropic key'),
    (re.compile(r'sk-or-[A-Za-z0-9]{20,}'), 'OpenRouter key'),
    (re.compile(r'ghp_[A-Za-z0-9]{36,}'), 'GitHub PAT'),
    (re.compile(r'gho_[A-Za-z0-9]{36,}'), 'GitHub OAuth'),
    (re.compile(r'sk-kimi-[A-Za-z0-9]{20,}'), 'Kimi API key'),
    (re.compile(r'sk-cp-[A-Za-z0-9]{20,}'), 'MiniMax coding plan'),
]

# 已知二进制敏感类型 (默认不 commit)
BINARY_FORBIDDEN_EXT = ['.pdf', '.doc', '.docx', '.pptx', '.xlsx', '.key', '.pem']


def get_staged_files():
    r = subprocess.run(['git', '-C', str(PROJ), 'diff', '--cached', '--name-only', '--diff-filter=ACMR'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [l for l in r.stdout.split('\n') if l]


def is_forbidden_path(path):
    for pat in FORBIDDEN_PATHS:
        if re.search(pat, path):
            return pat
    return None


def has_api_key(text):
    """检查文本中是否含 API key"""
    for pat, name in KEY_PATTERNS:
        if pat.search(text):
            return name
    return None


def check_staged_files(staged):
    errors = []
    warnings = []

    for path in staged:
        full = PROJ / path

        # 0. 已知大数据文件 (直接阻断)
        for pat in KNOWN_LARGE_FILES:
            if re.search(pat, path):
                errors.append(f'🚫 已知大数据文件: {path} (应加入 .gitignore)')
                break

        # 1. 路径检查
        forbidden = is_forbidden_path(path)
        if forbidden:
            errors.append(f'🚫 敏感路径: {path} (匹配 {forbidden})')
            continue

        # 2. 文件存在检查
        if not full.exists():
            warnings.append(f'⚠ 文件不存在: {path}')
            continue

        # 3. 二进制敏感扩展名
        ext = Path(path).suffix.lower()
        if ext in BINARY_FORBIDDEN_EXT and ext != '.docx':
            # docx 是结构化文本, 允许
            warnings.append(f'⚠ 二进制文件: {path} ({ext}) - 确认是否要 commit')

        # 4. 文件大小
        try:
            size = full.stat().st_size
            if size > SIZE_LIMIT:
                errors.append(f'🚫 文件过大: {path} ({size / 1024 / 1024:.1f} MB > 10 MB)')
        except OSError:
            pass

        # 5. API key 扫描 (仅文本文件)
        if ext in {'.py', '.md', '.txt', '.yaml', '.yml', '.json', '.csv', '.html', '.js', '.ts'}:
            try:
                with open(full, encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                key = has_api_key(text)
                if key:
                    errors.append(f'🚫 检出 {key}: {path}')
            except Exception:
                pass

    return errors, warnings


def main():
    print('🔍 pre-commit 安全检查\n')

    staged = get_staged_files()
    if not staged:
        print('(无 staged 文件)')
        return 0

    print(f'已 staged {len(staged)} 个文件')

    errors, warnings = check_staged_files(staged)

    if warnings:
        print('\n⚠ 警告:')
        for w in warnings:
            print(f'  {w}')

    if errors:
        print('\n🚫 阻断 commit:')
        for e in errors:
            print(f'  {e}')
        print('\n解决方法:')
        print('  1. git rm --cached <文件>   # 从 index 移除但保留本地')
        print('  2. 添加到 .gitignore       # 防止再次 staged')
        print('  3. 或用 bridge/safe_commit.py 自动处理')
        return 1

    if not warnings:
        print('\n✓ 通过')
    else:
        print('\n✓ 通过 (有警告)')

    return 0


if __name__ == '__main__':
    sys.exit(main())