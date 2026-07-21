"""
bridge/update_daily.py
========================
每日工作总结 + 文件同步 + git commit (不 push)
由 automation cron job 在每天 22:00 调用

输出:
  - experiment/daily/YYYY-MM-DD.md      (人类版 - 简洁)
  - experiment/daily/YYYY-MM-DD_ai.md   (AI 版 - 详细)
  - 追加 progress.md 时间戳
  - 同步 README.md / HOW_TO_FILL.md
  - git add + commit "daily: YYYY-MM-DD"
"""
import os
import sys
import json
import csv
import datetime
import subprocess
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / 'bridge'))

DAILY_DIR = PROJ / 'experiment' / 'daily'
DAILY_DIR.mkdir(parents=True, exist_ok=True)
FEEDBACK_DB = PROJ / 'experiment' / 'feedback_db.csv'
PROGRESS = PROJ / 'progress.md'
README = PROJ / 'README.md'
HOW_TO_FILL = PROJ / 'experiment' / 'HOW_TO_FILL.md'
IN_PROGRESS = PROJ / 'experiment' / 'in_progress.md'


def run_git(*args, **kw):
    r = subprocess.run(['git', '-C', str(PROJ)] + list(args),
                       capture_output=True, text=True, **kw)
    return r


def read_progress():
    if not PROGRESS.exists():
        return ''
    return PROGRESS.read_text(encoding='utf-8')


def read_in_progress():
    if not IN_PROGRESS.exists():
        return ''
    return IN_PROGRESS.read_text(encoding='utf-8')


def read_feedback_db():
    if not FEEDBACK_DB.exists():
        return []
    with open(FEEDBACK_DB, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def collect_today_state():
    """收集今日状态快照"""
    feedback = read_feedback_db()
    in_progress = read_in_progress()
    progress = read_progress()

    today = datetime.date.today().isoformat()

    # 已完成实验
    completed = [r for r in feedback if r.get('日期') and r.get('失败Class')]
    in_progress_ids = []
    # 从 in_progress.md 提取编号
    import re
    for m in re.finditer(r'\b(A\d+|D\d+|G\d+|H\d+|I\d+|J\d+|K\d+|L\d+|M\d+|N\d+)\b', in_progress):
        in_progress_ids.append(m.group(1))

    # git 状态
    status_r = run_git('status', '--short')
    git_clean = status_r.stdout.strip() == ''

    # 最近 commits
    log_r = run_git('log', '--oneline', '-10')
    recent_commits = log_r.stdout.strip()

    return {
        'date': today,
        'feedback_count': len(feedback),
        'completed_count': len(completed),
        'in_progress_ids': in_progress_ids,
        'git_clean': git_clean,
        'git_status': status_r.stdout.strip(),
        'recent_commits': recent_commits,
        'progress_lines': len(progress.split('\n')),
    }


def write_human_version(state):
    """人类版日报 — 简洁"""
    today = state['date']
    path = DAILY_DIR / f'{today}.md'
    if path.exists():
        return path  # 已存在，不覆盖

    content = f"""# {today} 工作日报

## 今日完成
- (待用户在微信/桌面补充)

## 反馈库状态
- 已完成实验: {state['completed_count']} 条
- 进行中实验: {len(state['in_progress_ids'])} 个 ({', '.join(state['in_progress_ids'][:10])}...)

## Git 状态
```
{state['git_status'][:500] or '(clean)'}
```

## 最近 10 个 commits
```
{state['recent_commits']}
```

## 待办
- (待补充)
"""
    path.write_text(content, encoding='utf-8')
    return path


def write_ai_version(state):
    """AI 版日报 — 详细 (供 LLM 上下文用)"""
    today = state['date']
    path = DAILY_DIR / f'{today}_ai.md'
    if path.exists():
        return path

    feedback = read_feedback_db()
    feedback_table = '| 编号 | 醛CAS | 胺CAS | Class | 日期 |\n|------|--------|--------|-------|------|\n'
    for r in feedback:
        feedback_table += f'| {r.get("方案编号","")} | {r.get("醛CAS","")} | {r.get("胺CAS","")} | {r.get("失败Class","")} | {r.get("日期","")} |\n'

    progress = read_progress()

    content = f"""# {today} 工作日报 (AI 版)

## 状态快照
```json
{json.dumps(state, ensure_ascii=False, indent=2)}
```

## 完整反馈库
{feedback_table}

## Progress.md (完整)
```
{progress}
```

## In-progress.md (完整)
```
{read_in_progress()}
```
"""
    path.write_text(content, encoding='utf-8')
    return path


def append_progress(state):
    """在 progress.md 追加今日时间戳"""
    today = state['date']
    if today in read_progress():
        return  # 今日已追加
    new_entry = f"""

### {today} (auto: cron 22:00)
- 反馈库: {state['feedback_count']} 条 ({state['completed_count']} 已完成)
- 进行中: {len(state['in_progress_ids'])} 个
- git 状态: {'clean' if state['git_clean'] else '有未提交'}
- 日报: `experiment/daily/{today}.md` (人类版) + `{today}_ai.md` (AI 版)
"""
    with open(PROGRESS, 'a', encoding='utf-8') as f:
        f.write(new_entry)


def git_commit_daily(state):
    """git add + commit, 不 push"""
    today = state['date']
    # 先 add 所有 daily 文件
    run_git('add', 'experiment/daily/')
    run_git('add', 'progress.md')
    # 验证 staged 有东西
    r = run_git('diff', '--cached', '--name-only')
    if not r.stdout.strip():
        return False, 'no changes to commit'
    msg = f'daily: {today} summary\n\n'
    msg += f'- 反馈库: {state["feedback_count"]} 条 ({state["completed_count"]} 已完成)\n'
    msg += f'- 进行中: {len(state["in_progress_ids"])} 个\n'
    r = run_git('commit', '-m', msg)
    if r.returncode != 0:
        return False, r.stderr
    return True, r.stdout.strip()


def main():
    state = collect_today_state()
    print(f'=== {state["date"]} 日报 ===')
    print(f'反馈: {state["feedback_count"]} 条')
    print(f'进行中: {len(state["in_progress_ids"])} 个')
    print()

    h_path = write_human_version(state)
    print(f'人类版: {h_path}')
    ai_path = write_ai_version(state)
    print(f'AI 版: {ai_path}')

    append_progress(state)
    print(f'progress.md 已追加')

    ok, msg = git_commit_daily(state)
    if ok:
        print(f'git commit OK: {msg[:100]}')
    else:
        print(f'git commit: {msg}')

    print()
    print('用户需要手动 push: git push origin master')


if __name__ == '__main__':
    main()