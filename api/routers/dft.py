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
- GET  /api/dft/atom-estimate   提交前原子数预估（含氢口径，替代前端粗估）
- POST /api/dft/jobs/{id}/cancel  取消进行中的计算任务（尽快终止子进程）
- GET  /api/dft/history         计算历史（dft_log.jsonl，新→旧分页）
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..schemas import (ConformerComplex, ConformerGenerate, ConformerManual,
                       DftDraftPut, DftJobCreate)

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
    from src.dft import conformers as dft_conformers
except ImportError:  # pragma: no cover - src 直接在 sys.path 时
    from dft import dimer as dimer_mod  # type: ignore
    from dft import engine, jobs  # type: ignore
    from dft import export as dft_export  # type: ignore
    from dft import log as dft_log  # type: ignore
    from dft import psi4_backend  # type: ignore
    from dft import conformers as dft_conformers  # type: ignore

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
        "progress_percent": job.get("progress_percent", 0),
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
                              n_samples=req.n_samples,
                              optimize=req.optimize, threads=req.threads,
                              with_props=req.with_props,
                              complex_xyz=req.complex_xyz)
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
        backend=backend, n_samples=req.n_samples,
        optimize=req.optimize, threads=req.threads,
        with_props=req.with_props,
        complex_xyz=req.complex_xyz)
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


@router.get("/conformers/engines")
def conformer_engines():
    """构象采样引擎可用性（前端「构象来源」选择器用）。"""
    return {"engines": dft_conformers.conformer_engines()}


@router.post("/conformers/generate")
def generate_conformers(req: ConformerGenerate):
    """自动检索低能构象（ETKDG 秒级 / CREST 分钟级，同步返回，前端显示加载态）。"""
    engine_name = (req.engine or "auto").strip()
    if engine_name not in ("auto", "etkdg", "crest"):
        raise HTTPException(400, f"未知的构象引擎：{req.engine}（可选 auto / etkdg / crest）")
    if engine.canonicalize_smiles(req.smiles) is None:
        raise HTTPException(400, f"SMILES 无法解析：{req.smiles[:80]}")
    if engine_name == "crest" and dft_conformers.crest_mode() is None:
        # 细分原因提示（引擎未运行/镜像缺失/真未安装），见 conformer_engines
        engines = dft_conformers.conformer_engines()
        hint = (engines.get("crest") or {}).get("install_hint")
        raise HTTPException(503, f"未安装 CREST：{hint}"
                                 f"；可改用 etkdg 引擎或自动模式")
    cached = dft_conformers.load_cached_conformers(
        req.smiles, engine_name, req.n_gen, req.max_confs, req.e_window_kj)
    if cached is not None:
        return {"conformers": cached, "engine": engine_name, "cached": True}
    confs = dft_conformers.generate_conformers(
        req.smiles, engine_name, n_gen=req.n_gen,
        max_confs=req.max_confs, e_window_kj=req.e_window_kj,
        threads=req.threads)
    if not confs:
        raise HTTPException(422, "构象生成失败：分子过小/无柔性键，或引擎未安装/超时，"
                                 "请换用其他引擎或分子")
    dft_conformers.save_cached_conformers(
        req.smiles, engine_name, req.n_gen, req.max_confs, req.e_window_kj, confs)
    return {"conformers": confs, "engine": engine_name, "cached": False}


@router.post("/conformers/complex")
def generate_complex_conformers(req: ConformerComplex):
    """复合物（A···B）低能构象采样（v1.5.2）：对两分子相对位姿做采样，
    输出 A+B 组合体 xyz（含 fragment_ranges），供 complex_xyz 注入计算。

    同步返回（分钟级）；失败 422 中文原因。
    """
    engine_name = (req.engine or "auto").strip()
    if engine_name not in ("auto", "etkdg", "crest", "rigid"):
        raise HTTPException(400, f"未知的构象引擎：{req.engine}"
                                 f"（可选 auto / etkdg / crest / rigid）")
    if engine.canonicalize_smiles(req.a_smiles) is None:
        raise HTTPException(400, f"分子 A 的 SMILES 无法解析：{req.a_smiles[:80]}")
    if engine.canonicalize_smiles(req.b_smiles) is None:
        raise HTTPException(400, f"分子 B 的 SMILES 无法解析：{req.b_smiles[:80]}")
    cached = dft_conformers.load_cached_complex_conformers(
        req.a_smiles, req.b_smiles, engine_name, req.n_gen, req.max_confs,
        req.e_window_kj, req.n_poses)
    if cached is not None:
        return {"complexes": cached, "engine": "complex", "cached": True}
    complexes = dft_conformers.generate_complex_conformers(
        req.a_smiles, req.b_smiles, engine_name, n_gen=req.n_gen,
        max_confs=req.max_confs, e_window_kj=req.e_window_kj,
        n_poses=req.n_poses, threads=req.threads)
    if not complexes:
        raise HTTPException(422, "复合物构象采样失败：两分子组合难以采样或 "
                                 "xTB 引擎不可用，请换用刚性（rigid）引擎或减少位姿数")
    dft_conformers.save_cached_complex_conformers(
        req.a_smiles, req.b_smiles, engine_name, req.n_gen, req.max_confs,
        req.e_window_kj, req.n_poses, complexes)
    return {"complexes": complexes, "engine": "complex", "cached": False}


@router.post("/conformers/manual")
def manual_conformer(req: ConformerManual):
    """手动摆放：主体 + 客体经刚体变换（平移/旋转，可选锚点对齐）合成复合物 xyz。"""
    import math

    if engine.canonicalize_smiles(req.a_smiles) is None:
        raise HTTPException(400, f"主体 SMILES 无法解析：{req.a_smiles[:80]}")
    if engine.canonicalize_smiles(req.b_smiles) is None:
        raise HTTPException(400, f"客体 SMILES 无法解析：{req.b_smiles[:80]}")
    try:
        a_xyz = engine.embed_monomer_xyz(req.a_smiles)
        if req.b_xyz:
            # 客体的指定构象（构象检索选中项）：直接使用，跳过 3D 生成
            b_xyz = req.b_xyz
        else:
            b_xyz = engine.embed_monomer_xyz(req.b_smiles)
    except engine.DftError as exc:
        raise HTTPException(422, f"几何生成失败：{exc}")
    b_atoms = _xyz_atoms_list(b_xyz)
    if not b_atoms:
        raise HTTPException(422, "客体几何生成失败")

    # 绕质心旋转（x→y→z 顺序，单位度）再平移
    cx, cy, cz = (sum(p[1] for p in b_atoms) / len(b_atoms),
                  sum(p[2] for p in b_atoms) / len(b_atoms),
                  sum(p[3] for p in b_atoms) / len(b_atoms))
    rx, ry, rz = (math.radians(req.rx_deg), math.radians(req.ry_deg),
                  math.radians(req.rz_deg))

    def rotate(x: float, y: float, z: float) -> tuple[float, float, float]:
        x, y, z = x - cx, y - cy, z - cz
        if rz:
            x, y = x * math.cos(rz) - y * math.sin(rz), \
                   x * math.sin(rz) + y * math.cos(rz)
        if ry:
            x, z = x * math.cos(ry) + z * math.sin(ry), \
                   -x * math.sin(ry) + z * math.cos(ry)
        if rx:
            y, z = y * math.cos(rx) - z * math.sin(rx), \
                   y * math.sin(rx) + z * math.cos(rx)
        return x + cx + req.tx, y + cy + req.ty, z + cz + req.tz

    b_lines = b_xyz.strip().splitlines()
    b_n = int(b_lines[0])
    new_b = [b_lines[0], b_lines[1]]
    for i, line in enumerate(b_lines[2: 2 + b_n]):
        parts = line.split()
        x, y, z = rotate(float(parts[1]), float(parts[2]), float(parts[3]))
        new_b.append(f"{parts[0]} {x:.6f} {y:.6f} {z:.6f}")

    # 锚点对齐：把客体锚点原子平移到主体锚点原子附近（范德华距离 3.0 Å）
    if req.anchor_a is not None and req.anchor_b is not None:
        a_atoms = _xyz_atoms_list(a_xyz)
        if req.anchor_a < len(a_atoms) and req.anchor_b < len(b_atoms):
            ax = a_atoms[req.anchor_a][1:4]
            bx = new_b[2 + req.anchor_b].split()
            dx, dy, dz = (ax[0] - float(bx[1]), ax[1] - float(bx[2]),
                          ax[2] - float(bx[3]))
            # 沿锚点方向拉开 3.0 Å（主体锚点 + 单位方向 × 3.0）
            norm = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            shift = 3.0
            ax_shift = (ax[0] + dx / norm * shift, ax[1] + dy / norm * shift,
                        ax[2] + dz / norm * shift)
            tx2, ty2, tz2 = (ax_shift[0] - float(bx[1]), ax_shift[1] - float(bx[2]),
                             ax_shift[2] - float(bx[3]))
            new_b = [new_b[0], new_b[1]] + [
                f"{new_b[2 + i].split()[0]} "
                f"{float(new_b[2 + i].split()[1]) + tx2:.6f} "
                f"{float(new_b[2 + i].split()[2]) + ty2:.6f} "
                f"{float(new_b[2 + i].split()[3]) + tz2:.6f}"
                for i in range(b_n)]

    a_n = int(a_xyz.strip().splitlines()[0])
    total = a_n + b_n
    combined = "\n".join([str(total), "manual complex"] +
                         a_xyz.strip().splitlines()[2: 2 + a_n] +
                         new_b[2: 2 + b_n])
    return {"xyz": combined, "atom_budget": {"a": a_n, "b": b_n,
                                             "complex": total},
            "fragment_ranges": {"a": [0, a_n], "b": [a_n, total]}}


def _xyz_atoms_list(xyz_block: str) -> list[list]:
    """xyz 文本 → [[symbol, x, y, z], ...]（表头两行跳过）。"""
    out = []
    try:
        lines = xyz_block.strip().splitlines()
        n = int(lines[0])
        for line in lines[2: 2 + n]:
            parts = line.split()
            if len(parts) >= 4:
                out.append([parts[0], float(parts[1]), float(parts[2]),
                            float(parts[3])])
    except Exception:
        pass
    return out


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
        },
        # 构象采样引擎可用性（v1.5.0：前端「构象来源」选择器用）
        "conformer_engines": dft_conformers.conformer_engines(),
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


@router.get("/atom-estimate")
def dft_atom_estimate(mode: str = "dimer", ald_smiles: str = "",
                      amine_smiles: str = "", x_type: str = "self_stack",
                      solvent_id: str | None = None,
                      ald2_smiles: str | None = None,
                      amine2_smiles: str | None = None,
                      custom_smiles: str | None = None):
    """提交前原子数预估（含氢口径，与嵌入 xyz 的真实原子数一致）。

    pair 模式：复合物 = 分子 A + 分子 B；dimer 模式：复合物 = 二聚体 + X
    （self_stack 时 X=二聚体自身，故复合物为二聚体 2 倍）。任何一步解析
    失败时对应计数值为 None（前端据 None 隐藏提示），本端点不报错。
    """
    def atoms(smiles: str | None) -> int | None:
        if not smiles:
            return None
        return engine.atom_count_with_h(smiles)

    a = atoms(ald_smiles)
    b = atoms(amine_smiles)
    if mode == "pair":
        return {"dimer_atom_count": None,
                "x_atom_count": b,
                "complex_atom_count":
                    (a + b) if a is not None and b is not None else None}
    try:
        dim = dimer_mod.make_dimer(ald_smiles, amine_smiles)
        dimer_smiles = dim["smiles"]
    except Exception:
        return {"dimer_atom_count": None, "x_atom_count": None,
                "complex_atom_count": None}
    d = atoms(dimer_smiles)
    x = None
    if x_type == "self_stack":
        x = d
    elif x_type == "solvent":
        solvent = next((s for s in engine.SOLVENTS if s["id"] == solvent_id),
                       None)
        x = atoms(solvent["smiles"]) if solvent else None
    elif x_type == "other_dimer":
        try:
            x = atoms(dimer_mod.make_dimer(ald2_smiles, amine2_smiles)["smiles"])
        except Exception:
            x = None
    elif x_type == "custom":
        x = atoms(custom_smiles)
    return {"dimer_atom_count": d,
            "x_atom_count": x,
            "complex_atom_count":
                (d + x) if d is not None and x is not None else None}


@router.get("/jobs/{job_id}")
def get_dft_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"计算任务 {job_id} 不存在（服务重启后任务不保留）")
    return _public_job(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_dft_job(job_id: str):
    """取消进行中的计算任务：置位取消事件 → 子进程尽快终止 → 终态 cancelled。"""
    ok, job = jobs.request_cancel(job_id)
    if job is None:
        raise HTTPException(404, f"计算任务 {job_id} 不存在")
    if not ok:
        raise HTTPException(409, "任务已处于终态（完成/失败/已取消），无法取消")
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
