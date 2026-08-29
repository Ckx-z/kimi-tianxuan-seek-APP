"""DFT 计算路由：异步任务 + 缓存 + 历史 + 收藏联动（2.0：二聚体与 X 的结合能）。

- POST /api/dft/jobs            建任务（202，缓存命中立即 done；旧字段
                                smiles_a/smiles_b 兼容映射为醛/胺单体；
                                backend="xtb"（默认）| "psi4" 精度档）
- GET  /api/dft/backends        各计算后端可用状态（psi4 未安装时给安装引导）
- GET  /api/dft/jobs/{id}       轮询任务状态/结果
- GET  /api/dft/jobs/{id}/geometry  复合物优化后 xyz（纯文本，供 3D 查看/下载）
- GET  /api/dft/jobs/{id}/export?format=gaussian|orca  量化软件输入文件下载
- GET  /api/dft/solvents        内置溶剂表（x_type=solvent 的可选项）
- GET  /api/dft/dimer-preview   醛/胺单体 → 缩合二聚体预览（SMILES + 多位点标注）
- GET  /api/dft/history         计算历史（dft_log.jsonl，新→旧分页）
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..schemas import DftDraftPut, DftJobCreate

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

try:
    from src.dft import dimer as dimer_mod
    from src.dft import engine, jobs
    from src.dft import export as dft_export
    from src.dft import log as dft_log
    from src.dft import psi4_backend
except ImportError:  # pragma: no cover - src 直接在 sys.path 时
    from dft import dimer as dimer_mod  # type: ignore
    from dft import engine, jobs  # type: ignore
    from dft import export as dft_export  # type: ignore
    from dft import log as dft_log  # type: ignore
    from dft import psi4_backend  # type: ignore

router = APIRouter(prefix="/api/dft", tags=["dft"])


def _public_job(job: dict) -> dict:
    """对外视图：保留轮询所需 + 透出输入参数（前端返回页面时据此恢复表单）。"""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress_hint": job["progress_hint"],
        "method": job["method"],
        "mode": job.get("mode", "dimer"),
        "backend": job.get("backend", "xtb"),
        "cached": job.get("cached", False),
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "input": {
            "ald_smiles": job.get("ald_smiles_input"),
            "amine_smiles": job.get("amine_smiles_input"),
            "x_type": job.get("x_type"),
            "solvent_id": job.get("solvent_id"),
            "ald2_smiles": job.get("ald2_smiles"),
            "amine2_smiles": job.get("amine2_smiles"),
            "custom_smiles": job.get("custom_smiles"),
            "n_samples": job.get("n_samples"),
        },
    }


def _draft_path() -> Path:
    """计算页草稿落盘路径（user_data_root，惰性解析便于测试隔离）。"""
    return runtime_config.user_data_root() / "dft_draft.json"


def _resolve_monomers(req: DftJobCreate) -> tuple[str, str]:
    """新旧字段兼容：ald_smiles/amine_smiles 优先，缺省回落 smiles_a/smiles_b。"""
    ald = (req.ald_smiles or req.smiles_a or "").strip()
    amine = (req.amine_smiles or req.smiles_b or "").strip()
    return ald, amine


@router.post("/jobs", status_code=202)
def create_dft_job(req: DftJobCreate):
    mode = (req.mode or "dimer").strip()
    if mode not in engine.MODES:
        raise HTTPException(
            400, f"未知的计算模式：{req.mode}"
            f"（可选 {' / '.join(engine.MODES)}）")
    backend = (req.backend or "xtb").strip()
    if backend not in jobs.BACKENDS:
        raise HTTPException(
            400, f"未知的计算后端：{req.backend}"
            f"（可选 {' / '.join(jobs.BACKENDS)}）")
    ald, amine = _resolve_monomers(req)

    # 方法档位按后端解释；psi4 未显式给方法档时回落默认（前端可能沿用 xtb 默认 gfn2）
    if backend == "psi4":
        method = psi4_backend.resolve_method_key(req.method)
        if method not in psi4_backend.PSI4_METHODS:
            method = psi4_backend.DEFAULT_PSI4_METHOD
    else:
        method = req.method
        if method not in engine.METHODS:
            raise HTTPException(400, f"未知方法档位：{req.method}（可选 gfnff / gfn2）")

    if mode == "pair":
        # 任意双分子模式：ald/amine 字段位复用为分子 A/B，跳过二聚体与 X 校验
        if not ald or not amine:
            raise HTTPException(400, "分子 A 与分子 B 的 SMILES 均不能为空")
        if engine.canonicalize_smiles(ald) is None:
            raise HTTPException(400, f"分子 A 的 SMILES 无法解析：{ald[:80]}")
        if engine.canonicalize_smiles(amine) is None:
            raise HTTPException(400, f"分子 B 的 SMILES 无法解析：{amine[:80]}")
        _check_backend_available(backend)
        job = jobs.create_job(ald, amine, method, mode="pair", backend=backend,
                              n_samples=req.n_samples)
        return _public_job(job)

    if not ald or not amine:
        raise HTTPException(400, "醛单体与胺单体的 SMILES 均不能为空")
    if req.x_type not in engine.X_TYPES:
        raise HTTPException(
            400, f"未知的 X 类型：{req.x_type}"
            f"（可选 {' / '.join(engine.X_TYPES)}）")

    # 前置校验：二聚体可生成 + X 参数齐全（缺参数 400 中文）
    try:
        dim = dimer_mod.make_dimer(ald, amine)
    except dimer_mod.DimerError as exc:
        raise HTTPException(400, f"二聚体生成失败：{exc}")
    try:
        engine.resolve_x(
            req.x_type, dim["smiles"], solvent_id=req.solvent_id,
            ald2_smiles=req.ald2_smiles, amine2_smiles=req.amine2_smiles,
            custom_smiles=req.custom_smiles)
    except engine.DftError as exc:
        raise HTTPException(400, str(exc))

    _check_backend_available(backend)
    job = jobs.create_job(
        ald, amine, method, x_type=req.x_type,
        solvent_id=req.solvent_id, ald2_smiles=req.ald2_smiles,
        amine2_smiles=req.amine2_smiles, custom_smiles=req.custom_smiles,
        backend=backend, n_samples=req.n_samples)
    return _public_job(job)


def _check_backend_available(backend: str) -> None:
    """后端可用性前置校验；不可用抛 503 中文原因（psi4 附带安装引导）。"""
    if backend == "xtb":
        if engine.xtb_binary() is None:
            raise HTTPException(
                503, "未安装计算引擎：未找到 xtb 二进制（vendor/xtb/bin/xtb.exe），"
                "DFT 计算暂不可用")
        return
    det = psi4_backend.detect_psi4()
    if not det["installed"]:
        raise HTTPException(503, det["reason"])


@router.get("/draft")
def get_dft_draft():
    """读计算页表单草稿（切页/刷新后恢复表单与任务引用用）。无草稿返回 null。"""
    try:
        p = _draft_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "draft" in data:
                return {"draft": data["draft"]}
    except Exception:
        pass
    return {"draft": None}


@router.put("/draft")
def put_dft_draft(req: DftDraftPut):
    """保存计算页表单草稿（结构由前端定义，后端原样存取，落 user_data_root）。"""
    try:
        p = _draft_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        payload = {"draft": req.draft,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return {"ok": True}
    except Exception:
        return {"ok": False}


@router.get("/backends")
def dft_backends():
    """各计算后端的可用状态（前端后端选择器与 Psi4 安装引导用）。"""
    det = psi4_backend.detect_psi4()
    return {
        "backends": {
            "xtb": {
                "installed": engine.xtb_binary() is not None,
                "version": None,
                "path": str(engine.xtb_binary() or "") or None,
                "label": "xTB 半经验（快速档）",
                "methods": [
                    {"id": m, "label": spec["label"]}
                    for m, spec in engine.METHODS.items()],
            },
            "psi4": {
                "installed": det["installed"],
                "version": det["version"],
                "path": det["path"],
                "label": "Psi4 真 DFT（精度档）",
                "methods": [
                    {"id": m, "label": spec["label"],
                     "preset": spec.get("preset")}
                    for m, spec in psi4_backend.PSI4_METHODS.items()],
                "default_method": psi4_backend.DEFAULT_PSI4_METHOD,
                "install_hint": None if det["installed"]
                else psi4_backend.INSTALL_HINT,
                "reason": det["reason"],
            },
        }
    }


@router.get("/solvents")
def dft_solvents():
    """内置常用溶剂表（x_type=solvent 时前端下拉的可选项）。"""
    return {"solvents": [
        {"id": s["id"], "name_zh": s["name_zh"], "smiles": s["smiles"]}
        for s in engine.SOLVENTS]}


@router.get("/dimer-preview")
def dft_dimer_preview(ald_smiles: str, amine_smiles: str):
    """醛/胺单体 → 缩合二聚体预览（不计算，仅反应模板）。

    返回二聚体 canonical SMILES 与多位点标注；结构图由前端调
    /api/monomers/structure.svg?smiles=<dimer_smiles> 展示。
    """
    try:
        dim = dimer_mod.make_dimer(ald_smiles, amine_smiles)
    except dimer_mod.DimerError as exc:
        raise HTTPException(400, str(exc))
    return {
        "dimer_smiles": dim["smiles"],
        "multi_site": dim["multi_site"],
        "note": dim["note"],
    }


@router.get("/jobs/{job_id}")
def get_dft_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"计算任务 {job_id} 不存在（服务重启后任务不保留）")
    return _public_job(job)


@router.get("/jobs/{job_id}/geometry", response_class=PlainTextResponse)
def get_dft_geometry(job_id: str):
    """复合物优化后几何（xyz 文本），供前端下载/3D 渲染。

    响应头 X-Fragment-Ranges 携带两片段原子序区间（JSON，0 基左闭右开），
    供 3D 着色区分两分子；旧任务无该字段时省略。
    """
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"计算任务 {job_id} 不存在")
    result = job.get("result") or {}
    xyz = result.get("complex_xyz")
    if not xyz:
        raise HTTPException(404, "该任务暂无可用几何（未完成或已失败）")
    headers = {}
    frag = result.get("fragment_ranges")
    if isinstance(frag, dict) and "a" in frag and "b" in frag:
        import json as _json
        headers["X-Fragment-Ranges"] = _json.dumps(frag)
        headers["Access-Control-Expose-Headers"] = "X-Fragment-Ranges"
    return PlainTextResponse(xyz, media_type="chemical/x-xyz", headers=headers)


@router.get("/jobs/{job_id}/export")
def export_dft_input(job_id: str, format: str = "gaussian"):
    """导出量化软件输入文件（Gaussian .gjf / ORCA .inp，text/plain 下载）。

    中文文件名走 RFC 5987 filename* 编码，同时给 ASCII 兜底名。
    """
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"计算任务 {job_id} 不存在")
    result = job.get("result") or {}
    xyz = result.get("complex_xyz")
    if not xyz:
        raise HTTPException(404, "该任务暂无可用几何（未完成或已失败）")
    method = result.get("method") or job.get("method") or ""
    try:
        content = dft_export.build_export(format, xyz, source=method)
    except dft_export.DftExportError as exc:
        raise HTTPException(400, str(exc))
    filename = dft_export.export_filename(format, method)
    quoted = urllib.parse.quote(filename, encoding="utf-8")
    fallback = f"dft_export.{filename.rsplit('.', 1)[-1]}"
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{fallback}\"; "
            f"filename*=UTF-8''{quoted}"),
    }
    return PlainTextResponse(
        content, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/history")
def dft_history(limit: int = 50, offset: int = 0):
    """计算历史：读 dft_log.jsonl，新→旧，limit/offset 分页。"""
    entries, count = dft_log.read_history(limit=limit, offset=offset)
    return {"history": entries, "count": count}
