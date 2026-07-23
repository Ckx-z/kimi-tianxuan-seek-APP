"""LLM 配置路由：查看 / 保存 / 连通性测试。"""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import LLMSettings

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/settings")
def get_settings():
    from llm import client
    return client.get_settings() | {"configured": client.is_configured()}


@router.put("/settings")
def put_settings(req: LLMSettings):
    from llm import client
    client.save_settings(req.base_url.strip(), req.api_key.strip(),
                         req.model.strip())
    return {"saved": True, "configured": client.is_configured()}


@router.post("/test")
def test_connection():
    from llm import client
    ok, msg = client.test_connection()
    return {"ok": ok, "message": msg}


@router.get("/env-status")
def env_status():
    """运行环境能力总览（供前端设置页展示，不触发任何重活）。

    返回 {tree, gnn, graphrag, llm}：各项为 "ok" 或
    "disabled: <原因>"；llm 为 "configured"/"not_configured"。
    """
    from llm import client
    try:
        from src import runtime_config
    except ImportError:  # src/ 直接上 sys.path 的兜底
        import runtime_config  # type: ignore
    status = runtime_config.capability_status()
    status["llm"] = "configured" if client.is_configured() else "not_configured"
    return status
