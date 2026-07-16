"""500 对样本 3D/二聚体描述符小样本验证。

固定随机抽取 500 对代表性样本，对比：
1. 基线（精简规则 + Hadamard 交互，无 3D）
2. +单体 3D 描述符
3. +单体 3D + 二聚体 3D 描述符

结果写入 EXPERIMENTS/exp_002.md。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from models.train import load_config, train


SAMPLE_SIZE = 500
RANDOM_STATE = 42


def sample_500_pairs(df: pd.DataFrame, n: int = SAMPLE_SIZE,
                     random_state: int = RANDOM_STATE) -> pd.DataFrame:
    """分层抽取 n 对样本。

    分层依据：
      - source_db（主要来源）
      - is_film 是否大于 0.5（正负样本平衡）
    """
    df = df.dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).copy()
    df["film_bin"] = (df["is_film"] >= 0.5).astype(int)

    # source_db 大类：保留主要来源，其余合并为 other
    source_counts = df["source_db"].value_counts()
    major_sources = source_counts[source_counts >= 20].index.tolist()
    df["source_strata"] = df["source_db"].where(
        df["source_db"].isin(major_sources), "other"
    )

    df["strata"] = df["source_strata"].astype(str) + "_" + df["film_bin"].astype(str)

    # 按比例分层抽样，若某层不足则取全部
    sampled = df.groupby("strata", group_keys=False).apply(
        lambda x: x.sample(
            n=min(len(x), max(1, int(n * len(x) / len(df)))),
            random_state=random_state,
        )
    )

    # 补齐到 n
    if len(sampled) < n:
        remaining = df.drop(sampled.index)
        extra = remaining.sample(n=n - len(sampled), random_state=random_state)
        sampled = pd.concat([sampled, extra])

    sampled = sampled.sample(frac=1, random_state=random_state).reset_index(drop=True)
    return sampled.drop(columns=["film_bin", "source_strata", "strata"])


def run_3d_ablation(data_path: str | Path, config_path: str | Path) -> list[dict]:
    df_full = pd.read_csv(data_path)
    df = sample_500_pairs(df_full)
    config = load_config(config_path)

    base_kwargs = {
        "use_rules": True,
        "reduced_rules": True,
        "use_interaction": True,
        "use_conditions": False,
        "hard_rule_strategy": "remove_hard_rule",
        "remove_all_rule": False,
    }

    experiments = [
        {
            "name": "baseline_500",
            "desc": "精简规则 + Hadamard 交互，无 3D（500 对）",
            "kwargs": {**base_kwargs, "use_3d": False, "use_dimer": False},
        },
        {
            "name": "monomer3d_500",
            "desc": "基线 + 单体 3D 描述符（500 对）",
            "kwargs": {**base_kwargs, "use_3d": True, "use_dimer": False},
        },
        {
            "name": "dimer3d_500",
            "desc": "基线 + 单体 3D + 二聚体 3D 描述符（500 对）",
            "kwargs": {**base_kwargs, "use_3d": True, "use_dimer": True},
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
        metrics["sample_size"] = SAMPLE_SIZE
        results.append(metrics)
        print(json.dumps(metrics, indent=2, ensure_ascii=False))

    return results


def write_report(results: list[dict], out_path: str | Path) -> None:
    lines = [
        "# 小样本验证报告：3D/二聚体描述符（500 对）",
        "",
        "## 实验目的",
        "",
        "在固定 500 对代表性样本上，快速验证单体 3D 描述符和二聚体 3D 描述符对模型性能的影响。",
        "",
        "## 抽样方法",
        "",
        f"- 总样本：{results[0]['n_samples']} 行",
        f"- 抽样方式：按 source_db 大类 + is_film 二值分层抽样，随机种子 {RANDOM_STATE}",
        "- 分组：留一醛基单体交叉验证（LeaveOneGroupOut）",
        "",
        "## 实验配置",
        "",
        "| 实验 | 描述 | PR-AUC | MAE | NDCG@10 | Hit@5 | 特征数 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['experiment']} | {r['description']} | "
            f"{r['pr_auc']:.4f} | {r['mae']:.4f} | "
            f"{r['ndcg_at_10']:.4f} | {r['hit_at_5']} | "
            f"{r['n_features']} |"
        )

    baseline_pr = results[0]["pr_auc"]
    monomer_pr = results[1]["pr_auc"]
    dimer_pr = results[2]["pr_auc"]

    lines.extend([
        "",
        "## 关键发现",
        "",
        f"- **基线 PR-AUC**: {baseline_pr:.4f}",
        f"- **+单体 3D PR-AUC**: {monomer_pr:.4f}（Δ {monomer_pr - baseline_pr:+.4f}）",
        f"- **+二聚体 3D PR-AUC**: {dimer_pr:.4f}（Δ {dimer_pr - baseline_pr:+.4f}）",
        "",
        "## 结论与下一步",
        "",
        "- 待根据实际运行结果填写",
    ])

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    data_path = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"
    config_path = PROJECT_ROOT / "configs" / "default.yaml"
    out_path = PROJECT_ROOT / "EXPERIMENTS" / "exp_002.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = run_3d_ablation(data_path, config_path)
    write_report(results, out_path)
    print(f"\n报告已保存：{out_path}")


if __name__ == "__main__":
    main()
