"""COF 成膜树模型训练脚本（v2）。

支持：
- 配置驱动（configs/default.yaml）
- 醛-胺 Hadamard 交互特征开关
- 精简化学规则开关
- 硬规则负样本过滤/降采样
- 留一单体交叉验证（醛/胺/配对）
- 多指标评估：PR-AUC、NDCG@10、Hit@5、MAE
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import average_precision_score, mean_absolute_error, ndcg_score
from sklearn.model_selection import LeaveOneGroupOut
from xgboost import XGBRegressor

# 让 src/ 在路径中
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from features.descriptors import featurize_dataframe


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "configs" / "default.yaml"


def load_config(config_path: str | Path = CONFIG_PATH) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_monomer_group(df: pd.DataFrame, group_by: str = "aldehyde") -> pd.Series:
    """生成留一单体交叉验证的分组标签。"""
    if group_by == "aldehyde":
        return df["aldehyde_smiles"].astype("category").cat.codes
    elif group_by == "amine":
        return df["amine_smiles"].astype("category").cat.codes
    else:
        return (df["aldehyde_smiles"] + "_" + df["amine_smiles"]).astype("category").cat.codes


def filter_rule_generated_negatives(df: pd.DataFrame,
                                    strategy: str = "remove_hard_rule",
                                    remove_all_rule: bool = False) -> pd.DataFrame:
    """处理规则生成的负样本。

    默认仅移除 hard_rule_sampled（用户明确要求），保留 chem_rule_* 等其它
    规则负样本作为真实负例。若设置 remove_all_rule=True，则同时移除所有
    chem_rule_* 来源。

    Args:
        df: 输入数据
        strategy: "keep" 保留；"remove_hard_rule" 仅移除 hard_rule_sampled；
                  "downsample" 随机降采样 hard_rule_sampled 到真实负样本规模
        remove_all_rule: 是否同时移除 chem_rule_* 来源
    """
    if "source_db" not in df.columns or strategy == "keep":
        return df

    source = df["source_db"].astype(str)
    if remove_all_rule:
        rule_mask = source.str.startswith("hard_rule") | source.str.startswith("chem_rule")
    else:
        rule_mask = source.str.startswith("hard_rule")

    if strategy == "remove_hard_rule":
        return df[~rule_mask].reset_index(drop=True)

    if strategy == "downsample":
        keep_df = df[~rule_mask]
        rule_df = df[rule_mask]
        # 真实负样本数
        n_real_neg = len(keep_df[keep_df["is_film"] < 0.5])
        n_rule = len(rule_df)
        sample_size = min(n_rule, max(n_real_neg, 500))
        if sample_size < n_rule:
            rule_df = rule_df.sample(n=sample_size, random_state=42)
        return pd.concat([keep_df, rule_df], ignore_index=True)

    raise ValueError(f"未知的规则负样本策略: {strategy}")


def encode_conditions(df: pd.DataFrame) -> pd.DataFrame:
    """极简条件特征编码（可选）。

    目前仅做温度数值化 + 缺失标记；溶剂/催化剂/路线/界面做 one-hot 低频合并。
    条件不是本项目重点，因此保持简单。
    """
    from data.audit import parse_temperature_to_celsius

    result = df.copy()
    if "temperature" in result.columns:
        temps = result["temperature"].astype(str).apply(parse_temperature_to_celsius)
        result["temperature_c"] = temps.fillna(temps.median())
        result["temperature_known"] = (~temps.isna()).astype(int)

    for col in ["synthesis_route", "interface_type"]:
        if col in result.columns:
            # 低频合并为 "other"
            vc = result[col].astype(str).value_counts()
            top = vc[vc >= 10].index
            result[col] = result[col].astype(str).where(result[col].isin(top), "other")
            dummies = pd.get_dummies(result[col], prefix=col, dummy_na=True)
            result = pd.concat([result, dummies], axis=1)

    # 溶剂/催化剂只做"是否已知"标记，不做 full one-hot（类别太杂乱）
    for col in ["solvent", "catalyst"]:
        if col in result.columns:
            result[f"{col}_known"] = result[col].notna().astype(int)

    return result


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """计算评估指标。"""
    mae = mean_absolute_error(y_true, y_pred)

    # PR-AUC：二值化标签，预测值作为排序分
    y_bin = (y_true >= 0.5).astype(int)
    pr_auc = average_precision_score(y_bin, y_pred)

    # NDCG@10：用连续标签作为相关性
    try:
        ndcg = ndcg_score([y_true], [y_pred], k=10)
    except Exception:
        ndcg = float("nan")

    # Hit@5：预测前 5 中真正正样本数
    top5_idx = np.argsort(y_pred)[-5:][::-1]
    hit5 = int(y_bin[top5_idx].sum())

    return {
        "mae": float(mae),
        "pr_auc": float(pr_auc),
        "ndcg_at_10": float(ndcg),
        "hit_at_5": hit5,
    }


def train(df: pd.DataFrame,
          config: dict,
          group_by: str = "aldehyde",
          use_rules: bool = True,
          reduced_rules: bool = False,
          use_interaction: bool = True,
          use_conditions: bool = False,
          use_3d: bool = False,
          use_dimer: bool = False,
          n_confs: int = 5,
          seed: int = 42,
          monomer_cache_path: str | Path | None = None,
          dimer_cache_path: str | Path | None = None,
          hard_rule_strategy: str = "remove_hard_rule",
          remove_all_rule: bool = False,
          model_path: str | Path | None = None,
          ) -> dict[str, Any]:
    """训练树模型 v2。"""
    df = df.dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).reset_index(drop=True)
    df = filter_rule_generated_negatives(df, strategy=hard_rule_strategy, remove_all_rule=remove_all_rule)

    if use_conditions:
        df = encode_conditions(df)

    # 生成特征
    X = featurize_dataframe(
        df,
        use_rules=use_rules,
        reduced_rules=reduced_rules,
        use_interaction=use_interaction,
        use_3d=use_3d,
        use_dimer=use_dimer,
        n_confs=n_confs,
        seed=seed,
        monomer_cache_path=Path(monomer_cache_path) if monomer_cache_path else None,
        dimer_cache_path=Path(dimer_cache_path) if dimer_cache_path else None,
    )

    # 如果需要条件特征，把条件列拼接到 X（利用保留的原始索引对齐）
    if use_conditions:
        condition_cols = [
            c for c in df.columns
            if c.startswith(("synthesis_route_", "interface_type_"))
            or c in ("temperature_c", "temperature_known", "solvent_known", "catalyst_known")
        ]
        if condition_cols:
            X = X.join(df[condition_cols])

    y = df.loc[X.index, "is_film"].values

    # 保留数值特征；条件 one-hot 已经 numeric
    exclude = ["aldehyde_smiles", "amine_smiles"]
    feature_cols = [c for c in X.columns if c not in exclude and X[c].dtype.kind in "iufcb"]
    X_num = X[feature_cols].fillna(0)

    # 交叉验证
    groups = get_monomer_group(df.loc[X.index], group_by).values
    logo = LeaveOneGroupOut()
    preds = pd.Series(index=df.index, dtype=float)

    tree_cfg = config.get("models", {}).get("tree", {})
    params = tree_cfg.get("params", {})
    cv_params = {**params, "n_estimators": min(params.get("n_estimators", 500), 200)}

    for train_idx, val_idx in logo.split(X_num, y, groups):
        model = XGBRegressor(**cv_params, random_state=42, n_jobs=4)
        model.fit(X_num.iloc[train_idx], y[train_idx])
        preds.iloc[val_idx] = model.predict(X_num.iloc[val_idx])

    # 全量最终模型
    final_model = XGBRegressor(**params, random_state=42, n_jobs=4)
    final_model.fit(X_num, y)

    # 评估
    metrics = evaluate(y, preds)
    metrics["n_samples"] = len(df)
    metrics["n_features"] = len(feature_cols)
    metrics["group_by"] = group_by
    metrics["use_rules"] = use_rules
    metrics["reduced_rules"] = reduced_rules
    metrics["use_interaction"] = use_interaction
    metrics["use_conditions"] = use_conditions
    metrics["use_3d"] = use_3d
    metrics["use_dimer"] = use_dimer
    metrics["n_confs"] = n_confs
    metrics["rule_neg_strategy"] = hard_rule_strategy
    metrics["remove_all_rule"] = remove_all_rule

    # 保存
    if model_path is None:
        model_path = PROJECT_ROOT / "models" / "tree_v2.pkl"
    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": final_model,
        "feature_cols": feature_cols,
        "config": config,
        "metrics": metrics,
    }, model_path)

    return {
        "model": final_model,
        "feature_cols": feature_cols,
        "metrics": metrics,
        "predictions": preds,
        "model_path": str(model_path),
    }


def main():
    parser = argparse.ArgumentParser(description="训练 COF 成膜树模型 v2")
    parser.add_argument("--data", default="data/interim/v5_train_stage1_cond_filled.csv")
    parser.add_argument("--model_path", default="models/tree_v2.pkl")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--group_by", default="aldehyde", choices=["aldehyde", "amine", "pair"])
    parser.add_argument("--no_rules", action="store_true", help="不使用规则特征")
    parser.add_argument("--reduced_rules", action="store_true", help="使用精简规则向量")
    parser.add_argument("--no_interaction", action="store_true", help="不使用 Hadamard 交互特征")
    parser.add_argument("--use_conditions", action="store_true", help="使用反应条件特征")
    parser.add_argument("--use_3d", action="store_true", help="使用单体 3D 描述符")
    parser.add_argument("--use_dimer", action="store_true", help="使用二聚体 3D 描述符")
    parser.add_argument("--n_confs", type=int, default=5, help="3D 构象数")
    parser.add_argument("--monomer_cache", default=None, help="单体 3D 缓存路径")
    parser.add_argument("--dimer_cache", default=None, help="二聚体 3D 缓存路径")
    parser.add_argument("--hard_rule_strategy", default="remove_hard_rule",
                        choices=["keep", "remove_hard_rule", "downsample"],
                        help="hard_rule_sampled 负样本处理策略")
    parser.add_argument("--remove_all_rule", action="store_true",
                        help="同时移除 chem_rule_* 等所有规则生成负样本")
    parser.add_argument("--out_metrics", default="models/tree_v2_metrics.json")
    args = parser.parse_args()

    config = load_config(args.config)
    df = pd.read_csv(args.data)

    result = train(
        df,
        config,
        group_by=args.group_by,
        use_rules=not args.no_rules,
        reduced_rules=args.reduced_rules,
        use_interaction=not args.no_interaction,
        use_conditions=args.use_conditions,
        use_3d=args.use_3d,
        use_dimer=args.use_dimer,
        n_confs=args.n_confs,
        monomer_cache_path=args.monomer_cache,
        dimer_cache_path=args.dimer_cache,
        hard_rule_strategy=args.hard_rule_strategy,
        remove_all_rule=args.remove_all_rule,
        model_path=args.model_path,
    )

    print("训练完成")
    print(json.dumps(result["metrics"], indent=2, ensure_ascii=False))

    with open(args.out_metrics, "w", encoding="utf-8") as f:
        json.dump(result["metrics"], f, indent=2, ensure_ascii=False)
    print(f"指标已保存：{args.out_metrics}")


if __name__ == "__main__":
    main()
