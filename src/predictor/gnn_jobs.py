"""GNN 重训任务管理（v1.8.0）：版本 registry + 后台训练 job（subprocess）。

- 版本目录：user_data_root()/gnn_models/<version>/（v5_model.pt + calibrator.pkl
  + retrain_meta.json + guard_report.json）；registry.json 记录 active 指针与
  版本状态（active|rejected|retired）。
- 训练 job：detached subprocess 跑 gnn_training/run_job.py（ANACONDA python
  编排：dphuanjing 微调 → guard 闸门 → registry 更新）；状态读
  jobdir/status.json + 输出目录 progress.jsonl；取消 = taskkill /T。
- 终端用户无 dphuanjing 时 start 直接报错（前端 /env 置灰）。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

GNN_MODELS_DIR = runtime_config.user_data_root() / "gnn_models"
JOBS_DIR = runtime_config.user_data_root() / "gnn_jobs"
REGISTRY_PATH = GNN_MODELS_DIR / "registry.json"
# 发版随包携带的版本目录（frozen 下包内只读；源码态与用户目录同根）
BUNDLED_FEEDBACK_DIR = runtime_config.resource_root() / "models" / "gnn_feedback"
BASE_VERSION = "gnn_v5.4"

_lock = threading.Lock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def _new_version() -> str:
    """新版本号 gnn_v5.5_<ts>（按 registry 已有 v5.x 计数递增）。"""
    reg = load_registry()
    major = 5
    minor = 4
    for v in reg.get("versions") or []:
        name = str(v.get("version") or "")
        if name.startswith("gnn_v5."):
            try:
                minor = max(minor, int(name.split("_")[1][1:].split("_")[0]))
            except (IndexError, ValueError):
                continue
    return f"gnn_v{5}.{minor + 1}_{datetime.now():%Y%m%d_%H%M%S}"


# ---------------------------------------------------------------- registry

def load_registry() -> dict:
    """读 registry（用户目录优先，其次包内随发版目录）。缺省返回空表。"""
    for path in (REGISTRY_PATH,
                 BUNDLED_FEEDBACK_DIR / "registry.json"):
        if path.is_file():
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    return obj
            except Exception as exc:
                logger.warning("registry 读取失败 %s: %s", path, exc)
    return {"active": BASE_VERSION, "versions": []}


def save_registry(reg: dict) -> None:
    GNN_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_name(REGISTRY_PATH.name + ".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REGISTRY_PATH)


def active_version() -> str:
    return str(load_registry().get("active") or BASE_VERSION)


def active_checkpoint() -> Path | None:
    """激活版本的 checkpoint 路径（base 版本返回 None，走默认解析）。"""
    ver = active_version()
    if ver == BASE_VERSION:
        return None
    p = GNN_MODELS_DIR / ver / "v5_model.pt"
    if p.is_file():
        return p
    p2 = BUNDLED_FEEDBACK_DIR / ver / "v5_model.pt"
    return p2 if p2.is_file() else None


def list_versions() -> list[dict]:
    reg = load_registry()
    out = []
    for v in reg.get("versions") or []:
        entry = dict(v)
        version = str(entry.get("version") or "")
        meta_path = GNN_MODELS_DIR / version / "retrain_meta.json"
        if not meta_path.is_file():
            meta_path = BUNDLED_FEEDBACK_DIR / version / "retrain_meta.json"
        if meta_path.is_file():
            try:
                entry["meta"] = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        guard_path = GNN_MODELS_DIR / version / "guard_report.json"
        if not guard_path.is_file():
            guard_path = BUNDLED_FEEDBACK_DIR / version / "guard_report.json"
        if guard_path.is_file():
            try:
                entry["guard"] = json.loads(guard_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        out.append(entry)
    out.sort(key=lambda v: v.get("created_at") or "", reverse=True)
    return out


def activate_version(version: str) -> dict | None:
    """切换/回退激活版本（版本必须存在于 registry）。"""
    if version != BASE_VERSION:
        ok = ((GNN_MODELS_DIR / version / "v5_model.pt").is_file()
              or (BUNDLED_FEEDBACK_DIR / version / "v5_model.pt").is_file())
        if not ok:
            return None
        known = any(str(v.get("version")) == version
                    for v in load_registry().get("versions") or [])
        if not known:
            return None
    with _lock:
        reg = load_registry()
        reg["active"] = version
        save_registry(reg)
        return dict(reg)


# ---------------------------------------------------------------- 训练 job

def env_ready() -> dict:
    """训练环境可用性（前端置灰依据）。"""
    try:
        from src import runtime_config as rc
        py = rc.gnn_python()
        base = (rc.resource_root() / "models" / "gnn_v5.4" / "v5_model.pt").is_file()
        finetune = (rc.resource_root() / "gnn_training" / "finetune.py").is_file()
        return {
            "gnn_python": str(py) if py else None,
            "available": bool(py and py.exists() and base and finetune),
            "reason": ("" if py and py.exists() and base and finetune
                       else "未找到 dphuanjing 推理环境或训练资产：重训入口不可用"),
        }
    except Exception as exc:  # pragma: no cover
        return {"gnn_python": None, "available": False,
                "reason": f"环境探测失败：{type(exc).__name__}"}


def start_retrain(feedback_ids: list[str] | None = None,
                  freeze: int = 2, epochs: int = 30, lr: float = 1e-4,
                  batch_size: int = 64, patience: int = 5,
                  feedback_pos_w: float = 5.0) -> dict:
    """启动微调 job。返回 job 记录；环境缺失/已有运行中 job 抛 RuntimeError。"""
    from . import gnn_feedback as fb

    env = env_ready()
    if not env["available"]:
        raise RuntimeError(env["reason"])
    running = [j for j in list_jobs() if j.get("status") == "running"]
    if running:
        raise RuntimeError(f"已有运行中的重训任务: {running[0]['job_id']}")

    rows = [r for r in fb.confirmed_rows()
            if feedback_ids is None or r["feedback_id"] in feedback_ids]
    if not rows:
        raise RuntimeError("没有已确认的反馈样本，无法启动重训（请先确认反馈）")

    version = _new_version()
    job_id = _new_job_id()
    jobdir = JOBS_DIR / job_id
    jobdir.mkdir(parents=True, exist_ok=True)
    out_dir = GNN_MODELS_DIR / version

    fb_csv = fb.export_feedback_csv(jobdir / "feedback.csv")

    job = {
        "job_id": job_id,
        "version": version,
        "status": "starting",
        "phase": "starting",
        "params": {"freeze": freeze, "epochs": epochs, "lr": lr,
                   "batch_size": batch_size, "patience": patience,
                   "feedback_pos_w": feedback_pos_w},
        "feedback_ids": [r["feedback_id"] for r in rows],
        "feedback_count": len(rows),
        "created_at": _now(),
        "updated_at": _now(),
    }
    _write_status(jobdir, job)

    runner = runtime_config.resource_root() / "gnn_training" / "run_job.py"
    base_csv = (runtime_config.resource_root() / "data" / "interim"
                / "v6_train_stage1.csv")
    base_ckpt = (runtime_config.resource_root() / "models" / "gnn_v5.4"
                 / "v5_model.pt")
    python = runtime_config.app_pythonw() or _find_anaconda_python()
    if python is None:
        raise RuntimeError("未找到应用解释器（ANACONDA python），无法启动训练编排")
    cmd = [
        str(python), str(runner),
        "--feedback-csv", str(fb_csv[0]),
        "--base-csv", str(base_csv),
        "--base-ckpt", str(base_ckpt),
        "--output", str(out_dir),
        "--jobdir", str(jobdir),
        "--version", version,
        "--freeze", str(freeze), "--epochs", str(epochs), "--lr", str(lr),
        "--batch-size", str(batch_size), "--patience", str(patience),
        "--feedback-pos-w", str(feedback_pos_w),
    ]
    log_path = jobdir / "train.log"
    with open(log_path, "a", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd, cwd=str(runtime_config.resource_root()),
            stdout=logf, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           | subprocess.DETACHED_PROCESS
                           | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    job["pid"] = proc.pid
    job["status"] = "running"
    job["phase"] = "data_parse"
    _write_status(jobdir, job)
    return job


def _find_anaconda_python():
    try:
        from src import runtime_config as rc
        return rc.app_pythonw() or rc.resolve_python("app")
    except Exception:
        return None


def _write_status(jobdir: Path, job: dict) -> None:
    job["updated_at"] = _now()
    (jobdir / "status.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_status(jobdir: Path) -> dict | None:
    p = jobdir / "status.json"
    if not p.is_file():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _merge_progress(job: dict) -> dict:
    """把训练进度（progress.jsonl 尾行）合入 job 视图。"""
    out_dir = GNN_MODELS_DIR / str(job.get("version") or "")
    prog = out_dir / "progress.jsonl"
    if prog.is_file():
        try:
            lines = prog.read_text(encoding="utf-8").splitlines()
            if lines:
                last = json.loads(lines[-1])
                for k in ("phase", "epoch", "train_loss", "val_pr_auc",
                          "best_pr_auc", "n_rows", "n_feedback"):
                    if k in last:
                        job[k] = last[k]
        except Exception:
            pass
    return job


def list_jobs() -> list[dict]:
    out = []
    if not JOBS_DIR.is_dir():
        return out
    for d in sorted(JOBS_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        job = _read_status(d)
        if job is None:
            continue
        job = _merge_progress(job)
        out.append(job)
    return out


def get_job(job_id: str) -> dict | None:
    if not job_id.startswith("job_") or "/" in job_id or "\\" in job_id:
        return None
    jobdir = JOBS_DIR / job_id
    if not jobdir.is_dir():
        return None
    job = _read_status(jobdir)
    return _merge_progress(job) if job else None


def job_log_tail(job_id: str, n: int = 40) -> list[str]:
    if not job_id.startswith("job_") or "/" in job_id or "\\" in job_id:
        return []
    log_path = JOBS_DIR / job_id / "train.log"
    if not log_path.is_file():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8",
                                   errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []


def cancel_job(job_id: str) -> bool:
    """取消训练 job（任务组杀进程树；状态标记 cancelled）。"""
    job = get_job(job_id)
    if job is None:
        return False
    pid = job.get("pid")
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        except Exception as exc:
            logger.warning("taskkill 失败: %s", exc)
    if job.get("status") == "running":
        job["status"] = "cancelled"
        job["phase"] = "cancelled"
        _write_status(JOBS_DIR / job_id, job)
    return True
