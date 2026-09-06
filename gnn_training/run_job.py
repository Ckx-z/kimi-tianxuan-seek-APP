"""GNN 重训 job 编排器（v1.8.0）：dphuanjing 微调 → 验证闸门 → registry 更新。

由 gnn_jobs.start_retrain 以 detached 进程启动（ANACONDA python），
stdout 已重定向到 jobdir/train.log；状态写 jobdir/status.json（阶段/结果），
微调内部进度由 finetune.py 写 <output>/progress.jsonl。

流程：starting → data_parse/feature_build/fine_tune（finetune 内部阶段）
→ guard → done（passed）/ failed（闸门不通过或异常）。
闸门通过 → registry 新版本 status=active 且 active 指针切换；不通过 →
status=rejected，active 保持不变。tree 模型全程不动。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "gnn_training"))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _status(jobdir: Path, **kw) -> None:
    p = jobdir / "status.json"
    current = {}
    if p.is_file():
        try:
            current = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(kw)
    current["updated_at"] = _now()
    p.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _registry_paths(output: Path) -> Path:
    return output.parent / "registry.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feedback-csv", required=True)
    ap.add_argument("--base-csv", required=True)
    ap.add_argument("--base-ckpt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--jobdir", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--freeze", type=int, default=2)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--feedback-pos-w", type=float, default=5.0)
    ap.add_argument("--baseline",
                    default=str(_REPO / "data" / "film_gold_baseline.json"))
    args = ap.parse_args()

    jobdir = Path(args.jobdir)
    out_dir = Path(args.output)

    try:
        from src import runtime_config
    except ImportError:
        import runtime_config  # type: ignore
    gnn_py = runtime_config.gnn_python()
    if gnn_py is None or not gnn_py.exists():
        _status(jobdir, status="failed", phase="failed",
                error="未找到 dphuanjing 推理环境")
        return

    # ---- 1. 微调（dphuanjing）----
    _status(jobdir, status="running", phase="data_parse")
    finetune = _REPO / "gnn_training" / "finetune.py"
    cmd = [str(gnn_py), str(finetune),
           "--base-csv", args.base_csv,
           "--feedback-csv", args.feedback_csv,
           "--base-ckpt", args.base_ckpt,
           "--output", str(out_dir),
           "--freeze", str(args.freeze),
           "--epochs", str(args.epochs),
           "--lr", str(args.lr),
           "--batch-size", str(args.batch_size),
           "--patience", str(args.patience),
           "--feedback-pos-w", str(args.feedback_pos_w)]
    print(f"[runner] 微调启动: {' '.join(cmd)}", flush=True)
    # runner 由 start_retrain 以 DETACHED|NO_WINDOW 启动（无控制台），
    # 子进程必须显式带同款 flags + stdout 指向日志，否则 torch import 阶段
    # 会被 0xC000013A 杀掉（Windows 控制台初始化问题，真机踩坑）。
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP
             | subprocess.DETACHED_PROCESS
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    r = subprocess.run(cmd, cwd=str(_REPO), timeout=6 * 3600,
                       stdout=sys.stdout, stderr=subprocess.STDOUT,
                       creationflags=flags)
    if r.returncode != 0:
        _status(jobdir, status="failed", phase="failed",
                error=f"微调失败（exit {r.returncode}），见 train.log")
        return

    # ---- 2. 验证闸门（ANACONDA 进程内）----
    _status(jobdir, status="running", phase="guard")
    import guard_eval
    sys.argv = ["guard_eval.py",
                "--ckpt", str(out_dir / "v5_model.pt"),
                "--feedback-csv", args.feedback_csv,
                "--baseline", args.baseline,
                "--out", str(out_dir)]
    try:
        guard_eval.main()
        passed = True
    except SystemExit as exc:
        passed = exc.code == 0
    print(f"[runner] 闸门结果: passed={passed}", flush=True)

    # ---- 3. registry 更新 ----
    reg_path = _registry_paths(out_dir)
    reg = {"active": "gnn_v5.4", "versions": []}
    if reg_path.is_file():
        try:
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:
            reg = {"active": "gnn_v5.4", "versions": []}

    meta_path = out_dir / "retrain_meta.json"
    meta = (json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.is_file() else {})
    guard_path = out_dir / "guard_report.json"
    guard = (json.loads(guard_path.read_text(encoding="utf-8"))
             if guard_path.is_file() else {})
    entry = {
        "version": args.version,
        "base": "gnn_v5.4",
        "status": "active" if passed else "rejected",
        "created_at": _now(),
        "val_pr_auc": (meta.get("metrics") or {}).get("val_pr_auc"),
        "feedback_count": len(meta.get("feedback_keys") or []),
        "gold": (guard.get("checks") or {}).get("gold", {}).get("current"),
    }
    reg["versions"] = [v for v in reg.get("versions") or []
                       if v.get("version") != args.version]
    reg["versions"].append(entry)
    if passed:
        reg["active"] = args.version
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = reg_path.with_name(reg_path.name + ".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(reg_path)

    _status(jobdir, status="done", phase="done", passed=passed,
            version=args.version, val_pr_auc=entry["val_pr_auc"],
            guard_passed=passed)
    print(f"[runner] 完成：{args.version} "
          f"{'已激活' if passed else 'rejected（不激活）'}", flush=True)


if __name__ == "__main__":
    main()
