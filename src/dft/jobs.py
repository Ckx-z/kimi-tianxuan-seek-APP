"""DFT 异步任务注册表：内存 job 字典 + 后台线程执行，供 API 轮询。

- create_job: 查缓存（命中直接 done + cached=True）→ 否则起后台线程
- 工作线程执行 engine.compute_binding，on_stage 回调更新 progress_hint
- 完成后写 dft_log.jsonl、存缓存、附带收藏联动信息
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from . import cache as dft_cache
from . import dimer as dimer_mod
from . import engine
from . import log as dft_log
from . import psi4_backend

# 计算后端：xtb 快速档（默认）| psi4 真 DFT 精度档
BACKENDS = ("xtb", "psi4")

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}

MAX_JOBS = 200  # 超出后淘汰最旧的已完成任务，防止内存膨胀


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_favorite(smiles_a: str, smiles_b: str) -> dict | None:
    """收藏联动：两种单体序都查一次（收藏按醛/胺位次存储，DFT 输入无序）。"""
    try:
        from favorites import store as fav_store
    except Exception:
        try:
            from src.favorites import store as fav_store  # type: ignore
        except Exception:
            return None
    fav = fav_store.find_favorite_by_pair(smiles_a, smiles_b) or \
        fav_store.find_favorite_by_pair(smiles_b, smiles_a)
    if not fav:
        return None
    folder = fav_store.get_folder(str(fav.get("folder_id") or ""))
    snap = fav.get("latest_prediction")
    return {
        "id": fav.get("id"),
        "folder_id": fav.get("folder_id"),
        "folder_name": (folder or {}).get("name", "收藏夹1"),
        "aldehyde_name": (fav.get("aldehyde") or {}).get("name", ""),
        "amine_name": (fav.get("amine") or {}).get("name", ""),
        "has_prediction": isinstance(snap, dict) and snap.get("score") is not None,
        "has_dft": isinstance(fav.get("dft_snapshot"), dict),
    }


def _prune_locked() -> None:
    if len(_JOBS) <= MAX_JOBS:
        return
    finished = sorted(
        ((j["created_at"], jid) for jid, j in _JOBS.items()
         if j["status"] in ("done", "failed")))
    for _, jid in finished[: len(_JOBS) - MAX_JOBS]:
        _JOBS.pop(jid, None)


def _sampler_tag(backend: str, method: str, n_samples: int | None
                 ) -> str | None:
    """缓存 key 的采样口径标记：gfnff 不采样（None 保持旧格式）；
    gfn2/psi4 走 MC 采样，按请求采样数区分（None → "mc0" 默认口径）。"""
    if backend == "xtb" and method != "gfn2":
        return None
    return f"mc{n_samples if n_samples and n_samples > 0 else 0}"


def create_job(ald_smiles: str, amine_smiles: str, method: str,
               x_type: str = "self_stack", solvent_id: str | None = None,
               ald2_smiles: str | None = None, amine2_smiles: str | None = None,
               custom_smiles: str | None = None, mode: str = "dimer",
               backend: str = "xtb", n_samples: int | None = None) -> dict:
    """建任务。缓存命中 → 直接 done；否则 pending 并起后台线程。

    mode="pair" 时 ald/amine 参数位复用为分子 A/B，忽略 x_type 相关字段。
    backend="psi4" 时 method 为 psi4_backend.PSI4_METHODS 键，走真 DFT 精度档。
    n_samples：MC 取向采样数（None=默认；1=旧单取向初猜口径）。
    """
    job_id = uuid.uuid4().hex[:16]
    job = {
        "job_id": job_id,
        "status": "pending",
        "progress_hint": "排队等待计算…",
        "method": method,
        "mode": mode,
        "backend": backend,
        "n_samples": n_samples,
        "ald_smiles_input": ald_smiles,
        "amine_smiles_input": amine_smiles,
        "x_type": x_type,
        "solvent_id": solvent_id,
        "ald2_smiles": ald2_smiles,
        "amine2_smiles": amine2_smiles,
        "custom_smiles": custom_smiles,
        "result": None,
        "error": None,
        "cached": False,
        "created_at": _utc_now(),
    }
    with _LOCK:
        _JOBS[job_id] = job
        _prune_locked()

    # 规范化/二聚体生成失败会在工作线程里变成中文错误；
    # 这里先算出新口径缓存 key 探缓存
    probe = _cache_probe_key(ald_smiles, amine_smiles, method, x_type,
                             solvent_id, ald2_smiles, amine2_smiles,
                             custom_smiles, mode, backend, n_samples)
    if probe is not None:
        key, canon_ald, canon_amine = probe
        hit = dft_cache.load_cache(key)
        if hit is not None:
            result = dict(hit)
            result["cached"] = True
            # pair 模式无单体组归属，不做收藏联动
            result["favorite"] = None if mode == "pair" else \
                _find_favorite(canon_ald, canon_amine)
            job.update(status="done", progress_hint="命中缓存，直接返回历史结果",
                       result=result, cached=True)
            return job

    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True,
                         name=f"dft-job-{job_id}")
    t.start()
    return job


def _cache_probe_key(ald_smiles, amine_smiles, method, x_type,
                     solvent_id, ald2_smiles, amine2_smiles,
                     custom_smiles, mode="dimer", backend="xtb",
                     n_samples=None):
    """尽力算出 (缓存 key, 规范化 A, 规范化 B)；任一步失败返回 None。"""
    try:
        methods = engine.METHODS if backend == "xtb" else psi4_backend.PSI4_METHODS
        method = psi4_backend.resolve_method_key(method) \
            if backend == "psi4" else method
        if method not in methods:
            return None
        tag = _sampler_tag(backend, method, n_samples)
        canon_ald = engine.canonicalize_smiles(ald_smiles)
        canon_amine = engine.canonicalize_smiles(amine_smiles)
        if not canon_ald or not canon_amine:
            return None
        if mode == "pair":
            return (dft_cache.cache_key(
                        canon_ald, f"pair:{canon_amine}", method, mode="pair",
                        backend=backend, sampler_tag=tag),
                    canon_ald, canon_amine)
        dim = dimer_mod.make_dimer(canon_ald, canon_amine)
        _, _, x_part = engine.resolve_x(
            x_type, dim["smiles"], solvent_id=solvent_id,
            ald2_smiles=ald2_smiles, amine2_smiles=amine2_smiles,
            custom_smiles=custom_smiles)
        return (dft_cache.cache_key(dim["smiles"], x_part, method,
                                    backend=backend, sampler_tag=tag),
                canon_ald, canon_amine)
    except Exception:
        return None


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _run_job(job_id: str) -> None:
    with _LOCK:
        job = _JOBS[job_id]
    ald_smiles = job["ald_smiles_input"]
    amine_smiles = job["amine_smiles_input"]
    method = job["method"]
    x_type = job.get("x_type") or "self_stack"
    mode = job.get("mode") or "dimer"
    backend = job.get("backend") or "xtb"
    n_samples = job.get("n_samples")
    if backend == "psi4":
        method = psi4_backend.resolve_method_key(method)

    def on_stage(hint: str) -> None:
        with _LOCK:
            j = _JOBS.get(job_id)
            if j:
                j["progress_hint"] = hint

    with _LOCK:
        job["status"] = "running"
        job["progress_hint"] = "正在准备计算…"

    log_base = {
        "smiles_a": ald_smiles, "smiles_b": amine_smiles,
        "x_type": x_type, "method": method, "mode": mode, "backend": backend,
        "n_samples": n_samples,
    }

    try:
        if backend == "psi4":
            # 精度档：真 DFT（ωB97X-D3BJ/def2-SVP 或 B3LYP/6-31G(d,p) + BSSE）
            if mode == "pair":
                result = psi4_backend.compute_pair_binding_psi4(
                    ald_smiles, amine_smiles, method, on_stage=on_stage,
                    n_samples=n_samples)
            else:
                result = psi4_backend.compute_binding_psi4(
                    ald_smiles, amine_smiles, method,
                    x_type=x_type,
                    solvent_id=job.get("solvent_id"),
                    ald2_smiles=job.get("ald2_smiles"),
                    amine2_smiles=job.get("amine2_smiles"),
                    custom_smiles=job.get("custom_smiles"),
                    on_stage=on_stage, n_samples=n_samples)
        elif mode == "pair":
            # 任意双分子模式：A···B 直接结合，跳过二聚体生成与 X 解析
            result = engine.compute_pair_binding(
                ald_smiles, amine_smiles, method, on_stage=on_stage,
                n_samples=n_samples)
        else:
            result = engine.compute_binding(
                ald_smiles, amine_smiles, method,
                x_type=x_type,
                solvent_id=job.get("solvent_id"),
                ald2_smiles=job.get("ald2_smiles"),
                amine2_smiles=job.get("amine2_smiles"),
                custom_smiles=job.get("custom_smiles"),
                on_stage=on_stage, n_samples=n_samples)
        key = dft_cache.cache_key(
            result["smiles_a"] if mode == "pair" else result["dimer_smiles"],
            result["x_cache_part"], method, mode=mode, backend=backend,
            sampler_tag=_sampler_tag(backend, method, n_samples))
        dft_cache.save_cache(key, result)
        result["cached"] = False
        # pair 模式无单体组归属，不做收藏联动
        result["favorite"] = None if mode == "pair" else \
            _find_favorite(result["smiles_a"], result["smiles_b"])
        dft_log.log_dft({
            "mode": mode,
            "backend": backend,
            "smiles_a": result["smiles_a"],
            "smiles_b": result["smiles_b"],
            "dimer_smiles": result["dimer_smiles"],
            "dimer_multi_site": result.get("dimer_multi_site", False),
            "dimer_note": result.get("dimer_note"),
            "x_type": result["x_type"],
            "x_smiles": result["x_smiles"],
            "x_description": result["x_description"],
            "x_request": result.get("x_request"),
            "method": method,
            "method_label": result.get("method_label"),
            "status": "done",
            "e_bind_kcal": result["e_bind_kcal"],
            "e_bind_kj": result["e_bind_kj"],
            "gap_ev": result["gap_ev"],
            "dipole_debye": result["dipole_debye"],
            "energies_hartree": result["energies_hartree"],
            "complex_xyz": result["complex_xyz"],
            "fragment_ranges": result.get("fragment_ranges"),
            "sampling": result.get("sampling"),
            "elapsed_sec": result["elapsed_sec"],
        })
        with _LOCK:
            job.update(status="done", progress_hint="计算完成", result=result)
    except engine.DftError as exc:
        msg = str(exc)
        dft_log.log_dft({**log_base, "status": "failed", "error": msg})
        with _LOCK:
            job.update(status="failed", error=msg, progress_hint="计算失败")
    except Exception as exc:  # noqa: BLE001 —— 兜底，绝不把异常吞到线程里
        msg = f"计算出现未预期错误：{type(exc).__name__}: {exc}"
        dft_log.log_dft({**log_base, "status": "failed", "error": msg})
        with _LOCK:
            job.update(status="failed", error=msg, progress_hint="计算失败")
