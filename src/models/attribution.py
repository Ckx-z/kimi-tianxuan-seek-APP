"""COF 成膜预测的多层级归因模块。

提供：
1. 全局特征重要性（SHAP / XGBoost gain）
2. 样本级分组归因：醛 / 胺 / 交互 / 规则
3. 官能团级归因：基于 SMARTS 检测的局部结构贡献
4. 可读的推荐解释
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import shap
from rdkit import Chem

import sys
from pathlib import Path
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# 官能团 SMARTS 模式（用于可读解释）
FUNCTIONAL_GROUPS = {
    # 醛侧
    "醛基": {"smarts": "[CX3H1](=O)[#6]", "side": "aldehyde"},
    "醛邻位 F": {"smarts": "[F][c]cc[C](=O)", "side": "aldehyde"},
    "醛邻位 CF3": {"smarts": "[CX4](F)(F)(F)[c]cc[C](=O)", "side": "aldehyde"},
    # 胺侧
    "伯胺": {"smarts": "[NH2][c]", "side": "amine"},
    "胺邻位 F": {"smarts": "[F][c]cc[NH2]", "side": "amine"},
    # 通用
    "苯环": {"smarts": "c1ccccc1", "side": "both"},
    "炔基": {"smarts": "[C]#[C]", "side": "both"},
    "杂芳环": {"smarts": "[#7,#8,#16;r]", "side": "both"},
    "三氟甲基": {"smarts": "[CX4](F)(F)(F)", "side": "both"},
    "芳香 F": {"smarts": "[F][c]", "side": "both"},
}


def classify_feature(feature_name: str) -> str:
    """把特征名归类到醛/胺/交互/规则。"""
    if feature_name.startswith("ald_"):
        return "aldehyde"
    elif feature_name.startswith("amine_"):
        return "amine"
    elif feature_name.startswith("int_") or feature_name.startswith("pair_"):
        return "interaction"
    elif feature_name.startswith("rule_"):
        return "rules"
    return "other"


def detect_functional_groups(ald_smiles: str, amine_smiles: str) -> Dict[str, Dict[str, bool]]:
    """检测单体中存在的官能团。"""
    result = {"aldehyde": {}, "amine": {}}
    mols = {"aldehyde": Chem.MolFromSmiles(ald_smiles),
            "amine": Chem.MolFromSmiles(amine_smiles)}

    for name, info in FUNCTIONAL_GROUPS.items():
        patt = Chem.MolFromSmarts(info["smarts"])
        if patt is None:
            continue
        side = info["side"]
        for mol_side, mol in mols.items():
            if mol is None:
                continue
            if side == "both" or side == mol_side:
                result[mol_side][name] = bool(mol.HasSubstructMatch(patt))
    return result


def global_shap_summary(model, X: pd.DataFrame, feature_cols: List[str],
                        max_samples: int = 500) -> pd.DataFrame:
    """全局 SHAP 特征重要性。"""
    explainer = shap.TreeExplainer(model)
    X_sub = X[feature_cols].sample(n=min(max_samples, len(X)), random_state=42) if len(X) > max_samples else X[feature_cols]
    shap_values = explainer.shap_values(X_sub)

    mean_abs = np.abs(np.array(shap_values)).mean(axis=0)
    return pd.DataFrame({
        "feature": feature_cols,
        "mean_abs_shap": mean_abs,
        "group": [classify_feature(f) for f in feature_cols],
    }).sort_values("mean_abs_shap", ascending=False)


def group_contributions(shap_values: np.ndarray, feature_cols: List[str]) -> Dict[str, float]:
    """把 SHAP 值按醛/胺/交互/规则分组求和（取绝对值，表示贡献强度）。"""
    groups = {"aldehyde": 0.0, "amine": 0.0, "interaction": 0.0, "rules": 0.0, "other": 0.0}
    for val, feat in zip(shap_values, feature_cols):
        groups[classify_feature(feat)] += abs(float(val))
    total = sum(groups.values())
    if total > 0:
        groups = {k: v / total for k, v in groups.items()}
    return groups


def explain_single(model, feature_cols: List[str], ald_smiles: str, amine_smiles: str) -> Dict:
    """对单个醛-胺组合生成可解释报告。"""
    from features.descriptors import compute_pair_features

    feats = compute_pair_features(ald_smiles, amine_smiles,
                                   use_rules=True, reduced_rules=True, use_interaction=True)
    X = pd.DataFrame([{k: feats.get(k, 0.0) for k in feature_cols}])

    pred = float(model.predict(X)[0])

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)[0]

    # 分组贡献
    groups = group_contributions(shap_values, feature_cols)

    # Top 特征
    feature_df = pd.DataFrame({
        "feature": feature_cols,
        "shap": shap_values,
        "value": X.iloc[0].values,
        "group": [classify_feature(f) for f in feature_cols],
    })
    top_positive = feature_df[feature_df["shap"] > 0].sort_values("shap", ascending=False).head(5)
    top_negative = feature_df[feature_df["shap"] < 0].sort_values("shap").head(5)

    # 官能团检测
    fg = detect_functional_groups(ald_smiles, amine_smiles)
    dominant_side = max(groups, key=lambda k: groups[k] if k in ("aldehyde", "amine") else -1)
    dominant_fgs = [name for name, present in fg.get(dominant_side, {}).items() if present]

    return {
        "ald_smiles": ald_smiles,
        "amine_smiles": amine_smiles,
        "predicted_film_score": pred,
        "group_contributions": groups,
        "dominant_side": dominant_side,
        "dominant_functional_groups": dominant_fgs,
        "top_positive_features": top_positive[["feature", "shap", "value", "group"]].to_dict("records"),
        "top_negative_features": top_negative[["feature", "shap", "value", "group"]].to_dict("records"),
    }


def summarize_pair_interaction(ald_smiles: str, amine_smiles: str,
                                model, feature_cols: List[str]) -> str:
    """生成一段人类可读的归因摘要。"""
    exp = explain_single(model, feature_cols, ald_smiles, amine_smiles)
    lines = [
        f"预测成膜得分: {exp['predicted_film_score']:.3f}",
        f"主导贡献方: {exp['dominant_side']}（醛/胺）",
        f"该单体上的关键官能团: {', '.join(exp['dominant_functional_groups']) or '无特殊官能团'}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    # 简单测试
    import json
    import joblib

    model_path = "models/tree_v2.pkl"
    data = joblib.load(model_path)
    model = data["model"]
    feature_cols = data["feature_cols"]

    ald = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
    amine = "Nc1ccc(N)cc1"
    exp = explain_single(model, feature_cols, ald, amine)
    print(json.dumps(exp, indent=2, ensure_ascii=False))
