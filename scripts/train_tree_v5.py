"""阶段二（v1.6.1）：tree_v5 双臂 bagging 重训 + 与 v4 同协议对比。

数据：data/interim/v6_train_stage1.csv（613 行噪声标签已修复 + 600 条规则
硬负样本）。特征：v4 同款（rules/interaction/3D/TE/频率降权）+ 新增成网特征
pair_min_functionality / pair_max_functionality / pair_can_network。

协议：
- 评估：GroupShuffleSplit（按醛单体分组，5 折，test 0.2）单模型 CV——
  两臂（v4 复现基线 = 旧特征集；v5 = 新特征集）同协议对比 pr_auc/mae；
- 集成：5 种子 bagging（v4_mild 参数）+ TE 全量拟合 + 频率降权，
  产出 models/tree_v5_ens.pkl 与 models/tree_v5_noTE_ens.pkl（自描述格式，
  TreeFilmPredictor 直接可加载）；
- 护栏：硬负样本/修复噪声样本上 v4 vs v5 分数对比（不可成网组合应低分）。

用法：
    E:\\ANACONDA\\python.exe scripts/train_tree_v5.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, mean_absolute_error
from sklearn.model_selection import GroupShuffleSplit
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from features.descriptors import featurize_dataframe  # noqa: E402
from features.target_encoding import (  # noqa: E402
    apply_film_rates, fit_film_rates, frequency_sample_weights)

CSV_PATH = PROJECT_ROOT / "data" / "interim" / "v6_train_stage1.csv"
MODEL_V5_ENS = PROJECT_ROOT / "models" / "tree_v5_ens.pkl"
MODEL_V5_NOTE_ENS = PROJECT_ROOT / "models" / "tree_v5_noTE_ens.pkl"

SEEDS = [42, 123, 7, 2026, 555]
PARAMS = dict(n_estimators=600, max_depth=5, learning_rate=0.04,
              subsample=0.8, colsample_bytree=0.8, reg_alpha=0.5,
              reg_lambda=3.0, min_child_weight=10,
              objective="reg:squarederror", eval_metric="mae")
# 成网特征（v5 新增；v4 复现基线将其剔除）
NETWORK_COLS = ("pair_min_functionality", "pair_max_functionality",
                "pair_can_network")


def pr_auc(y: np.ndarray, preds: np.ndarray) -> float:
    y_bin = (y >= 0.5).astype(int)
    try:
        return float(average_precision_score(y_bin, preds))
    except Exception:
        return float("nan")


def cv_eval(X: np.ndarray, y: np.ndarray, groups: np.ndarray,
            weights: np.ndarray, params: dict, n_splits: int = 5) -> dict:
    """GroupShuffleSplit（按醛分组）单模型 CV → pr_auc/mae 均值±std。"""
    gss = GroupShuffleSplit(n_splits=n_splits, test_size=0.2, random_state=42)
    prs, maes = [], []
    for train_idx, val_idx in gss.split(X, y, groups):
        m = XGBRegressor(**params, random_state=42, n_jobs=4)
        m.fit(X[train_idx], y[train_idx], sample_weight=weights[train_idx])
        p = m.predict(X[val_idx])
        prs.append(pr_auc(y[val_idx], p))
        maes.append(mean_absolute_error(y[val_idx], p))
    return {"pr_auc_mean": float(np.mean(prs)), "pr_auc_std": float(np.std(prs)),
            "mae_mean": float(np.mean(maes)), "mae_std": float(np.std(maes)),
            "n_splits": n_splits}


def train_bagging(X: np.ndarray, y: np.ndarray, weights: np.ndarray,
                  params: dict) -> list:
    members = []
    for seed in SEEDS:
        t0 = time.time()
        m = XGBRegressor(**params, random_state=seed, n_jobs=4)
        m.fit(X, y, sample_weight=weights)
        members.append(m)
        print(f"  seed={seed}: {time.time() - t0:.1f}s", flush=True)
    return members


def sanity_scores(model_path: Path, df: pd.DataFrame) -> dict:
    """护栏：不可成网组合（硬负样本 + 修复噪声）上的模型分数分布。"""
    from src.predictor.tree_model import TreeFilmPredictor
    tp = TreeFilmPredictor(model_path=model_path)
    tp.load()
    X = tp._featurize(df).values
    preds = (np.vstack([m.predict(X) for m in tp.ensemble]).mean(axis=0)
             if tp.ensemble else tp.model.predict(X))
    return {"mean": float(preds.mean()), "p90": float(np.percentile(preds, 90)),
            "n_ge_05": int((preds >= 0.5).sum()), "n": len(preds)}


def main() -> None:
    t0 = time.time()
    print(f"加载 {CSV_PATH} ...", flush=True)
    df = pd.read_csv(CSV_PATH).dropna(subset=["aldehyde_smiles", "amine_smiles"])
    y = df["is_film"].values.astype(float)
    groups = df["aldehyde_smiles"].astype("category").cat.codes.values
    print(f"样本 {len(df)}，is_film 均值 {y.mean():.3f}", flush=True)

    print("特征化（rules+interaction+3D，同 v4 口径；含新增成网特征）...", flush=True)
    X = featurize_dataframe(df, use_rules=True, reduced_rules=True,
                            use_interaction=True, use_3d=True, n_confs=5)
    X = X.select_dtypes(include=[np.number]).fillna(0)
    print(f"特征矩阵 {X.shape}，耗时 {time.time() - t0:.0f}s", flush=True)

    old_cols = [c for c in X.columns if c not in NETWORK_COLS]
    assert all(c in X.columns for c in NETWORK_COLS), "成网特征缺失"

    weights = frequency_sample_weights(df).values
    te_rates = fit_film_rates(df)
    X_te = np.hstack([X.values, apply_film_rates(df, te_rates).values])
    X_te_old = np.hstack([X[old_cols].values,
                          apply_film_rates(df, te_rates).values])
    feature_cols_v5 = list(X.columns) + ["te_ald_film_rate", "te_amine_film_rate"]
    feature_cols_v5_noTE = list(X.columns)

    # ---- 同协议 CV 对比 ----
    print("== CV：v4 复现基线（旧特征，v6 数据）==", flush=True)
    cv_old = cv_eval(X_te_old, y, groups, weights, PARAMS)
    print(f"  {cv_old}", flush=True)
    print("== CV：v5（新增成网特征，v6 数据）==", flush=True)
    cv_new = cv_eval(X_te, y, groups, weights, PARAMS)
    print(f"  {cv_new}", flush=True)

    # ---- 5 种子 bagging ----
    print("== 训练 tree_v5_ens（含 TE + 频率降权）==", flush=True)
    members_v5 = train_bagging(X_te, y, weights, PARAMS)
    in_v5 = {"pr_auc": pr_auc(y, np.vstack(
        [m.predict(X_te) for m in members_v5]).mean(axis=0)),
        "mae": float(mean_absolute_error(y, np.vstack(
            [m.predict(X_te) for m in members_v5]).mean(axis=0)))}
    print(f"  in-sample: {in_v5}", flush=True)
    joblib.dump({
        "ensemble": members_v5,
        "feature_cols": feature_cols_v5,
        "config": {"param_set": "v4_mild", "xgb_params": PARAMS,
                   "train_data": CSV_PATH.name},
        "metrics": {"use_rules": True, "reduced_rules": True,
                    "use_interaction": True, "use_3d": True,
                    "use_dimer": False, "n_confs": 5,
                    "use_te": True, "use_freq_weights": True,
                    "network_features": list(NETWORK_COLS),
                    "cv_group_shuffle": cv_new,
                    "cv_baseline_old_features": cv_old,
                    "in_sample_ensemble": in_v5,
                    "n_samples": len(df), "ensemble": True,
                    "n_members": len(SEEDS), "seeds": SEEDS},
        "te_rates": te_rates,
    }, MODEL_V5_ENS)
    print(f"已保存: {MODEL_V5_ENS}", flush=True)

    print("== 训练 tree_v5_noTE_ens（无 TE）==", flush=True)
    members_no = train_bagging(X.values, y, weights, PARAMS)
    joblib.dump({
        "ensemble": members_no,
        "feature_cols": feature_cols_v5_noTE,
        "config": {"param_set": "v4_mild", "xgb_params": PARAMS,
                   "train_data": CSV_PATH.name},
        "metrics": {"use_rules": True, "reduced_rules": True,
                    "use_interaction": True, "use_3d": True,
                    "use_dimer": False, "n_confs": 5,
                    "use_te": False, "use_freq_weights": True,
                    "network_features": list(NETWORK_COLS),
                    "n_samples": len(df), "ensemble": True,
                    "n_members": len(SEEDS), "seeds": SEEDS},
    }, MODEL_V5_NOTE_ENS)
    print(f"已保存: {MODEL_V5_NOTE_ENS}", flush=True)

    # ---- 护栏：不可成网组合分数（v4 vs v5）----
    print("== 护栏：不可成网组合（硬负样本+修复噪声）分数 ==", flush=True)
    no_net = X[X["pair_can_network"] == 0.0].index
    sub = df.loc[no_net]
    for name, path in (("tree_v4_ens", PROJECT_ROOT / "models" / "tree_v4_ens.pkl"),
                       ("tree_v5_ens", MODEL_V5_ENS)):
        try:
            s = sanity_scores(path, sub)
            print(f"  {name}: {s}", flush=True)
        except Exception as exc:
            print(f"  {name}: 跳过（{exc}）", flush=True)

    print(f"[done] 总耗时 {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
