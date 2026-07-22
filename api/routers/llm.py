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
