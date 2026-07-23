"""静默启动 COF 成膜 App（供 启动COF推荐.vbs 以 pythonw 调用，无控制台窗口）。

行为（单实例 + 重启语义）：
1. 通过命名 mutex 保证启动器单实例：双击两次只会有一个启动流程在跑，
   后一个直接退出，杜绝两个新实例抢 7860 端口（OSError: Cannot find empty port）。
2. 用 PID 文件（logs/app.pid）管理 App 进程：启动时若 PID 文件存在且进程活着，
   一律先杀掉旧实例（视为旧代码/僵尸进程），再启动新实例 —— 即"双击 = 重启为最新代码"。
3. 若 7860 端口被【非本 App】的进程占用（PID 文件对不上），弹 msgbox 提示用户处理，不强杀别人。
4. 启动失败/超时弹窗并写日志（既有行为保留）。

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
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_PATH = PROJECT_ROOT / "app" / "gradio_app.py"
LOG_DIR = PROJECT_ROOT / "logs"
LAUNCH_LOG = LOG_DIR / "app_launch.log"
APP_LOG = LOG_DIR / "gradio_app.log"
PID_FILE = LOG_DIR / "app.pid"
HOST = "127.0.0.1"
PORT = 7860
URL = f"http://{HOST}:{PORT}"
MUTEX_NAME = "Local\\COFFilmAppLauncher_SingleInstance"

# App 进程解释器：经 src/runtime_config 解析（环境变量 COF_APP_PYTHONW >
# config/runtime.local.json > 开发机历史路径 > PATH 探测 pythonw/python）。
# App 依赖 base 环境（gradio 6.20）；.venv 没有 gradio，不能用它启动 App
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    from src import runtime_config as _rc
except ImportError:
    _rc = None
PYTHONW = _rc.app_pythonw() if _rc is not None else None

STILL_ACTIVE = 259
ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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


def acquire_single_instance_mutex():
    """创建命名 mutex 保证启动器单实例。

    返回 (mutex_handle, acquired)。未获取到时 handle 也为有效句柄，调用方退出即释放。
    """
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        return None, False
    acquired = ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    return handle, acquired


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def pid_alive(pid: int) -> bool:
    """用 OpenProcess + GetExitCodeProcess 判断进程是否存活（Windows 安全，不会误杀）。"""
    if pid <= 0:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return bool(ok) and exit_code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def read_pid_file() -> int | None:
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        return pid if pid > 0 else None
    except Exception:
        return None


def kill_pid_tree(pid: int) -> None:
    """taskkill /T /F 杀掉进程树（App 可能有子进程）。"""
    logging.info("杀掉旧实例进程树: pid=%d", pid)
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )


def wait_port_free(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_port_open(HOST, PORT):
            return True
        time.sleep(0.5)
    return not is_port_open(HOST, PORT)


def read_log_tail(path: Path, max_chars: int = 800) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:].strip()
    except Exception:
        return ""


def cleanup_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def stop_previous_instance() -> None:
    """重启语义：PID 文件存在且进程活着 → 杀掉旧实例（旧代码/僵尸）。"""
    old_pid = read_pid_file()
    if old_pid is None:
        cleanup_pid_file()
        return
    if pid_alive(old_pid):
        logging.info("检测到旧 App 实例 (pid=%d) 仍在运行，执行重启：先杀掉旧实例", old_pid)
        kill_pid_tree(old_pid)
        # 等旧进程真正退出
        deadline = time.time() + 10
        while time.time() < deadline and pid_alive(old_pid):
            time.sleep(0.3)
        if pid_alive(old_pid):
            logging.warning("旧实例 pid=%d 未能确认退出", old_pid)
    else:
        logging.info("PID 文件指向的进程 (pid=%d) 已不存在，清理过期 PID 文件", old_pid)
    cleanup_pid_file()


def main() -> int:
    setup_logging()
    logging.info("=== 静默启动开始（单实例+重启语义） ===")

    # 单实例锁：双击竞态时后一个启动器直接退出，避免两个新实例抢端口
    mutex_handle, acquired = acquire_single_instance_mutex()
    if not acquired:
        logging.info("另一个启动流程正在进行（mutex 已存在），本次启动直接退出")
        return 0

    try:
        if not APP_PATH.exists():
            show_error("COF App 启动失败", f"找不到 App 脚本：\n{APP_PATH}")
            return 1

        if PYTHONW is None or not PYTHONW.exists():
            show_error(
                "COF App 启动失败",
                "找不到可用的 Python 环境（App 依赖 gradio）。\n\n"
                "请安装 Anaconda，或通过环境变量 COF_APP_PYTHONW / "
                "config/runtime.local.json 指定 pythonw 路径。",
            )
            return 1

        # 重启语义：杀掉 PID 文件记录的旧实例（若有）
        stop_previous_instance()

        # 等端口释放；若仍被占用，则是【非本 App】的进程占的 → 弹窗提示，不强杀
        if not wait_port_free(timeout=15):
            show_error(
                "COF App 启动失败",
                f"端口 {PORT} 被其他程序占用（不是本 App 的旧实例），无法启动。\n\n"
                "请检查并关闭占用该端口的程序后重试。\n"
                f'可在命令行运行: netstat -ano | findstr :{PORT}',
            )
            return 1

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

        # 记录 PID 文件，供下次启动做重启语义
        try:
            PID_FILE.write_text(str(process.pid), encoding="utf-8")
            logging.info("已写入 PID 文件: %s (pid=%d)", PID_FILE, process.pid)
        except Exception:
            logging.warning("写入 PID 文件失败，重启语义下次不可用", exc_info=True)

        # 等待服务就绪；期间若进程提前退出则判定失败
        deadline = time.time() + 90
        while time.time() < deadline:
            if process.poll() is not None:
                app_log_handle.close()
                cleanup_pid_file()
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
            cleanup_pid_file()
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
    finally:
        if mutex_handle:
            ctypes.windll.kernel32.ReleaseMutex(mutex_handle)
            ctypes.windll.kernel32.CloseHandle(mutex_handle)


if __name__ == "__main__":
    sys.exit(main())
