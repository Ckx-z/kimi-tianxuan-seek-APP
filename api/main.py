"""FastAPI 入口：COF 成膜推荐系统 REST API。

启动（开发）：
    E:\\ANACONDA\\python.exe -m uvicorn api.main:app --reload --port 8000
交互文档：http://127.0.0.1:8000/docs

与 Gradio App（app/gradio_app.py）并存，共用 src/ 后端与 data/ 数据，
互不影响；是未来 React/Tauri 独立前端的对接层。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import favorites, iterate, llm, monomers, plan, predict, records

app = FastAPI(
    title="COF 成膜推荐系统 API",
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

for r in (predict.router, favorites.router, records.router,
          monomers.router, plan.router, llm.router, iterate.router):
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
