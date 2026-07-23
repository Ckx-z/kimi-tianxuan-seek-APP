"""PyInstaller 打包入口：cof-backend.exe

用法（Electron 主进程 sidecar 契约）：
    cof-backend.exe --port <端口> [--host 127.0.0.1]

frozen 模式下先 bootstrap 可写用户目录（%APPDATA%/COF-Film-Recommend，
可用 COF_DATA_DIR 覆盖），再用 uvicorn 在主进程内拉起 api.main:app。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def bootstrap_user_dirs() -> None:
    """frozen 首跑：建可写目录，并把只读的小型运行资产拷到用户应用根。

    编排器 iterate_suggest.py 以 --app-root 定位 <app_root>/minimax/experiment
    （失败语料），app_root 在 frozen 时指向用户应用根，故需拷一份过去。
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    try:
        from src import runtime_config
    except ImportError:
        import runtime_config  # type: ignore

    app_root = runtime_config.user_app_root()
    for sub in ("data", "config", "reports", "minimax"):
        (app_root / sub).mkdir(parents=True, exist_ok=True)

    src_exp = meipass / "minimax" / "experiment"
    dst_exp = app_root / "minimax" / "experiment"
    if src_exp.is_dir() and not dst_exp.exists():
        try:
            shutil.copytree(src_exp, dst_exp)
        except Exception as exc:  # 拷贝失败不阻断启动（语料缺失仅降级检索）
            print(f"[bootstrap] 失败语料拷贝跳过: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="COF 成膜推荐系统后端")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    args = parser.parse_args()

    bootstrap_user_dirs()

    import uvicorn
    uvicorn.run("api.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
