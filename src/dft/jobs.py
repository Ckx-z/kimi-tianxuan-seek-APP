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
from . import engine
from . import log as dft_log

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


def create_job(smiles_a: str, smiles_b: str, method: str) -> dict:
    """建任务。缓存命中 → 直接 done；否则 pending 并起后台线程。"""
    job_id = uuid.uuid4().hex[:16]
    job = {
        "job_id": job_id,
        "status": "pending",
        "progress_hint": "排队等待计算…",
        "method": method,
        "smiles_a_input": smiles_a,
        "smiles_b_input": smiles_b,
        "result": None,
        "error": None,
        "cached": False,
        "created_at": _utc_now(),
    }
    with _LOCK:
        _JOBS[job_id] = job
        _prune_locked()

    # 规范化失败会在工作线程里变成中文错误；这里先用规范化值探缓存
    canon_a = engine.canonicalize_smiles(smiles_a)
    canon_b = engine.canonicalize_smiles(smiles_b)
    if canon_a and canon_b and method in engine.METHODS:
        key = dft_cache.cache_key(canon_a, canon_b, method)
        hit = dft_cache.load_cache(key)
        if hit is not None:
            result = dict(hit)
            result["cached"] = True
            result["favorite"] = _find_favorite(canon_a, canon_b)
            job.update(status="done", progress_hint="命中缓存，直接返回历史结果",
                       result=result, cached=True)
            return job

    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True,
                         name=f"dft-job-{job_id}")
    t.start()
    return job


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _run_job(job_id: str) -> None:
    with _LOCK:
        job = _JOBS[job_id]
    smiles_a = job["smiles_a_input"]
    smiles_b = job["smiles_b_input"]
    method = job["method"]

    def on_stage(hint: str) -> None:
        with _LOCK:
            j = _JOBS.get(job_id)
            if j:
                j["progress_hint"] = hint

    with _LOCK:
        job["status"] = "running"
        job["progress_hint"] = "正在准备计算…"

    try:
        result = engine.compute_binding(
            smiles_a, smiles_b, method, on_stage=on_stage)
        key = dft_cache.cache_key(result["smiles_a"], result["smiles_b"], method)
        dft_cache.save_cache(key, result)
        result["cached"] = False
        result["favorite"] = _find_favorite(
            result["smiles_a"], result["smiles_b"])
        dft_log.log_dft({
            "smiles_a": result["smiles_a"],
            "smiles_b": result["smiles_b"],
            "method": method,
            "status": "done",
            "e_bind_kcal": result["e_bind_kcal"],
            "e_bind_kj": result["e_bind_kj"],
            "gap_ev": result["gap_ev"],
            "dipole_debye": result["dipole_debye"],
            "energies_hartree": result["energies_hartree"],
            "complex_xyz": result["complex_xyz"],
            "elapsed_sec": result["elapsed_sec"],
        })
        with _LOCK:
            job.update(status="done", progress_hint="计算完成", result=result)
    except engine.DftError as exc:
        msg = str(exc)
        dft_log.log_dft({
            "smiles_a": smiles_a, "smiles_b": smiles_b,
            "method": method, "status": "failed", "error": msg,
        })
        with _LOCK:
            job.update(status="failed", error=msg, progress_hint="计算失败")
    except Exception as exc:  # noqa: BLE001 —— 兜底，绝不把异常吞到线程里
        msg = f"计算出现未预期错误：{type(exc).__name__}: {exc}"
        dft_log.log_dft({
            "smiles_a": smiles_a, "smiles_b": smiles_b,
            "method": method, "status": "failed", "error": msg,
        })
        with _LOCK:
            job.update(status="failed", error=msg, progress_hint="计算失败")
