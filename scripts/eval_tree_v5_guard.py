"""阶段二护栏评估：tree_v4_ens vs tree_v5_ens 在不可成网组合与金标准对上的分数。

用法：E:\\ANACONDA\\python.exe scripts/eval_tree_v5_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from predictor.tree_model import TreeFilmPredictor  # noqa: E402

CSV = PROJECT_ROOT / "data" / "interim" / "v6_train_stage1.csv"
MODELS = {
    "tree_v4_ens": PROJECT_ROOT / "models" / "tree_v4_ens.pkl",
    "tree_v5_ens": PROJECT_ROOT / "models" / "tree_v5_ens.pkl",
}

# 金标准对（人工标注三档）
GOLD = [
    ("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1", 1, "Tp+对苯二胺（良）"),
    ("O=Cc1ccc(C=O)cc1", "Nc1ccc(N)cc1", 1, "对苯二甲醛+对苯二胺（良）"),
    ("O=Cc1ccccc1", "Nc1ccccc1", 0, "苯甲醛+苯胺（坏）"),
    ("O=Cc1ccccc1", "NCCN", 0, "苯甲醛+乙二胺（坏）"),
    ("O=Cc1ccccc1", "Nc1ccc(N)cc1", 0, "苯甲醛+对苯二胺（坏）"),
]


def preds(model: TreeFilmPredictor, df: pd.DataFrame) -> np.ndarray:
    X = model._featurize(df).values
    if model.ensemble:
        return np.vstack([m.predict(X) for m in model.ensemble]).mean(axis=0)
    return model.model.predict(X)


def main() -> None:
    df = pd.read_csv(CSV).dropna(subset=["aldehyde_smiles", "amine_smiles"])
    print("== 不可成网组合（硬负样本 + 修复噪声）分数分布 ==")
    X_all = None
    for name, path in MODELS.items():
        m = TreeFilmPredictor(model_path=path)
        m.load()
        if X_all is None:
            from features.descriptors import featurize_dataframe
            print("特征化 v6（用于筛选不可成网行）...")
            X_all = featurize_dataframe(df, use_rules=True, reduced_rules=True,
                                        use_interaction=True, use_3d=False,
                                        n_confs=5)
            X_all = X_all.select_dtypes(include=[np.number]).fillna(0)
        sub = df.loc[X_all.index[X_all["pair_can_network"] == 0.0]]
        p = preds(m, sub)
        print(f"  {name}: n={len(p)} mean={p.mean():.3f} p90={np.percentile(p,90):.3f} "
              f"n>=0.5={int((p>=0.5).sum())}")

    print("\n== 金标准对 ==")
    gold_df = pd.DataFrame(
        [{"aldehyde_smiles": a, "amine_smiles": b} for a, b, _, _ in GOLD])
    for name, path in MODELS.items():
        m = TreeFilmPredictor(model_path=path)
        m.load()
        p = preds(m, gold_df)
        print(f"  {name}:")
        for i, (a, b, label, desc) in enumerate(GOLD):
            print(f"    {desc}: {p[i]:.3f}（期望 {'高' if label else '低'}）")


if __name__ == "__main__":
    main()
