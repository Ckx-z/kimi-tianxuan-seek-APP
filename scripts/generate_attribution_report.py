"""生成模型 v2 的归因报告。

对最终模型 tree_v2.pkl 做：
1. 全局 SHAP 特征重要性（按醛/胺/交互/规则分组）
2. 若干代表性单体对的单样本归因
3. 保存为 markdown 报告
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from models.attribution import explain_single, global_shap_summary, summarize_pair_interaction


def main():
    model_path = PROJECT_ROOT / "models" / "tree_v2.pkl"
    data = joblib.load(model_path)
    model = data["model"]
    feature_cols = data["feature_cols"]
    metrics = data["metrics"]

    # 1. 读取训练数据（用于全局 SHAP）
    df = pd.read_csv(PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv")
    mask = ~df["source_db"].astype(str).str.startswith("hard_rule")
    df = df[mask].dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).reset_index(drop=True)

    from features.descriptors import featurize_dataframe
    X = featurize_dataframe(df, use_rules=True, reduced_rules=True, use_interaction=True)
    X_num = X[feature_cols].fillna(0)

    # 2. 全局 SHAP
    shap_df = global_shap_summary(model, X_num, feature_cols, max_samples=500).reset_index(drop=True)
    group_importance = shap_df.groupby("group")["mean_abs_shap"].sum().sort_values(ascending=False)

    # 3. 代表性单体对
    examples = [
        # Tp + Pa：经典成膜组合
        ("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1", "Tp + Pa (经典成膜)"),
        # Tp + 含 F 胺
        ("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1cc(F)cc(N)c1", "Tp + 含F二胺"),
        # 小醛 + Pa
        ("O=Cc1ccccc1", "Nc1ccc(N)cc1", "苯甲醛 + Pa (C2+C3 拓扑不匹配)"),
    ]

    # 4. 构建报告
    lines = [
        "# 模型 v2 归因报告",
        "",
        f"**模型**: `models/tree_v2.pkl`  ",
        f"**PR-AUC**: {metrics['pr_auc']:.4f}  ",
        f"**MAE**: {metrics['mae']:.4f}  ",
        f"**样本数**: {metrics['n_samples']}  ",
        f"**特征数**: {metrics['n_features']}  ",
        "",
        "## 全局特征分组重要性",
        "",
        "| 分组 | 累计 SHAP |",
        "|---|---|",
    ]
    for group, val in group_importance.items():
        lines.append(f"| {group} | {val:.4f} |")

    lines.extend([
        "",
        "## Top-20 全局重要特征",
        "",
        "| 排名 | 特征 | 分组 | 重要性 |",
        "|---|---|---|---|",
    ])
    for i, row in shap_df.head(20).iterrows():
        lines.append(f"| {i+1} | {row['feature']} | {row['group']} | {row['mean_abs_shap']:.4f} |")

    lines.extend([
        "",
        "## 代表性单体对归因",
        "",
    ])
    for ald, amine, desc in examples:
        lines.append(f"### {desc}")
        lines.append(f"- 醛: `{ald}`")
        lines.append(f"- 胺: `{amine}`")
        lines.append("")
        lines.append(summarize_pair_interaction(ald, amine, model, feature_cols))
        lines.append("")
        exp = explain_single(model, feature_cols, ald, amine)
        lines.append("**Top-5 正向特征：**")
        for f in exp["top_positive_features"]:
            lines.append(f"- {f['feature']} ({f['group']}): SHAP={f['shap']:.4f}, value={f['value']:.4f}")
        lines.append("\n**Top-5 负向特征：**")
        for f in exp["top_negative_features"]:
            lines.append(f"- {f['feature']} ({f['group']}): SHAP={f['shap']:.4f}, value={f['value']:.4f}")
        lines.append("")

    # 5. 保存
    out_path = PROJECT_ROOT / "reports" / "attribution_v2.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"归因报告已保存：{out_path}")


if __name__ == "__main__":
    main()
