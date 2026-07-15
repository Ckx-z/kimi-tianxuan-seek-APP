"""
bridge/save_session.py
======================
会话结束时自动落盘决策: 生成 experiment/decisions/{date}.md
由用户手动触发或 AI 在对话结尾调用

用法: python bridge/save_session.py "今天做了什么"
"""
import sys
import os
import datetime
import subprocess
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
DECISIONS_DIR = PROJ / 'experiment' / 'decisions'
DECISIONS_DIR.mkdir(parents=True, exist_ok=True)


def extract_context():
    """收集当前状态快照"""
    import csv
    fb = PROJ / 'experiment' / 'feedback_db.csv'
    fb_count = 0
    if fb.exists():
        with open(fb, encoding='utf-8-sig') as f:
            fb_count = sum(1 for _ in csv.reader(f)) - 1

    ip = PROJ / 'experiment' / 'in_progress.md'
    in_progress = ip.read_text(encoding='utf-8')[:500] if ip.exists() else '(无)'

    r = subprocess.run(['git', '-C', str(PROJ), 'log', '--oneline', '-5'],
                       capture_output=True, text=True)
    git_log = r.stdout.strip()

    return f"""## 状态快照
- 反馈库: {fb_count} 条
- 最近 git commits:
```
{git_log}
```"""


def save(title, content):
    """生成 decisions/{date}.md"""
    today = datetime.date.today().isoformat()
    fp = DECISIONS_DIR / f'{today}.md'

    header = f'# {today} 决策日志\n\n> 会话标题: {title}\n\n'
    full = header + content + '\n\n' + extract_context()

    fp.write_text(full, encoding='utf-8')
    print(f'✓ 决策已落盘: {fp}')
    return fp


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python bridge/save_session.py "<会话标题>" "<内容>"')
        sys.exit(1)
    title = sys.argv[1]
    content = sys.argv[2] if len(sys.argv) > 2 else '(无)'
    save(title, content)