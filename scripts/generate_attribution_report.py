"""生成树模型的归因报告（默认 tree_v3）。

对指定模型 pkl 做：
1. 全局 SHAP 特征重要性（按醛/胺/交互/规则/3D 分组）
2. 若干代表性单体对的单样本归因
3. 保存为 markdown 报告

特征开关（use_rules / use_3d 等）自动从模型 pkl 内的 metrics 读取，
保证归因时的特征空间与训练一致。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from models.attribution import explain_single, global_shap_summary, summarize_pair_interaction

# featurize_dataframe / compute_pair_features 支持的特征开关
FEAT_FLAG_KEYS = ("use_rules", "reduced_rules", "use_interaction",
                  "use_3d", "use_dimer", "n_confs")

# tree_v2 及更早模型的 pkl 内没有 metrics 开关记录，回退到 v2 配置
DEFAULT_FEAT_FLAGS = {
    "use_rules": True,
    "reduced_rules": True,
    "use_interaction": True,
}

EXAMPLES = [
    # Tp + Pa：经典成膜组合
    ("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1", "Tp + Pa (经典成膜)"),
    # Tp + 含 F 胺
    ("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1cc(F)cc(N)c1", "Tp + 含F二胺"),
    # 小醛 + Pa
    ("O=Cc1ccccc1", "Nc1ccc(N)cc1", "苯甲醛 + Pa (C2+C3 拓扑不匹配)"),
]


def main():
    parser = argparse.ArgumentParser(description="生成树模型 SHAP 归因报告")
    parser.add_argument("--model", default="models/tree_v3.pkl", help="模型 pkl 路径")
    parser.add_argument("--data", default="data/interim/v5_train_stage1_cond_filled.csv",
                        help="用于全局 SHAP 的训练数据")
    parser.add_argument("--out", default=None, help="输出报告路径（默认按模型名推导）")
    args = parser.parse_args()

    model_path = PROJECT_ROOT / args.model
    data = joblib.load(model_path)
    model = data["model"]
    feature_cols = data["feature_cols"]
    metrics = data.get("metrics") or {}

    # 特征开关：优先取 pkl metrics 中的训练配置，缺失则回退 v2 配置
    feat_flags = dict(DEFAULT_FEAT_FLAGS)
    feat_flags.update({k: metrics[k] for k in FEAT_FLAG_KEYS if k in metrics})
    print(f"模型：{model_path.name}，特征开关：{feat_flags}")

    model_name = model_path.stem  # e.g. tree_v3
    report_tag = model_name.replace("tree_", "") if model_name.startswith("tree_") else model_name
    out_path = Path(args.out) if args.out else PROJECT_ROOT / "reports" / f"attribution_{report_tag}.md"
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path

    # 1. 读取训练数据（用于全局 SHAP），过滤方式与训练一致（remove_hard_rule）
    df = pd.read_csv(PROJECT_ROOT / args.data)
    mask = ~df["source_db"].astype(str).str.startswith("hard_rule")
    df = df[mask].dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).reset_index(drop=True)

    from features.descriptors import featurize_dataframe
    X = featurize_dataframe(df, **feat_flags)
    X_num = X.reindex(columns=feature_cols).fillna(0)

    # 2. 全局 SHAP
    shap_df = global_shap_summary(model, X_num, feature_cols, max_samples=500).reset_index(drop=True)
    group_importance = shap_df.groupby("group")["mean_abs_shap"].sum().sort_values(ascending=False)
    total_shap = group_importance.sum()

    # 3D 特征单独汇总（本报告的关注点）
    groups_3d = ["aldehyde_3d", "amine_3d", "dimer_3d"]
    shap_3d = shap_df[shap_df["group"].isin(groups_3d)]
    share_3d = shap_3d["mean_abs_shap"].sum() / total_shap if total_shap > 0 else 0.0

    # 4. 构建报告
    lines = [
        f"# 模型 {report_tag} 归因报告",
        "",
        f"**模型**: `models/{model_path.name}`  ",
        f"**PR-AUC**: {metrics.get('pr_auc', float('nan')):.4f}  ",
        f"**MAE**: {metrics.get('mae', float('nan')):.4f}  ",
        f"**样本数**: {metrics.get('n_samples', 'N/A')}  ",
        f"**特征数**: {metrics.get('n_features', len(feature_cols))}  ",
        f"**特征开关**: {feat_flags}  ",
        "",
        "## 全局特征分组重要性",
        "",
        "| 分组 | 累计 SHAP | 占比 |",
        "|---|---|---|",
    ]
    for group, val in group_importance.items():
        lines.append(f"| {group} | {val:.4f} | {val / total_shap:.1%} |")

    lines.extend([
        "",
        "## 3D 特征贡献",
        "",
        f"3D 特征（醛 3D + 胺 3D + 二聚体 3D）累计占全局 SHAP 的 **{share_3d:.1%}**。",
        "",
        "| 排名 | 3D 特征 | 分组 | 重要性 | 全局排名 |",
        "|---|---|---|---|---|",
    ])
    global_rank = {row["feature"]: i + 1 for i, row in shap_df.iterrows()}
    for i, row in enumerate(shap_3d.head(10).itertuples()):
        lines.append(
            f"| {i + 1} | {row.feature} | {row.group} | "
            f"{row.mean_abs_shap:.4f} | {global_rank[row.feature]} |"
        )

    lines.extend([
        "",
        "## Top-20 全局重要特征",
        "",
        "| 排名 | 特征 | 分组 | 重要性 |",
        "|---|---|---|---|",
    ])
    for i, row in shap_df.head(20).iterrows():
        lines.append(f"| {i + 1} | {row['feature']} | {row['group']} | {row['mean_abs_shap']:.4f} |")

    lines.extend([
        "",
        "## 代表性单体对归因",
        "",
    ])
    explain_kwargs = {k: v for k, v in feat_flags.items() if k in ("use_3d", "use_dimer", "n_confs")}
    for ald, amine, desc in EXAMPLES:
        lines.append(f"### {desc}")
        lines.append(f"- 醛: `{ald}`")
        lines.append(f"- 胺: `{amine}`")
        lines.append("")
        lines.append(summarize_pair_interaction(ald, amine, model, feature_cols, **explain_kwargs))
        lines.append("")
        exp = explain_single(model, feature_cols, ald, amine, **explain_kwargs)
        lines.append("**Top-5 正向特征：**")
        for f in exp["top_positive_features"]:
            lines.append(f"- {f['feature']} ({f['group']}): SHAP={f['shap']:.4f}, value={f['value']:.4f}")
        lines.append("\n**Top-5 负向特征：**")
        for f in exp["top_negative_features"]:
            lines.append(f"- {f['feature']} ({f['group']}): SHAP={f['shap']:.4f}, value={f['value']:.4f}")
        lines.append("")

    # 5. 保存
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"归因报告已保存：{out_path}")


if __name__ == "__main__":
    main()
