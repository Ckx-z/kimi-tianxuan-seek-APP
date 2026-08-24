"""DFT 计算路由：异步任务 + 缓存 + 历史 + 收藏联动。

- POST /api/dft/jobs            建任务（202，缓存命中立即 done）
- GET  /api/dft/jobs/{id}       轮询任务状态/结果
- GET  /api/dft/jobs/{id}/geometry  复合物优化后 xyz（纯文本，供 3D 查看/下载）
- GET  /api/dft/history         计算历史（dft_log.jsonl，新→旧分页）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..schemas import DftJobCreate

try:
    from src.dft import engine, jobs
    from src.dft import log as dft_log
except ImportError:  # pragma: no cover - src 直接在 sys.path 时
    from dft import engine, jobs  # type: ignore
    from dft import log as dft_log  # type: ignore

router = APIRouter(prefix="/api/dft", tags=["dft"])


def _public_job(job: dict) -> dict:
    """对外视图：去掉内部输入冗余字段，保留轮询所需。"""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress_hint": job["progress_hint"],
        "method": job["method"],
        "cached": job.get("cached", False),
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
    }


@router.post("/jobs", status_code=202)
def create_dft_job(req: DftJobCreate):
    if not req.smiles_a.strip() or not req.smiles_b.strip():
        raise HTTPException(400, "两个单体的 SMILES 均不能为空")
    if req.method not in engine.METHODS:
        raise HTTPException(400, f"未知方法档位：{req.method}（可选 gfnff / gfn2）")
    if engine.xtb_binary() is None:
        raise HTTPException(
            503, "未安装计算引擎：未找到 xtb 二进制（vendor/xtb/bin/xtb.exe），"
            "DFT 计算暂不可用")
    job = jobs.create_job(req.smiles_a.strip(), req.smiles_b.strip(), req.method)
    return _public_job(job)


@router.get("/jobs/{job_id}")
def get_dft_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"计算任务 {job_id} 不存在（服务重启后任务不保留）")
    return _public_job(job)


@router.get("/jobs/{job_id}/geometry", response_class=PlainTextResponse)
def get_dft_geometry(job_id: str):
    """复合物优化后几何（xyz 文本），供前端下载/3D 渲染。"""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"计算任务 {job_id} 不存在")
    result = job.get("result") or {}
    xyz = result.get("complex_xyz")
    if not xyz:
        raise HTTPException(404, "该任务暂无可用几何（未完成或已失败）")
    return PlainTextResponse(xyz, media_type="chemical/x-xyz")


@router.get("/history")
def dft_history(limit: int = 50, offset: int = 0):
    """计算历史：读 dft_log.jsonl，新→旧，limit/offset 分页。"""
    entries, count = dft_log.read_history(limit=limit, offset=offset)
    return {"history": entries, "count": count}
