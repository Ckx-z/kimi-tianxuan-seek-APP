"""静默启动 COF 成膜 App（供 start_app.vbs 以 pythonw 调用，无控制台窗口）。

行为：
1. 若 7860 端口已有服务在运行，直接打开浏览器，不重复启动。
2. 否则用 pythonw 在后台启动 app/gradio_app.py，日志写入 logs/gradio_app.log。
3. 轮询等待服务就绪（最多 90 秒），就绪后自动打开浏览器。
4. 启动失败（依赖缺失、端口占用异常、进程提前退出等）时弹出 msgbox 并记录日志。

注意：本脚本在 pythonw 下运行，sys.stdout 为 None，禁止 print，一律写日志。
"""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_PATH = PROJECT_ROOT / "app" / "gradio_app.py"
LOG_DIR = PROJECT_ROOT / "logs"
LAUNCH_LOG = LOG_DIR / "app_launch.log"
APP_LOG = LOG_DIR / "gradio_app.log"
HOST = "127.0.0.1"
PORT = 7860
URL = f"http://{HOST}:{PORT}"
# App 依赖 base 环境（gradio 6.20）；.venv 没有 gradio，不能用它启动 App
PYTHONW = Path(r"E:\ANACONDA\pythonw.exe")


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=str(LAUNCH_LOG),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )


def show_error(title: str, message: str) -> None:
    """无控制台环境下用 msgbox 提示错误。"""
    logging.error("%s: %s", title, message)
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def is_server_ready(url: str, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def read_log_tail(path: Path, max_chars: int = 800) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:].strip()
    except Exception:
        return ""


def main() -> int:
    setup_logging()
    logging.info("=== 静默启动开始 ===")

    if not APP_PATH.exists():
        show_error("COF App 启动失败", f"找不到 App 脚本：\n{APP_PATH}")
        return 1

    if not PYTHONW.exists():
        show_error(
            "COF App 启动失败",
            f"找不到 Python 环境：\n{PYTHONW}\n\n请确认 Anaconda base 环境存在（App 依赖 gradio）。",
        )
        return 1

    # 端口已占用：认为 App 已在运行，直接打开浏览器
    if is_port_open(HOST, PORT):
        logging.info("端口 %d 已被占用，视为 App 已运行，直接打开浏览器", PORT)
        webbrowser.open(URL)
        return 0

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + str(PROJECT_ROOT / "app")

    logging.info("启动 App: %s %s", PYTHONW, APP_PATH)
    app_log_handle = open(APP_LOG, "a", encoding="utf-8", errors="replace")
    app_log_handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")
    app_log_handle.flush()
    try:
        process = subprocess.Popen(
            [str(PYTHONW), str(APP_PATH)],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=app_log_handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception as exc:
        app_log_handle.close()
        show_error("COF App 启动失败", f"无法启动 App 进程：\n{exc}")
        return 1

    # 等待服务就绪；期间若进程提前退出则判定失败
    deadline = time.time() + 90
    while time.time() < deadline:
        if process.poll() is not None:
            app_log_handle.close()
            tail = read_log_tail(APP_LOG)
            show_error(
                "COF App 启动失败",
                "App 进程提前退出（可能是依赖缺失或代码错误）。\n\n"
                f"详细日志：{APP_LOG}\n\n日志末尾：\n{tail or '（无输出）'}",
            )
            return 1
        try:
            urllib.request.urlopen(URL, timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        # 超时
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
        app_log_handle.close()
        tail = read_log_tail(APP_LOG)
        show_error(
            "COF App 启动超时",
            "等待 90 秒后服务仍未就绪。\n\n"
            f"详细日志：{APP_LOG}\n\n日志末尾：\n{tail or '（无输出）'}",
        )
        return 1

    logging.info("App 已就绪: %s (pid=%s)", URL, process.pid)
    app_log_handle.close()
    webbrowser.open(URL)
    logging.info("已打开浏览器，静默启动脚本退出（App 继续在后台运行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
