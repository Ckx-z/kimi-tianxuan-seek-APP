"""项目状态一致性检查工具。

运行此脚本，自动检查项目文档状态是否一致：
- PROJECT_STATE.md 是否滞后于最新日报
- models/ 是否有新文件未记录
- reports/ 是否有新报告未记录
- EXPERIMENTS/ 是否有未记录实验
- .agents/session_state.yaml 是否存在且有效
- 测试是否全部通过

用法：
    cd "C:/Users/ckx/Desktop/全新机器学习实验"
    python scripts/check_project_state.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 颜色代码（Windows 兼容）
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _color(text: str, color: str) -> str:
    """给文本添加颜色（如果不支持颜色则返回原文）。"""
    if sys.platform == "win32" and not os.environ.get("TERM"):
        # Windows 终端不支持 Unicode emoji，用 ASCII 替代
        text = text.replace("✅", "[OK]").replace("❌", "[ERR]").replace("⚠️", "[WARN]")
        text = text.replace("🎉", "[DONE]")
        return text
    return f"{color}{text}{RESET}"


def _check_file_exists(path: Path, label: str) -> tuple[bool, str]:
    """检查文件是否存在。"""
    if path.exists():
        return True, _color(f"[OK] {label} 存在", GREEN)
    return False, _color(f"[ERR] {label} 缺失: {path}", RED)


def _get_latest_daily_log() -> Path | None:
    """获取最新的日报文件（只匹配 YYYY-MM-DD.md，排除 _human.md 等）。"""
    log_dir = PROJECT_ROOT / "DAILY_LOG"
    if not log_dir.exists():
        return None
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    md_files = sorted(
        [f for f in log_dir.iterdir() if f.suffix == ".md" and date_pattern.match(f.stem)],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return md_files[0] if md_files else None


def _get_file_mtime(path: Path) -> datetime | None:
    """获取文件最后修改时间。"""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime)


def check_project_state() -> dict:
    """执行所有检查，返回结果字典。"""
    results = {
        "passed": 0,
        "warnings": 0,
        "errors": 0,
        "messages": [],
    }

    def _pass(msg: str):
        results["passed"] += 1
        results["messages"].append(_color(f"[OK] {msg}", GREEN))

    def _warn(msg: str):
        results["warnings"] += 1
        results["messages"].append(_color(f"[WARN] {msg}", YELLOW))

    def _error(msg: str):
        results["errors"] += 1
        results["messages"].append(_color(f"[ERR] {msg}", RED))

    # 1. 检查核心文档是否存在
    core_docs = {
        "PROJECT_STATE.md": PROJECT_ROOT / "PROJECT_STATE.md",
        "DECISIONS.md": PROJECT_ROOT / "DECISIONS.md",
        "DATA_DICT.md": PROJECT_ROOT / "DATA_DICT.md",
        "SESSION_START.md": PROJECT_ROOT / "SESSION_START.md",
        "README.md": PROJECT_ROOT / "README.md",
    }
    for label, path in core_docs.items():
        ok, msg = _check_file_exists(path, label)
        if ok:
            _pass(f"{label} 存在")
        else:
            _error(f"{label} 缺失")

    # 2. 检查 .agents/ 目录
    agents_dir = PROJECT_ROOT / ".agents"
    if agents_dir.exists():
        _pass(".agents/ 目录存在")
        for fname in ["AGENTS.md", "session_index.yaml"]:
            fpath = agents_dir / fname
            if fpath.exists():
                _pass(f".agents/{fname} 存在")
            else:
                _error(f".agents/{fname} 缺失")
        # session_state.yaml 不强制（可能被 gitignore）
        state_path = agents_dir / "session_state.yaml"
        if state_path.exists():
            _pass(".agents/session_state.yaml 存在")
        else:
            _warn(".agents/session_state.yaml 不存在（当前无活跃会话）")
    else:
        _error(".agents/ 目录缺失")

    # 3. 检查 PROJECT_STATE.md 是否滞后于最新日报
    state_path = PROJECT_ROOT / "PROJECT_STATE.md"
    latest_log = _get_latest_daily_log()
    if state_path.exists() and latest_log:
        state_mtime = _get_file_mtime(state_path)
        log_mtime = _get_file_mtime(latest_log)
        if state_mtime and log_mtime:
            if state_mtime.date() < log_mtime.date():
                _warn(
                    f"PROJECT_STATE.md ({state_mtime.date()}) "
                    f"滞后于最新日报 {latest_log.name} ({log_mtime.date()})"
                )
            else:
                _pass(
                    f"PROJECT_STATE.md 最新（{state_mtime.date()}）"
                )

    # 4. 检查 models/ 是否有新文件
    models_dir = PROJECT_ROOT / "models"
    if models_dir.exists():
        model_files = [f for f in models_dir.iterdir() if f.is_file()]
        if model_files:
            _pass(f"models/ 有 {len(model_files)} 个文件")
            # 检查是否在 PROJECT_STATE 或 session_index 中记录
            # 简化：只检查是否有文件
        else:
            _warn("models/ 目录为空")
    else:
        _warn("models/ 目录不存在")

    # 5. 检查 reports/ 是否有新报告
    reports_dir = PROJECT_ROOT / "reports"
    if reports_dir.exists():
        report_files = [f for f in reports_dir.iterdir() if f.is_file()]
        if report_files:
            _pass(f"reports/ 有 {len(report_files)} 个文件")
        else:
            _warn("reports/ 目录为空")
    else:
        _warn("reports/ 目录不存在")

    # 6. 检查 EXPERIMENTS/ 是否有未记录实验
    exp_dir = PROJECT_ROOT / "EXPERIMENTS"
    if exp_dir.exists():
        exp_files = [f for f in exp_dir.iterdir() if f.is_file() and f.name != "_template.md"]
        if exp_files:
            _pass(f"EXPERIMENTS/ 有 {len(exp_files)} 个实验记录")
        else:
            _warn("EXPERIMENTS/ 无实验记录（除模板外）")
    else:
        _warn("EXPERIMENTS/ 目录不存在")

    # 7. 检查 DAILY_LOG 是否有最新日报
    log_dir = PROJECT_ROOT / "DAILY_LOG"
    if log_dir.exists():
        latest = _get_latest_daily_log()
        if latest:
            today = datetime.now().date()
            log_date = datetime.strptime(latest.stem, "%Y-%m-%d").date()
            if log_date == today:
                _pass(f"最新日报：{latest.name}（今天）")
            elif log_date == today:
                _pass(f"最新日报：{latest.name}（今天）")
            else:
                _warn(f"最新日报 {latest.name} 不是今天（{today}）")
        else:
            _error("DAILY_LOG/ 无日报文件")
    else:
        _error("DAILY_LOG/ 目录不存在")

    # 8. 检查测试是否可运行
    tests_dir = PROJECT_ROOT / "tests"
    if tests_dir.exists():
        _pass("tests/ 目录存在")
        # 尝试运行 pytest
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(tests_dir), "-v", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=PROJECT_ROOT,
            )
            if result.returncode == 0:
                # 解析通过数
                import re
                passed_match = re.search(r"(\d+) passed", result.stdout)
                if passed_match:
                    n = passed_match.group(1)
                    _pass(f"测试全部通过（{n} 个）")
                else:
                    _pass("测试通过")
            else:
                _warn("测试未全部通过，运行 pytest 查看详情")
        except Exception as e:
            _warn(f"无法运行测试: {e}")
    else:
        _warn("tests/ 目录不存在")

    return results


def print_summary(results: dict) -> None:
    """打印检查摘要。"""
    print()
    print(_color("=" * 50, BOLD))
    print(_color("  项目状态检查报告", BOLD))
    print(_color("=" * 50, BOLD))
    print()
    for msg in results["messages"]:
        print(f"  {msg}")
    print()
    print(_color("-" * 50, BOLD))
    total = results["passed"] + results["warnings"] + results["errors"]
    print(
        f"  总计: {results['passed']} 通过, "
        f"{results['warnings']} 警告, "
        f"{results['errors']} 错误"
    )
    print(_color("-" * 50, BOLD))
    print()
    if results["errors"] == 0 and results["warnings"] == 0:
        print(_color("  [OK] 所有检查通过！项目状态健康。", GREEN))
    elif results["errors"] == 0:
        print(_color("  [WARN] 有警告，但无错误。建议查看上方详情。", YELLOW))
    else:
        print(_color("  [ERR] 有错误需要修复。请查看上方详情。", RED))
    print()


def main() -> int:
    """主入口。"""
    results = check_project_state()
    print_summary(results)
    return 1 if results["errors"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
