"""阶段 15（P2）：训练 5 种子 bagging 集成——tree_v4_ens / tree_v4_noTE_ens。

背景（exp_011 / D26 / WORKFLOW_ALIGNMENT 对齐建议第 3 条）：
App 需要输出「打分 ± 认知不确定度」。方案：多种子 bagging——
同一配置、仅变 random_state 训练 5 个子模型，预测取 mean ± std，
std 作为认知不确定度（模型对样本的分歧程度）。

本脚本：
1. 复用 stage11 特征缓存（stage11_xy_cache.pkl）；
2. 池内臂：v4_mild 配置 + TE（全量拟合）+ 频率降权，5 种子 → models/tree_v4_ens.pkl；
3. 外推臂：v4_mild_noTE 配置（无 TE 列）+ 频率降权，5 种子 → models/tree_v4_noTE_ens.pkl；
4. 自描述格式：{"ensemble": [...], "feature_cols", "metrics", "te_rates"?}，
   TreeFilmPredictor 加载后自动走集成分支（单模型 pkl 向后兼容不受影响）。

用法：
    .venv\\Scripts\\python.exe scripts/stage15_train_ensemble.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import mean_absolute_error
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from features.target_encoding import (  # noqa: E402
    apply_film_rates,
    fit_film_rates,
    frequency_sample_weights,
)
from stage11_dual_holdout import V4_CANDIDATES, load_v3, load_xy, pr_auc  # noqa: E402

MODEL_V4_ENS = PROJECT_ROOT / "models" / "tree_v4_ens.pkl"
MODEL_NO_TE_ENS = PROJECT_ROOT / "models" / "tree_v4_noTE_ens.pkl"

SEEDS = [42, 123, 7, 2026, 555]


def train_members(X: np.ndarray, y: np.ndarray, weights: np.ndarray,
                  params: dict) -> list:
    """同配置 5 种子 bagging 子模型。"""
    members = []
    for seed in SEEDS:
        t0 = time.time()
        m = XGBRegressor(**params, random_state=seed, n_jobs=4)
        m.fit(X, y, sample_weight=weights)
        members.append(m)
        print(f"  seed={seed}: {(time.time() - t0):.1f}s", flush=True)
    return members


def ensemble_in_sample(members: list, X: np.ndarray, y: np.ndarray) -> dict:
    preds = np.vstack([m.predict(X) for m in members])
    mean = preds.mean(axis=0)
    return {
        "pr_auc": pr_auc(y, mean),
        "mae": float(mean_absolute_error(y, mean)),
        "member_std_mean": float(preds.std(axis=0).mean()),
    }


def main() -> None:
    t0 = time.time()
    X_base, y, df = load_xy()
    v3, flags, feature_cols_base = load_v3()
    assert X_base.shape[1] == len(feature_cols_base)
    weights = frequency_sample_weights(df).values
    params = V4_CANDIDATES["v4_mild"]

    # ---- 池内臂：tree_v4_ens（含 TE）----
    print("=== 训练 tree_v4_ens（5 种子，含 TE + 频率降权）===", flush=True)
    te_rates_full = fit_film_rates(df)
    X_te = np.hstack([X_base, apply_film_rates(df, te_rates_full).values])
    members_v4 = train_members(X_te, y, weights, params)
    in_v4 = ensemble_in_sample(members_v4, X_te, y)
    print(f"tree_v4_ens in-sample: {in_v4}", flush=True)
    feature_cols_v4 = feature_cols_base + ["te_ald_film_rate", "te_amine_film_rate"]
    # 指标沿用单模型 tree_v4 的 CV 口径（集成不改变协议，CV 验证见 stage15_ensemble_cv.py）
    v4_single = joblib.load(PROJECT_ROOT / "models" / "tree_v4.pkl")
    metrics_v4 = dict(v4_single["metrics"])
    metrics_v4.update({
        "ensemble": True,
        "n_members": len(SEEDS),
        "seeds": SEEDS,
        "uncertainty": "多种子 bagging：预测 = 成员均值 ± 成员 std（认知不确定度）",
        "in_sample_ensemble": in_v4,
    })
    joblib.dump({
        "ensemble": members_v4,
        "feature_cols": feature_cols_v4,
        "config": v3.get("config"),
        "metrics": metrics_v4,
        "te_rates": te_rates_full,
    }, MODEL_V4_ENS)
    print(f"已保存: {MODEL_V4_ENS}", flush=True)

    # ---- 外推臂：tree_v4_noTE_ens（无 TE 列）----
    print("=== 训练 tree_v4_noTE_ens（5 种子，无 TE + 频率降权）===", flush=True)
    members_no = train_members(X_base, y, weights, params)
    in_no = ensemble_in_sample(members_no, X_base, y)
    print(f"tree_v4_noTE_ens in-sample: {in_no}", flush=True)
    no_single = joblib.load(PROJECT_ROOT / "models" / "tree_v4_noTE.pkl")
    metrics_no = dict(no_single["metrics"])
    metrics_no.update({
        "ensemble": True,
        "n_members": len(SEEDS),
        "seeds": SEEDS,
        "uncertainty": "多种子 bagging：预测 = 成员均值 ± 成员 std（认知不确定度）",
        "in_sample_ensemble": in_no,
    })
    joblib.dump({
        "ensemble": members_no,
        "feature_cols": feature_cols_base,
        "config": v3.get("config"),
        "metrics": metrics_no,
        # 无 te_rates —— 与 tree_v4_noTE.pkl 一致，走无先验路径
    }, MODEL_NO_TE_ENS)
    print(f"已保存: {MODEL_NO_TE_ENS}", flush=True)

    print(f"[done] 总耗时 {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
