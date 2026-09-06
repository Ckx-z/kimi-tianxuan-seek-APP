"""版本对比（v1.8.0）：目标版本 vs 基础 v5.4——反馈对逐对打分 + 金标准指标。

运行环境：ANACONDA python（同步子进程，由 /api/gnn/versions/{v}/compare 调用）；
GNN 逐对推理走 dphuanjing。输出 JSON（stdout）。

用法：
    E:\\ANACONDA\\python.exe gnn_training/compare_versions.py --version <v> --repo <root>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _predict(ald: str, amine: str, ckpt: Path, repo: Path,
             python: Path) -> float | None:
    script = repo / "gnn_runtime" / "predict_pair.py"
    result = subprocess.run(
        [str(python), str(script), "--ald", ald, "--amine", amine,
         "--model", str(ckpt), "--mc", "10"],
        cwd=str(script.parent), capture_output=True, timeout=180)
    out = result.stdout.decode("utf-8", errors="replace")
    m = re.search(r"成膜概率\s*[:：]\s*([0-9.]+)", out)
    return float(m.group(1)) if m else None


def _gold(repo: Path) -> dict:
    """金标准 headline 指标（当前进程环境决定 GNN checkpoint）。"""
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "src"))
    from src.predictor import FilmPredictor
    from src.predictor.fusion import headline_score
    gold = json.loads((repo / "data" / "film_gold_standard.json")
                      .read_text(encoding="utf-8"))["pairs"]
    pred = FilmPredictor()
    scored = []
    for p in gold:
        res = pred.predict(p["aldehyde_smiles"], p["amine_smiles"])
        if (res.get("ood") or {}).get("level") == "out":
            scored.append((0.0, float(p["label"])))
            continue
        score, _ = headline_score(res)
        if score is None:
            continue
        scored.append((float(score), float(p["label"])))
    import numpy as np
    scores = np.array([s for s, _ in scored])
    labels = np.array([l for _, l in scored])
    rx = np.argsort(np.argsort(scores))
    ry = np.argsort(np.argsort(labels))
    return {
        "a_min": float(scores[labels == 1.0].min()),
        "c_max": float(scores[labels == 0.0].max()),
        "mae": float(np.mean(np.abs(scores - labels))),
        "spearman": float(np.corrcoef(rx, ry)[0, 1]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--repo", required=True)
    args = ap.parse_args()
    repo = Path(args.repo)
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "src"))
    from src import runtime_config
    from src.predictor import gnn_feedback, gnn_jobs

    python = runtime_config.gnn_python()
    base_ckpt = repo / "models" / "gnn_v5.4" / "v5_model.pt"
    if args.version == "gnn_v5.4":
        target_ckpt = base_ckpt
    else:
        target_ckpt = (runtime_config.user_data_root() / "gnn_models"
                       / args.version / "v5_model.pt")
        if not target_ckpt.is_file():
            target_ckpt = repo / "models" / "gnn_feedback" / args.version / "v5_model.pt"

    rows = gnn_feedback.confirmed_rows()
    pairs = []
    for r in rows:
        base_s = _predict(r["ald_smiles"], r["amine_smiles"], base_ckpt,
                          repo, python)
        tgt_s = _predict(r["ald_smiles"], r["amine_smiles"], target_ckpt,
                         repo, python)
        pairs.append({
            "ald": r["ald_smiles"], "amine": r["amine_smiles"],
            "label": r["label"], "note": r.get("note") or "",
            "gnn_v5.4": base_s, "target": tgt_s,
        })

    # 金标准：基础版（无 env 覆盖） vs 目标版（COF_GNN_CHECKPOINT 覆盖）
    os.environ.pop("COF_GNN_CHECKPOINT", None)
    base_gold = _gold(repo)
    os.environ["COF_GNN_CHECKPOINT"] = str(target_ckpt)
    target_gold = _gold(repo)
    print(json.dumps({
        "version": args.version,
        "pairs": pairs,
        "gold": {"gnn_v5.4": base_gold, "target": target_gold},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
