"""模型 v2 消融实验脚本。

运行多个配置，对比：
A. 基线（原始规则 + 无交互 + 保留 hard_rule_sampled）
B. 精简规则 + 移除 hard_rule_sampled
C. 精简规则 + Hadamard 交互 + 移除 hard_rule_sampled
D. 精简规则 + Hadamard 交互 + 条件特征 + 移除 hard_rule_sampled

结果写入 EXPERIMENTS/exp_001.md。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from models.train import load_config, train


def run_ablation(data_path: str | Path, config_path: str | Path) -> list[dict]:
    df = pd.read_csv(data_path)
    config = load_config(config_path)

    experiments = [
        {
            "name": "A_基线",
            "desc": "原始 34 维规则，无交互，保留 hard_rule_sampled",
            "kwargs": {
                "use_rules": True,
                "reduced_rules": False,
                "use_interaction": False,
                "use_conditions": False,
                "hard_rule_strategy": "keep",
                "remove_all_rule": False,
            },
        },
        {
            "name": "B_精简规则",
            "desc": "仅核心规则，无交互，移除 hard_rule_sampled",
            "kwargs": {
                "use_rules": True,
                "reduced_rules": True,
                "use_interaction": False,
                "use_conditions": False,
                "hard_rule_strategy": "remove_hard_rule",
                "remove_all_rule": False,
            },
        },
        {
            "name": "C_精简规则_Hadamard交互",
            "desc": "核心规则 + 醛胺 Hadamard 交互，移除 hard_rule_sampled",
            "kwargs": {
                "use_rules": True,
                "reduced_rules": True,
                "use_interaction": True,
                "use_conditions": False,
                "hard_rule_strategy": "remove_hard_rule",
                "remove_all_rule": False,
            },
        },
        {
            "name": "D_精简规则_Hadamard交互_条件",
            "desc": "核心规则 + Hadamard 交互 + 条件特征，移除 hard_rule_sampled",
            "kwargs": {
                "use_rules": True,
                "reduced_rules": True,
                "use_interaction": True,
                "use_conditions": True,
                "hard_rule_strategy": "remove_hard_rule",
                "remove_all_rule": False,
            },
        },
    ]

    results = []
    for exp in experiments:
        print(f"\n=== 运行实验：{exp['name']} ===")
        result = train(
            df,
            config,
            group_by="aldehyde",
            model_path=PROJECT_ROOT / "models" / f"tree_v2_{exp['name']}.pkl",
            **exp["kwargs"],
        )
        metrics = result["metrics"]
        metrics["experiment"] = exp["name"]
        metrics["description"] = exp["desc"]
        results.append(metrics)
        print(json.dumps(metrics, indent=2, ensure_ascii=False))

    return results


def write_report(results: list[dict], out_path: str | Path) -> None:
    lines = [
        "# 消融实验报告：模型 v2（规则松绑 + Hadamard 交互）",
        "",
        "## 实验配置",
        "",
        "| 实验 | 描述 | PR-AUC | MAE | NDCG@10 | Hit@5 | n_samples | n_features |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['experiment']} | {r['description']} | "
            f"{r['pr_auc']:.4f} | {r['mae']:.4f} | "
            f"{r['ndcg_at_10']:.4f} | {r['hit_at_5']} | "
            f"{r['n_samples']} | {r['n_features']} |"
        )

    lines.extend([
        "",
        "## 关键发现",
        "",
        "- 待填充",
        "",
        "## 下一步",
        "",
        "- 根据最优配置决定是否接入 3D/二聚体描述符",
    ])

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    data_path = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"
    config_path = PROJECT_ROOT / "configs" / "default.yaml"
    out_path = PROJECT_ROOT / "EXPERIMENTS" / "exp_001.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = run_ablation(data_path, config_path)
    write_report(results, out_path)
    print(f"\n报告已保存：{out_path}")


if __name__ == "__main__":
    main()
