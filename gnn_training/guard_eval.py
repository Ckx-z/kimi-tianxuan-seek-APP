"""GNN 微调验证闸门（v1.8.0）：反馈对改善 + 金标准不回退，不达标 rejected。

运行环境：ANACONDA python（FilmPredictor/tree 依赖）；GNN 推理自动走
dphuanjing（runtime_config），被评估 checkpoint 经环境变量
COF_GNN_CHECKPOINT 指定（gnn_model._resolve_runtime 的测试钩子，
最优先级，不改 registry）。

检查项：
1. 反馈对改善：label=1 的反馈对校准后 GNN 分 ≥0.5；label=0 的 ≤0.25；
2. 金标准不回退：eval_film_scoring --offline 全量重跑，a_min/c_max/MAE/
   Spearman 与基线快照对比（容忍 ±0.01，a_min/c_max 允许下浮/上浮 0.01）。
输出：<output>/guard_report.json {passed, checks, baseline, current}。

用法：
    E:\\ANACONDA\\python.exe gnn_training/guard_eval.py \
        --ckpt <gnn_models>/<version>/v5_model.pt \
        --feedback-csv <feedback.csv> --baseline build/gold_baseline.json \
        --out <gnn_models>/<version>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

TOL = 0.01
GOLD_PATH = _REPO / "data" / "film_gold_standard.json"


def _load_gold() -> list[dict]:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))["pairs"]


def _predict_pair(ald: str, amine: str, ckpt: Path) -> float | None:
    """单对校准分（经 gnn_runtime/predict_pair.py，dphuanjing 解释器）。"""
    try:
        from src import runtime_config
    except ImportError:
        import runtime_config  # type: ignore
    python = runtime_config.gnn_python()
    if python is None or not python.exists():
        return None
    script = _REPO / "gnn_runtime" / "predict_pair.py"
    result = subprocess.run(
        [str(python), str(script), "--ald", ald, "--amine", amine,
         "--model", str(ckpt), "--mc", "10"],
        cwd=str(script.parent), capture_output=True, timeout=180)
    out = result.stdout.decode("utf-8", errors="replace")
    m = re.search(r"成膜概率\s*[:：]\s*([0-9.]+)", out)
    if not m:
        return None
    return float(m.group(1))


def _gold_metrics_offline() -> dict | None:
    """金标准全量离线评估（tree + 被评估 GNN 的 headline 口径）。

    COF_GNN_CHECKPOINT 环境变量由调用方设置，_resolve_runtime 优先采用。
    """
    from src.predictor import FilmPredictor
    pred = FilmPredictor()
    pairs = _load_gold()
    scored = []
    for p in pairs:
        res = pred.predict(p["aldehyde_smiles"], p["amine_smiles"])
        tree = res.get("tree_probability")
        gnn = res.get("gnn_probability")
        ood_level = (res.get("ood") or {}).get("level")
        if ood_level == "out":
            scored.append((0.0, float(p["label"])))
            continue
        # headline = max(tree, gnn)（redline 组合按 min(0.25, 0.5*min) 计，
        # 与 fusion.headline_score 同口径；这里用 fusion 直接算）
        from src.predictor.fusion import headline_score
        score, _ = headline_score(res)
        if score is None:
            continue
        scored.append((float(score), float(p["label"])))
    if not scored:
        return None
    scores = np.array([s for s, _ in scored])
    labels = np.array([l for _, l in scored])
    a = scores[labels == 1.0]
    c = scores[labels == 0.0]
    rx = np.argsort(np.argsort(scores))
    ry = np.argsort(np.argsort(labels))
    return {
        "n": len(scored),
        "a_min": float(a.min()) if len(a) else None,
        "c_max": float(c.max()) if len(c) else None,
        "mae": float(np.mean(np.abs(scores - labels))),
        "spearman": float(np.corrcoef(rx, ry)[0, 1]) if len(scores) >= 5 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True, help="待评估 checkpoint 路径")
    ap.add_argument("--feedback-csv", required=True, help="反馈 CSV")
    ap.add_argument("--baseline", default=None,
                    help="金标准基线快照 JSON（缺省用内置基线文件）")
    ap.add_argument("--out", required=True, help="报告输出目录（guard_report.json）")
    args = ap.parse_args()

    ckpt = Path(args.ckpt)
    os.environ["COF_GNN_CHECKPOINT"] = str(ckpt)
    report: dict = {"ckpt": str(ckpt), "passed": False, "checks": {}}

    # ---- 1. 反馈对改善 ----
    import csv as _csv
    with open(args.feedback_csv, "r", encoding="utf-8-sig") as f:
        fb_rows = list(_csv.DictReader(f))
    pos, neg = [], []
    for r in fb_rows:
        score = _predict_pair(r["aldehyde_smiles"], r["amine_smiles"], ckpt)
        entry = {"ald": r["aldehyde_smiles"], "amine": r["amine_smiles"],
                 "label": float(r["is_film"]), "gnn_cal": score}
        (pos if float(r["is_film"]) >= 0.5 else neg).append(entry)
    pos_pass = all((e["gnn_cal"] is not None and e["gnn_cal"] >= 0.5)
                   for e in pos) if pos else True
    neg_pass = all((e["gnn_cal"] is not None and e["gnn_cal"] <= 0.25)
                   for e in neg) if neg else True
    report["checks"]["feedback"] = {
        "passed": bool(pos_pass and neg_pass), "positive": pos, "negative": neg,
        "pos_pass": bool(pos_pass), "neg_pass": bool(neg_pass),
    }

    # ---- 2. 金标准不回退 ----
    baseline_path = Path(args.baseline) if args.baseline else \
        _REPO / "data" / "film_gold_baseline.json"
    baseline = (json.loads(baseline_path.read_text(encoding="utf-8"))
                if baseline_path.is_file() else None)
    current = _gold_metrics_offline()
    gold_pass = True
    if baseline is None or current is None:
        gold_pass = False
        note = "基线/当前金标准评估缺失"
    else:
        regress = []
        for key, dirn in (("a_min", -1), ("c_max", 1),
                          ("mae", 1), ("spearman", -1)):
            b, c = baseline.get(key), current.get(key)
            if b is None or c is None:
                continue
            if dirn * (c - b) > TOL:
                regress.append(f"{key}: {b} → {c}")
        gold_pass = not regress
        note = "; ".join(regress) if regress else "四项指标未回退"
    report["checks"]["gold"] = {
        "passed": gold_pass, "baseline": baseline, "current": current,
        "note": note,
    }

    report["passed"] = bool(report["checks"]["feedback"]["passed"]
                            and report["checks"]["gold"]["passed"])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "guard_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"],
                      "feedback": report["checks"]["feedback"]["passed"],
                      "gold": report["checks"]["gold"]["passed"],
                      "note": report["checks"]["gold"]["note"]},
                     ensure_ascii=False))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
