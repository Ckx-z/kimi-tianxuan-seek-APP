"""对比 GNN v5.3 与 tree_v3 对同一批单体对的打分差异。

按标签分层抽样 24 对（每个标签层级 6 对），分别用两个模型预测，
输出对比表、相关性与平均偏差，保存到 reports/gnn_vs_tree_sample.json。

注意：GNN 走 subprocess（每次约 10 秒），整体约 4-5 分钟。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from predictor.gnn_model import GNNFilmPredictor
from predictor.tree_model import TreeFilmPredictor


def main():
    df = pd.read_csv(PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv")
    mask = ~df["source_db"].astype(str).str.startswith("hard_rule")
    df = df[mask].dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).reset_index(drop=True)

    # 每个标签层级抽 6 对（pandas 3.x 的 groupby.apply 会排除分组列，改用显式循环）
    sample = pd.concat([
        g.sample(n=min(6, len(g)), random_state=42)
        for _, g in df.groupby("is_film")
    ]).reset_index(drop=True)
    print(f"抽样 {len(sample)} 对")

    tree = TreeFilmPredictor(model_path=PROJECT_ROOT / "models" / "tree_v3.pkl")
    tree.load()
    gnn = GNNFilmPredictor()

    rows = []
    for i, row in sample.iterrows():
        ald, amine, label = row["aldehyde_smiles"], row["amine_smiles"], float(row["is_film"])
        tree_pred = tree.predict_single(ald, amine)
        try:
            g = gnn.predict_single(ald, amine)
            gnn_pred, gnn_std = g["probability"], g["std"]
        except Exception as e:
            gnn_pred, gnn_std = None, None
            print(f"[{i}] GNN 失败: {e}")
        rows.append({
            "ald": ald, "amine": amine, "label": label,
            "tree": round(tree_pred, 4),
            "gnn": round(gnn_pred, 4) if gnn_pred is not None else None,
            "gnn_std": round(gnn_std, 4) if gnn_std is not None else None,
        })
        print(f"[{i}] label={label} tree={rows[-1]['tree']} gnn={rows[-1]['gnn']}")

    ok = [r for r in rows if r["gnn"] is not None]
    tree_preds = np.array([r["tree"] for r in ok])
    gnn_preds = np.array([r["gnn"] for r in ok])
    labels = np.array([r["label"] for r in ok])

    summary = {
        "n": len(ok),
        "spearman_tree_gnn": float(spearmanr(tree_preds, gnn_preds).statistic),
        "pearson_tree_gnn": float(pearsonr(tree_preds, gnn_preds).statistic),
        "mean_gap_tree_minus_gnn": float(np.mean(tree_preds - gnn_preds)),
        "mean_abs_gap": float(np.mean(np.abs(tree_preds - gnn_preds))),
        "tree_mean": float(tree_preds.mean()),
        "gnn_mean": float(gnn_preds.mean()),
        "tree_vs_label_spearman": float(spearmanr(tree_preds, labels).statistic),
        "gnn_vs_label_spearman": float(spearmanr(gnn_preds, labels).statistic),
        "rows": rows,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))

    out = PROJECT_ROOT / "reports" / "gnn_vs_tree_sample.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
