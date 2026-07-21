"""
bridge/load_context.py
======================
新会话开始时自动注入上下文: 读取最近 decisions + feedback + in_progress
输出一个可注入 system prompt 的文本

用法: python bridge/load_context.py
"""
import os
import sys
import csv
import io
import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
DECISIONS_DIR = PROJ / 'experiment' / 'decisions'


def get_recent_decisions(days=3):
    """读取最近 N 天的决策日志"""
    if not DECISIONS_DIR.exists():
        return '(无)'
    files = sorted(DECISIONS_DIR.glob('*.md'), reverse=True)
    recent = []
    today = datetime.date.today()
    for f in files:
        try:
            d = datetime.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if (today - d).days <= days:
            recent.append(f)
    if not recent:
        return '(无)'
    out = ''
    for f in reversed(recent):
        text = f.read_text(encoding='utf-8')
        out += f'\n--- {f.stem} ---\n{text}\n'
    return out


def get_feedback_summary():
    """反馈库摘要"""
    fb = PROJ / 'experiment' / 'feedback_db.csv'
    if not fb.exists():
        return '0 条'
    with open(fb, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return f'{len(rows)} 条'


def get_in_progress():
    """进行中实验"""
    ip = PROJ / 'experiment' / 'in_progress.md'
    if not ip.exists():
        return '(无)'
    return ip.read_text(encoding='utf-8')[:1000]


def get_in_progress():
    """进行中实验"""
    ip = PROJ / 'experiment' / 'in_progress.md'
    if not ip.exists():
        return '(无)'
    return ip.read_text(encoding='utf-8')[:1000]


def get_abcdef_diff():
    """巡查 ABCDEF.docx — 自动对比上次 vs 现在"""
    try:
        sys.path.insert(0, str(PROJ / 'bridge'))
        from inspect_abcdef import main as inspect
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        inspect()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        return output
    except Exception as e:
        return f'(巡查失败: {e})'


def main():
    print('=== 上下文注入 (最近 3 天决策 + 反馈库摘要 + 进行中实验 + ABCDEF巡查) ===\n')

    print('## 最近决策')
    print(get_recent_decisions(days=3))

    print(f'\n## 反馈库状态\n{get_feedback_summary()}')

    print(f'\n## 进行中实验\n{get_in_progress()[:500]}')

    print(f'\n## ABCDEF 巡查\n{get_abcdef_diff()[:1500]}')


if __name__ == '__main__':
    main()