"""阶段 13 阶段 B：闭卷 embedding 的树模型评估（逐折重训 GNN 的判定实验）。

背景（exp_009 / D24）：
- 阶段 A：全量 GNN embedding 双留出 0.6824 → 0.844，但含"GNN 见过验证折单体"的记忆
  泄漏通道，只是乐观上界；
- 阶段 B：`stage13b_fold_retrain.py` 对每个双留出折只用训练行从零重训 GNN 并提取
  闭卷 pair_emb；本脚本用**每折对应的闭卷 embedding** 训练/评估 XGBoost
  （v4_mild + 频率降权，与阶段 A 完全同口径），回答：
  "排除记忆后，GNN embedding 迁移是否仍有真实泛化收益？"

判定门槛（与阶段 A 对称，事先约定）：
- 双留出 3 种子均值较闭卷基线（X_base，同折同参）提升 ≥ +0.03 且逐种子方向一致
  → 表征迁移确认有效 → 接入路由两臂重测 + App 集成立项；
- < +0.03 或方向不一致 → 路线证伪关闭（阶段 A 的 +0.16 判为记忆泄漏）。

附赠诊断：折内 GNN 直接预测的闭卷外推 PR-AUC（gnn_direct），回答"GNN 自己在
双未见场景多少分"——树 vs GNN vs 结合的最终对比证据。

用法（.venv；stage13b_fold_retrain.py 产出的 npy 齐全后运行）：
    .venv\\Scripts\\python.exe scripts/stage13b_fold_eval.py --seeds 42
    .venv\\Scripts\\python.exe scripts/stage13b_fold_eval.py --seeds 42,123,7
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.metrics import mean_absolute_error
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from features.target_encoding import frequency_sample_weights  # noqa: E402
from stage11_dual_holdout import (  # noqa: E402
    V4_CANDIDATES,
    dual_holdout_folds,
    fold_summary,
    load_xy,
    pr_auc,
)

EMB_DIR = PROJECT_ROOT / "data" / "interim"
REPORT_JSON = PROJECT_ROOT / "reports" / "gnn_embedding_foldb_eval.json"

GATE_DELTA = 0.03
PHASE_A_PAIR_PLUS = 0.8442  # 阶段 A（含泄漏）参考值
N_FOLDS = 5


def run_seed(seed: int, X_base: np.ndarray, y: np.ndarray, df,
             weights: np.ndarray, params: dict) -> dict | None:
    """单个双留出种子：逐折用闭卷 embedding 训练/评估，返回聚合指标。"""
    folds = dual_holdout_folds(df, n_splits=N_FOLDS, seed=seed)
    emb_paths = [EMB_DIR / f"gnn_emb_foldb_s{seed}_f{k}.npy" for k in range(N_FOLDS)]
    missing = [str(p) for p in emb_paths if not p.exists()]
    if missing:
        print(f"[s{seed}] 缺 {len(missing)} 个闭卷 embedding，跳过本种子：{missing[0]} ...",
              flush=True)
        return None

    variants = ("base", "pair_only", "pair_plus")
    preds = {v: np.full(len(df), np.nan) for v in variants}
    pred_gnn = np.full(len(df), np.nan)
    per_fold = {v: [] for v in variants}
    per_fold_gnn = []

    for k, (tr, va) in enumerate(folds):
        if len(va) == 0:
            continue
        emb = np.load(emb_paths[k])
        assert len(emb) == len(df), f"{emb_paths[k]} 行数与 df 不一致"
        feats = {"base": X_base, "pair_only": emb,
                 "pair_plus": np.hstack([X_base, emb])}
        for v in variants:
            m = XGBRegressor(**params, random_state=42, n_jobs=4)
            m.fit(feats[v][tr], y[tr], sample_weight=weights[tr])
            p = m.predict(feats[v][va])
            preds[v][va] = p
            per_fold[v].append({"fold": k, "n_train": int(len(tr)), "n_val": int(len(va)),
                                "pr_auc": pr_auc(y[va], p),
                                "mae": float(mean_absolute_error(y[va], p))})

        # 附赠诊断：折内 GNN 直接外推（闭卷）
        probs_path = EMB_DIR / f"gnn_valprobs_foldb_s{seed}_f{k}.npy"
        mask_path = EMB_DIR / f"gnn_valmask_foldb_s{seed}_f{k}.npy"
        if probs_path.exists() and mask_path.exists():
            probs = np.load(probs_path)
            mask = np.load(mask_path)
            pred_gnn[va[mask]] = probs
            per_fold_gnn.append({"fold": k, "n_val": int(mask.sum()),
                                 "pr_auc": pr_auc(y[va[mask]], probs),
                                 "mae": float(mean_absolute_error(y[va[mask]], probs))})
        line = " | ".join(f"{v}={per_fold[v][-1]['pr_auc']:.4f}" for v in variants)
        gnn_s = f"gnn={per_fold_gnn[-1]['pr_auc']:.4f}" if per_fold_gnn else "gnn=—"
        print(f"  [s{seed}/f{k}] {line} | {gnn_s}", flush=True)

    out = {"per_fold": {}, "fold_summary": {}, "pooled": {}}
    for v in variants:
        mask_v = ~np.isnan(preds[v])
        out["pooled"][v] = {
            "n_val_covered": int(mask_v.sum()),
            "pr_auc": pr_auc(y[mask_v], preds[v][mask_v]),
            "mae": float(mean_absolute_error(y[mask_v], preds[v][mask_v])),
        }
        out["per_fold"][v] = per_fold[v]
        out["fold_summary"][v] = fold_summary(per_fold[v])
    mask_g = ~np.isnan(pred_gnn)
    if mask_g.sum():
        out["pooled"]["gnn_direct"] = {
            "n_val_covered": int(mask_g.sum()),
            "pr_auc": pr_auc(y[mask_g], pred_gnn[mask_g]),
            "mae": float(mean_absolute_error(y[mask_g], pred_gnn[mask_g])),
        }
        out["per_fold"]["gnn_direct"] = per_fold_gnn
        out["fold_summary"]["gnn_direct"] = fold_summary(per_fold_gnn)
    print(f"[s{seed}] pooled: base={out['pooled']['base']['pr_auc']:.4f} "
          f"pair_only={out['pooled']['pair_only']['pr_auc']:.4f} "
          f"pair_plus={out['pooled']['pair_plus']['pr_auc']:.4f} "
          f"gnn_direct={out['pooled'].get('gnn_direct', {}).get('pr_auc', float('nan')):.4f}",
          flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=str, default="42",
                    help="逗号分隔的双留出种子（缺 embedding 的种子自动跳过）")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    t0 = time.time()
    X_base, y, df = load_xy()
    params = V4_CANDIDATES["v4_mild"]
    weights = frequency_sample_weights(df).values

    results = {
        "protocol": "双留出 5 折（每折 GNN 只用该折训练行从零重训，闭卷 embedding）",
        "config": {"params": {k: v for k, v in params.items()},
                   "use_te": False, "use_freq_weights": True},
        "references": {"phaseA_baseline_dual_3seed": 0.6824,
                       "phaseA_pair_plus_dual_3seed_含泄漏": PHASE_A_PAIR_PLUS},
        "gate": f"双留出 3 种子均值较闭卷基线提升 ≥ +{GATE_DELTA} 且逐种子一致 → 表征迁移确认",
        "seeds": {},
    }

    for seed in seeds:
        print(f"\n=== 双留出 seed={seed}（闭卷）===", flush=True)
        r = run_seed(seed, X_base, y, df, weights, params)
        if r is not None:
            results["seeds"][str(seed)] = r

    # 多种子汇总 + 判定
    done = [s for s in seeds if str(s) in results["seeds"]]
    if done:
        summary = {"n_seeds": len(done), "seeds": done}
        for v in ("base", "pair_only", "pair_plus", "gnn_direct"):
            vals = [results["seeds"][str(s)]["pooled"].get(v, {}).get("pr_auc")
                    for s in done]
            vals = [x for x in vals if x is not None]
            if vals:
                summary[v] = {"values": vals, "mean": float(np.mean(vals))}
        if "base" in summary and "pair_plus" in summary:
            deltas = [p - b for p, b in zip(summary["pair_plus"]["values"],
                                            summary["base"]["values"])]
            mean_delta = summary["pair_plus"]["mean"] - summary["base"]["mean"]
            consistent = all(d > 0 for d in deltas)
            verdict = ("CONFIRMED" if (mean_delta >= GATE_DELTA and consistent)
                       else "FALSIFIED")
            summary["decision"] = {
                "pair_plus_delta_per_seed": deltas, "mean_delta": float(mean_delta),
                "per_seed_consistent": bool(consistent), "verdict": verdict,
                "phaseA_gain_was": float(PHASE_A_PAIR_PLUS - 0.6824),
                "note": "CONFIRMED=闭卷仍显著受益 → 路由接入立项；FALSIFIED=阶段 A 增益判为记忆泄漏，路线关闭",
            }
        results["summary"] = summary

    tmp = REPORT_JSON.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=float)
    tmp.replace(REPORT_JSON)

    print("\n========== 阶段 B 汇总（双留出 PR-AUC，逐种子）==========")
    if "summary" in results:
        s = results["summary"]
        for v in ("base", "pair_only", "pair_plus", "gnn_direct"):
            if v in s:
                print(f"{v:<12} mean={s[v]['mean']:.4f}  values={['%.4f' % x for x in s[v]['values']]}")
        if "decision" in s:
            d = s["decision"]
            print(f"\n判定：mean_delta={d['mean_delta']:+.4f} "
                  f"consistent={d['per_seed_consistent']} → {d['verdict']}")
            print(f"（对照：阶段 A 含泄漏增益 {d['phaseA_gain_was']:+.4f}）")
    print(f"\n报告 -> {REPORT_JSON}，总耗时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
