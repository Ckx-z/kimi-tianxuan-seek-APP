"""阶段 15（P2）：bagging 集成的 CV 验证——ensemble mean PR-AUC 不低于单模型。

目的（D27 证据要求）：确认 5 种子 bagging 集成均值在 LOGO / 双留出两协议下
PR-AUC 不低于对应单模型（seed=42），集成不损害精度，std 才可放心当不确定度用。

设计：与 stage11 完全同折、同参数、同 TE 防泄漏纪律（每折 TE 只用训练折拟合）；
唯一变量是"单模型（seed42）vs 5 种子均值"。单种子（seed=42 折划分）确认即可。

输出：reports/stage15_ensemble_cv.json

用法：
    .venv\\Scripts\\python.exe scripts/stage15_ensemble_cv.py
"""

from __future__ import annotations

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
    build_fold_features,
    load_xy,
    make_folds,
    pr_auc,
)

from stage15_train_ensemble import SEEDS  # noqa: E402

REPORT_JSON = PROJECT_ROOT / "reports" / "stage15_ensemble_cv.json"


def eval_folds(X_base, y, df, folds, params, use_te, weights, tag):
    """每折训练单模型（seed42）+ 5 种子集成，返回合并 PR-AUC/MAE 对比。

    除 single/ensemble 外同时报告 member_mean（各成员单独评估的均值）——
    "集成是否损害精度"的正确基线是单模型的平均水平，而非某个可能偏运气的种子。
    """
    pred_single = np.full(len(df), np.nan)
    member_preds = {s: np.full(len(df), np.nan) for s in SEEDS}
    for i, (tr, va) in enumerate(folds):
        if len(va) == 0:
            continue
        t0 = time.time()
        X_full = build_fold_features(X_base, df, tr, use_te)
        m_single = XGBRegressor(**params, random_state=42, n_jobs=4)
        m_single.fit(X_full[tr], y[tr], sample_weight=weights[tr])
        pred_single[va] = m_single.predict(X_full[va])
        for seed in SEEDS:
            m = XGBRegressor(**params, random_state=seed, n_jobs=4)
            m.fit(X_full[tr], y[tr], sample_weight=weights[tr])
            member_preds[seed][va] = m.predict(X_full[va])
        ens = np.vstack([member_preds[s][va] for s in SEEDS]).mean(axis=0)
        print(f"  [{tag}] fold {i}: n_val={len(va)} "
              f"single={pr_auc(y[va], pred_single[va]):.4f} "
              f"ens={pr_auc(y[va], ens):.4f} ({time.time() - t0:.1f}s)", flush=True)
    mask = ~np.isnan(pred_single)
    P = np.vstack([member_preds[s][mask] for s in SEEDS])
    pred_ens = P.mean(axis=0)
    member_pr = [pr_auc(y[mask], row) for row in P]
    member_mae = [float(mean_absolute_error(y[mask], row)) for row in P]
    return {
        "n_val_covered": int(mask.sum()),
        "single_pr_auc": pr_auc(y[mask], pred_single[mask]),
        "ensemble_pr_auc": pr_auc(y[mask], pred_ens),
        "member_mean_pr_auc": float(np.mean(member_pr)),
        "member_pr_auc": member_pr,
        "single_mae": float(mean_absolute_error(y[mask], pred_single[mask])),
        "ensemble_mae": float(mean_absolute_error(y[mask], pred_ens)),
        "member_mean_mae": float(np.mean(member_mae)),
    }


def main() -> None:
    t0 = time.time()
    X_base, y, df = load_xy()
    weights = frequency_sample_weights(df).values
    params = V4_CANDIDATES["v4_mild"]
    logo_folds, _ = make_folds(df, y, X_base, dual_seed=42)

    report = {
        "note": "bagging 集成（5 种子均值）vs 单模型（seed42），同折同参同 TE 纪律；"
                "种子划分 seed=42（1 个种子确认，D27）",
        "seeds": SEEDS,
        "arms": {},
    }
    # (use_te, arm_name, label)
    arms = [(True, "tree_v4_ens", "v4_mild 含 TE（池内臂）"),
            (False, "tree_v4_noTE_ens", "v4_mild_noTE（外推臂）")]
    for use_te, arm_name, label in arms:
        print(f"=== {label} ===", flush=True)
        entry = {"use_te": use_te}
        print(" LOGO（GroupKFold-10 按醛）:", flush=True)
        entry["logo"] = eval_folds(X_base, y, df, logo_folds, params, use_te,
                                   weights, f"{arm_name}/LOGO")
        # 双留出：seed42 主划分 + seed7/123 复核划分（seed42 单模型在 seed42 划分上
        # 可能偏运气，多划分复核才能鉴别"集成损害精度"与"单点运气"）
        for ds in (42, 7, 123):
            _, dual_folds_ds = make_folds(df, y, X_base, dual_seed=ds)
            key = "dual" if ds == 42 else f"dual_s{ds}"
            print(f" 双留出（醛+胺 5 折, 划分 seed={ds}）:", flush=True)
            entry[key] = eval_folds(X_base, y, df, dual_folds_ds, params, use_te,
                                    weights, f"{arm_name}/双留出/s{ds}")
        for proto, e in entry.items():
            if proto == "use_te":
                continue
            e["delta_pr_auc"] = e["ensemble_pr_auc"] - e["single_pr_auc"]
            e["delta_vs_member_mean"] = e["ensemble_pr_auc"] - e["member_mean_pr_auc"]
            e["ensemble_not_worse"] = e["delta_pr_auc"] >= 0
            e["ensemble_not_worse_than_member_mean"] = e["delta_vs_member_mean"] >= 0
        report["arms"][arm_name] = entry

    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=float)
    print("\n========== 汇总（PR-AUC：单模型s42 → 集成 | 成员均值）==========")
    for arm_name, entry in report["arms"].items():
        for proto, e in entry.items():
            if proto == "use_te":
                continue
            print(f"{arm_name:<22}{proto:<9}{e['single_pr_auc']:.4f} → "
                  f"{e['ensemble_pr_auc']:.4f}  Δ{e['delta_pr_auc']:+.4f}  "
                  f"成员均值 {e['member_mean_pr_auc']:.4f}  "
                  f"{'OK' if e['ensemble_not_worse'] else 'below-s42'}"
                  f"{' / ≥成员均值' if e['ensemble_not_worse_than_member_mean'] else ''}")
    print(f"\n已保存报告: {REPORT_JSON}（耗时 {(time.time() - t0) / 60:.1f} 分钟）", flush=True)


if __name__ == "__main__":
    main()
