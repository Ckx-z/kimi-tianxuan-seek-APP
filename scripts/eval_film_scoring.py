"""成膜打分金标准评估（阶段三）：定期对金标准集重算指标（月度评估入口）。

指标（与方案文档 §4 一致）：
- C 类 max score ≤ 0.25（坏样本天花板）
- A 类 min score ≥ 0.60（好样本地板）
- 全量 Spearman（排序质量）≥ 0.85
- MAE（vs 0/0.5/1 三档）≤ 0.20
另输出 per-pair 明细表 + tree/gnn 分量与 score_flags，便于追踪回归。

用法：
    E:\\ANACONDA\\python.exe scripts/eval_film_scoring.py
    E:\\ANACONDA\\python.exe scripts/eval_film_scoring.py --url http://127.0.0.1:8001
    E:\\ANACONDA\\python.exe scripts/eval_film_scoring.py --offline   # 直连 FilmPredictor（无 HTTP）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

GOLD_PATH = PROJECT_ROOT / "data" / "film_gold_standard.json"
REPORT_DIR = PROJECT_ROOT / "data"
DEFAULT_URL = "http://127.0.0.1:8001"


def score_online(url: str, ald: str, amine: str, timeout: int = 300) -> dict:
    import urllib.request
    req = urllib.request.Request(
        url.rstrip("/") + "/api/predict",
        data=json.dumps({"ald_smiles": ald, "amine_smiles": amine,
                         "source": "gold_eval"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def score_offline(ald: str, amine: str) -> dict:
    from predictor import FilmPredictor
    pred = FilmPredictor()
    res = pred.predict(ald, amine)
    return {
        "score": None, "tree_score": res.get("tree_probability"),
        "gnn_score": res.get("gnn_probability"),
        "ood": res.get("ood") or {},
        "pair_seen": res.get("pair_seen", True),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL, help="后端地址（离线模式忽略）")
    ap.add_argument("--offline", action="store_true",
                    help="不走 HTTP，直连本进程 FilmPredictor")
    ap.add_argument("--out", default=None, help="报告输出路径（默认自动命名）")
    args = ap.parse_args()

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    pairs = gold["pairs"]
    rows = []
    t0 = time.time()
    for i, p in enumerate(pairs, 1):
        ald, amine, label = p["aldehyde_smiles"], p["amine_smiles"], p["label"]
        try:
            r = score_offline(ald, amine) if args.offline \
                else score_online(args.url, ald, amine, timeout=300)
        except Exception as exc:
            rows.append({"i": i, **p, "score": None, "error": str(exc)})
            print(f"[{i}/{len(pairs)}] {p['class']} 失败: {exc}")
            continue
        rows.append({
            "i": i, "aldehyde_smiles": ald, "amine_smiles": amine,
            "class": p["class"], "label": label, "note": p["note"],
            "score": r.get("score"), "tree_score": r.get("tree_score"),
            "gnn_score": r.get("gnn_score"),
            "ood_level": (r.get("ood") or {}).get("level"),
            "score_flags": r.get("score_flags"),
        })
        print(f"[{i}/{len(pairs)}] {p['class']} "
              f"label={label:g} score={r.get('score')} tree={r.get('tree_score')} "
              f"gnn={r.get('gnn_score')}", flush=True)

    # ood level == "out"（官能团不适用：无醛基/无胺基/非标准成键基团）时
    # API 有意返回 score=None（前端显示「模型不适用」）。金标准里这类样本
    # 全部是 C 类「明确不可成膜」（label=0.0），按 0.0 计入指标。
    for r in rows:
        if r.get("score") is None and r.get("ood_level") == "out":
            r["score"] = 0.0
            r["tree_score"] = 0.0
            r["gnn_score"] = 0.0
            r["ood_out_zeroed"] = True

    ok = [r for r in rows if isinstance(r.get("score"), (int, float))]
    a = [r for r in ok if r["class"] == "A"]
    b = [r for r in ok if r["class"] == "B"]
    c = [r for r in ok if r["class"] == "C"]
    scores = np.array([r["score"] for r in ok])
    labels = np.array([r["label"] for r in ok])

    # Spearman（分数越高标签越大）
    def spearman(x, y):
        rx = np.argsort(np.argsort(x))
        ry = np.argsort(np.argsort(y))
        return float(np.corrcoef(rx, ry)[0, 1])

    metrics = {
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "n_total": len(pairs), "n_scored": len(ok),
        "a_min": float(min(r["score"] for r in a)) if a else None,
        "a_pass": bool(a and min(r["score"] for r in a) >= 0.60),
        "c_max": float(max(r["score"] for r in c)) if c else None,
        "c_pass": bool(c and max(r["score"] for r in c) <= 0.25),
        "b_span": [float(min(r["score"] for r in b)), float(max(r["score"] for r in b))]
        if b else None,
        "mae": float(np.mean(np.abs(scores - labels))) if len(ok) else None,
        "mae_pass": bool(len(ok) and np.mean(np.abs(scores - labels)) <= 0.20),
        "spearman": spearman(scores, labels) if len(ok) >= 5 else None,
        "spearman_pass": bool(len(ok) >= 5 and spearman(scores, labels) >= 0.85),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    print("\n== 评估结果 ==")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    out = Path(args.out) if args.out else REPORT_DIR / (
        f"film_gold_report_{datetime.now():%Y%m%d_%H%M%S}.json")
    out.write_text(json.dumps(
        {"metrics": metrics, "rows": rows, "gold_version": gold.get("version")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入: {out}")


if __name__ == "__main__":
    main()
