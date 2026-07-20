"""单体历史成膜率先验（target encoding）与频率降权。

把"该单体历史上的平均成膜率"显式作为特征，让模型把统计先验与化学
信息叠加，而不是让化学特征去重新发明它（exp_004：胺频率单特征
PR-AUC 0.864 > tree_v3 的 0.772）。

⚠️ 防泄漏铁律：先验必须在 CV 每个折内只用训练折计算；
最终模型用全量数据计算并把映射表存入 pkl，供预测时使用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 特征名（te_ = target encoding）
TE_ALD = "te_ald_film_rate"
TE_AMINE = "te_amine_film_rate"
FEATURE_NAMES = (TE_ALD, TE_AMINE)


def fit_film_rates(df: pd.DataFrame, label_col: str = "is_film") -> dict:
    """从数据框计算单体成膜率先验映射表。

    Returns:
        {"ald_rate": {smiles: rate}, "amine_rate": {smiles: rate},
         "global_mean": float}
    """
    return {
        "ald_rate": df.groupby("aldehyde_smiles")[label_col].mean().to_dict(),
        "amine_rate": df.groupby("amine_smiles")[label_col].mean().to_dict(),
        "global_mean": float(df[label_col].mean()),
    }


def apply_film_rates(df: pd.DataFrame, rates: dict) -> pd.DataFrame:
    """把先验映射为两列特征，未见过的单体回退全局均值。"""
    gm = rates["global_mean"]
    return pd.DataFrame({
        TE_ALD: df["aldehyde_smiles"].map(rates["ald_rate"]).fillna(gm).values,
        TE_AMINE: df["amine_smiles"].map(rates["amine_rate"]).fillna(gm).values,
    }, index=df.index)


def frequency_sample_weights(df: pd.DataFrame) -> pd.Series:
    """频率降权：w = 1/sqrt(醛频次 × 胺频次)，归一化到均值 1。

    借鉴旧项目 GNN 的频率降权：高频单体对（如被反复测试的明星单体）
    在训练集中占比过大，会让模型过拟合到这些单体的统计规律上。
    """
    ald_freq = df["aldehyde_smiles"].map(df["aldehyde_smiles"].value_counts())
    amine_freq = df["amine_smiles"].map(df["amine_smiles"].value_counts())
    w = 1.0 / np.sqrt(ald_freq.astype(float) * amine_freq.astype(float))
    return w / w.mean()
