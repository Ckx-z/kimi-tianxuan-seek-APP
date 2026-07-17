"""分析 tree_v3 是否在"数频率"（过拟合/相似度退化检验）。

对比（同一 GroupKFold-10 按醛分组划分）：
1. 模型 CV 预测（XGBoost，与 tree_v3 训练同参数）
2. F0 全局均值基线
3. F1 胺频率基线（val 预测 = 训练折内该胺的标签均值）
4. F2 醛+胺频率基线（各 50%；醛未见过时回退全局均值）

另计算：
- 最终模型 tree_v3 在训练集内的 in-sample 指标（过拟合间隙）
- 模型 CV 预测的方差分解：醛/胺单体的加性效应 vs 配对特异残差
- 模型 CV 预测与胺频率的相关性

结果打印并保存到 reports/tree_vs_frequency_analysis.json。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression
from sklearn.metrics import average_precision_score, mean_absolute_error
from sklearn.model_selection import GroupKFold
from scipy.sparse import csr_matrix
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from features.descriptors import featurize_dataframe


def pr_auc(y, pred):
    return average_precision_score((np.asarray(y) >= 0.5).astype(int), pred)


def additive_r2(values, ald_codes, amine_codes, n_ald, n_amine):
    """values 对 醛one-hot + 胺one-hot 线性回归的 R²（加性单体效应占比）。"""
    rows = np.arange(len(values))
    Xa = csr_matrix((np.ones(len(values)), (rows, ald_codes)), shape=(len(values), n_ald))
    Xb = csr_matrix((np.ones(len(values)), (rows, amine_codes)), shape=(len(values), n_amine))
    from scipy.sparse import hstack
    X = hstack([Xa, Xb]).tocsr()
    reg = LinearRegression()
    reg.fit(X, values)
    return reg.score(X, values)


def main():
    df = pd.read_csv(PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv")
    mask = ~df["source_db"].astype(str).str.startswith("hard_rule")
    df = df[mask].dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).reset_index(drop=True)
    y = df["is_film"].values.astype(float)
    print(f"样本数: {len(df)}")

    # 特征（tree_v3 配置；3D 全部走缓存）
    model_data = joblib.load(PROJECT_ROOT / "models" / "tree_v3.pkl")
    feature_cols = model_data["feature_cols"]
    flags = {k: model_data["metrics"][k] for k in
             ("use_rules", "reduced_rules", "use_interaction", "use_3d", "use_dimer", "n_confs")}
    X = featurize_dataframe(df, **flags)
    X_num = X.reindex(columns=feature_cols).fillna(0).values

    # in-sample（过拟合间隙）
    final_model = model_data["model"]
    pred_in = final_model.predict(X_num)
    in_sample = {"pr_auc": pr_auc(y, pred_in), "mae": mean_absolute_error(y, pred_in)}
    print(f"in-sample: {in_sample}")

    # CV 划分
    with open(PROJECT_ROOT / "configs" / "default.yaml", encoding="utf-8") as f:
        params = yaml.safe_load(f)["models"]["tree"]["params"]
    cv_params = {**params, "n_estimators": min(params.get("n_estimators", 500), 200)}

    groups = df["aldehyde_smiles"].astype("category").cat.codes.values
    gkf = GroupKFold(n_splits=10)

    pred_model = np.full(len(df), np.nan)
    pred_f0 = np.full(len(df), np.nan)
    pred_f1 = np.full(len(df), np.nan)
    pred_f2 = np.full(len(df), np.nan)

    for fold, (tr, va) in enumerate(gkf.split(X_num, y, groups)):
        m = XGBRegressor(**cv_params, random_state=42, n_jobs=4)
        m.fit(X_num[tr], y[tr])
        pred_model[va] = m.predict(X_num[va])

        global_mean = y[tr].mean()
        pred_f0[va] = global_mean
        amine_rate_tr = df.iloc[tr].groupby("amine_smiles")["is_film"].mean()
        ald_rate_tr = df.iloc[tr].groupby("aldehyde_smiles")["is_film"].mean()
        amine_va = df.iloc[va]["amine_smiles"].map(amine_rate_tr).fillna(global_mean)
        ald_va = df.iloc[va]["aldehyde_smiles"].map(ald_rate_tr).fillna(global_mean)
        pred_f1[va] = amine_va.values
        pred_f2[va] = (0.5 * amine_va + 0.5 * ald_va).values
        print(f"fold {fold} done")

    results = {"in_sample": in_sample, "cv_protocol": "GroupKFold-10 by aldehyde, seed default"}
    for name, pred in [("model", pred_model), ("F0_global_mean", pred_f0),
                       ("F1_amine_freq", pred_f1), ("F2_ald_amine_freq", pred_f2)]:
        results[name] = {
            "pr_auc": pr_auc(y, pred),
            "mae": mean_absolute_error(y, pred),
            "spearman": float(spearmanr(y, pred).statistic),
        }
        print(f"{name}: {results[name]}")

    # 方差分解（模型 CV 预测 & 标签）
    ald_codes = df["aldehyde_smiles"].astype("category").cat.codes.values
    amine_codes = df["amine_smiles"].astype("category").cat.codes.values
    n_ald, n_amine = ald_codes.max() + 1, amine_codes.max() + 1
    results["decomposition"] = {
        "model_pred_additive_r2": additive_r2(pred_model, ald_codes, amine_codes, n_ald, n_amine),
        "label_additive_r2": additive_r2(y, ald_codes, amine_codes, n_ald, n_amine),
        "model_pred_vs_amine_freq_spearman": float(spearmanr(pred_model, pred_f1).statistic),
        "model_pred_vs_label_spearman": float(spearmanr(pred_model, y).statistic),
    }
    print("decomposition:", results["decomposition"])

    # 参考：tree_v3 报告的 LOGO CV 指标
    results["reported_logo_cv"] = {"pr_auc": model_data["metrics"]["pr_auc"],
                                   "mae": model_data["metrics"]["mae"]}

    out = PROJECT_ROOT / "reports" / "tree_vs_frequency_analysis.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
