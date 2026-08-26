"""FastAPI 入口：COF 科研系统 REST API。

启动（开发）：
    E:\\ANACONDA\\python.exe -m uvicorn api.main:app --reload --port 8000
交互文档：http://127.0.0.1:8000/docs

与 Gradio App（app/gradio_app.py）并存，共用 src/ 后端与 data/ 数据，
互不影响；是未来 React/Tauri 独立前端的对接层。
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .routers import (assistant, dft, favorite_folders, favorites, iterate,
                      literature, llm, monomers, plan, predict, records)

# frozen（PyInstaller onedir）时资源在 sys._MEIPASS（exe 旁 _internal）
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIST = PROJECT_ROOT / "webapp" / "dist"

app = FastAPI(
    title="COF 科研系统 API",
    version="0.1.0",
    description="src/ 后端的 REST 封装：打分 / 收藏 / 实验记录 / 方案卡 / LLM。",
)

# 开发期放开本地前端跨域（React dev server 等）；上线时按域名收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (predict.router, favorites.router, favorite_folders.router,
          records.router, monomers.router, plan.router, llm.router,
          iterate.router, assistant.router, dft.router, literature.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    """存活 + 模型可用性（不触发 GNN subprocess，仅树模型加载态）。"""
    from .deps import get_predictor
    pred = get_predictor()
    return {
        "status": "ok",
        "tree_available": pred.tree_available,
        "gnn_available": pred.gnn_available,
        "routing": pred.router is not None,
    }


class SPAStaticFiles(StaticFiles):
    """静态托管 React 构建产物，未命中的非 /api 路径回退 index.html（SPA 路由）。

    缓存策略（2026-08-20 事故修复）：Electron 内嵌 Chromium 对
    http://localhost:<port>/ 做启发式磁盘缓存，版本升级后同 origin 会
    直接吃旧缓存 → 用户看到上一版界面。因此：
    - index.html（及 SPA 回退）：no-cache，每次启动必须回源校验；
    - assets/ 下带内容哈希的 JS/CSS：immutable 长缓存（内容变哈希变）。
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            resp = await super().get_response(path, scope)
            self._apply_cache_headers(resp, path)
            return resp
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                # 带扩展名的路径（.js/.css/.png 等静态资源）缺失必须回真 404
                # ——若回退成 index.html，module script 会因 MIME 是 text/html
                # 而白屏且极难排查；SPA 前端路由不含扩展名，不受影响
                if "." in path.replace("\\", "/").rsplit("/", 1)[-1]:
                    raise
                resp = await super().get_response("index.html", scope)
                resp.headers["Cache-Control"] = "no-cache"
                return resp
            raise

    @staticmethod
    def _apply_cache_headers(resp, path: str) -> None:
        # 不依赖 path 的具体形态（mount 点不同 path 取值不同），
        # 按内容类型与哈希资源目录判断，保证各入口一致生效。
        ctype = resp.headers.get("content-type", "")
        if "text/html" in ctype:
            resp.headers["Cache-Control"] = "no-cache"
        elif "assets/" in path.replace("\\", "/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"


# 挂载在路由注册之后：/api/* 由上方 router 优先匹配，其余路径走静态文件
if WEBAPP_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(WEBAPP_DIST), html=True), name="webapp")
