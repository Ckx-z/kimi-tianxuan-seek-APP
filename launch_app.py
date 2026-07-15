"""一键启动 App：启动 Gradio 服务并自动打开浏览器。"""

from __future__ import annotations

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
HOST = "127.0.0.1"
PORT = 7860
URL = f"http://{HOST}:{PORT}"


def is_port_open(host: str, port: int) -> bool:
    """检查端口是否已经被占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def is_server_ready(url: str, timeout: int = 60) -> bool:
    """轮询等待 Gradio 服务就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    print(f"项目目录: {PROJECT_ROOT}")
    print(f"App 脚本: {APP_PATH}")

    if not APP_PATH.exists():
        print(f"错误：找不到 {APP_PATH}")
        sys.exit(1)

    # 如果端口已占用，说明 App 已经在运行，直接打开浏览器
    if is_port_open(HOST, PORT):
        print(f"检测到 App 已在运行 ({URL})")
        webbrowser.open(URL)
        print("已打开浏览器")
        return

    # 启动 App（在后台运行）
    print("正在启动 App，请稍候...")
    env = dict(os.environ)
    # 把 src/ 和 app/ 加入 PYTHONPATH，确保能找到模块
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + str(PROJECT_ROOT / "app")

    process = subprocess.Popen(
        [sys.executable, str(APP_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # 等待服务就绪
    print(f"等待 Gradio 服务就绪（最多 60 秒）...")
    if not is_server_ready(URL, timeout=60):
        print("错误：App 启动失败或超时")
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
        sys.exit(1)

    print(f"App 已就绪: {URL}")
    webbrowser.open(URL)
    print("已打开浏览器")

    # 保持进程运行，直到用户按 Ctrl+C
    print("按 Ctrl+C 关闭 App")
    try:
        while True:
            line = process.stdout.readline()
            if line:
                print(line, end="")
            else:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n正在关闭 App...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()


if __name__ == "__main__":
    main()
