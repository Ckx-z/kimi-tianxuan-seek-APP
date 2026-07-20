"""预测层：树模型封装（XGBoost/LightGBM）。

使用结构描述符（不含条件，因为条件缺失 86-90%），训练一个快速基线模型。
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.model_selection import LeaveOneGroupOut
from xgboost import XGBRegressor

# 本文件位于 src/predictor/，需要让 src/ 在路径中才能导入 features
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from features.descriptors import featurize_dataframe
from features.fingerprints import featurize_fingerprints
from features.target_encoding import apply_film_rates


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def _get_monomer_group(df: pd.DataFrame, group_by: str = "aldehyde") -> pd.Series:
    """生成留一单体交叉验证的分组标签。"""
    if group_by == "aldehyde":
        return df["aldehyde_smiles"].astype("category").cat.codes
    elif group_by == "amine":
        return df["amine_smiles"].astype("category").cat.codes
    else:
        # 按醛+胺组合分组
        return (df["aldehyde_smiles"] + "_" + df["amine_smiles"]).astype("category").cat.codes


class TreeFilmPredictor:
    """基于树模型的 COF 成膜概率预测器。"""

    def __init__(self, model_path: str | Path | None = None):
        self.model_path = Path(model_path) if model_path else MODELS_DIR / "tree_baseline.pkl"
        self.model = None
        self.feature_cols = None
        # 特征开关（use_rules / use_3d 等），加载模型时从 pkl 内的 metrics 恢复
        self.feature_flags: dict = {}
        # 单体历史成膜率映射表（tree_v4 起存入 pkl；旧 pkl 无此字段 → None）
        self.te_rates: dict | None = None
        # 指纹参数（tree_v5 起存入 pkl，{"kind","radius","n_bits"}；旧 pkl 无此键 → None）
        self.fp_params: dict | None = None

    def train(self, df: pd.DataFrame, group_by: str = "aldehyde") -> dict:
        """训练模型，使用留一单体交叉验证。

        Args:
            df: 包含 aldehyde_smiles, amine_smiles, is_film 的 DataFrame
            group_by: "aldehyde" / "amine" / "pair"

        Returns:
            评估指标 dict
        """
        # 去掉 SMILES 缺失的行，确保特征和标签对齐
        df = df.dropna(subset=["aldehyde_smiles", "amine_smiles"]).reset_index(drop=True)

        # 生成特征
        X = featurize_dataframe(df)
        y = df["is_film"].values

        # 保留数值特征
        exclude = ["aldehyde_smiles", "amine_smiles"]
        self.feature_cols = [c for c in X.columns if c not in exclude and X[c].dtype.kind in "iufc"]
        X_num = X[self.feature_cols].fillna(0)

        # 留一单体 CV
        groups = _get_monomer_group(df, group_by)
        logo = LeaveOneGroupOut()
        preds = pd.Series(index=df.index, dtype=float)

        for train_idx, val_idx in logo.split(X_num, y, groups):
            model = XGBRegressor(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.1,
                reg_lambda=1.0,
                min_child_weight=5,
                objective="reg:squarederror",
                eval_metric="mae",
                random_state=42,
                n_jobs=4,
            )
            model.fit(X_num.iloc[train_idx], y[train_idx])
            preds.iloc[val_idx] = model.predict(X_num.iloc[val_idx])

        # 用全量数据训练最终模型
        self.model = XGBRegressor(
            n_estimators=500,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            min_child_weight=5,
            objective="reg:squarederror",
            eval_metric="mae",
            random_state=42,
            n_jobs=4,
        )
        self.model.fit(X_num, y)
        self.save()

        # 评估
        from sklearn.metrics import mean_absolute_error, average_precision_score

        mae = mean_absolute_error(y, preds)
        # 二值化后计算 PR-AUC
        y_bin = (y >= 0.5).astype(int)
        pred_bin = (preds >= 0.5).astype(int)
        try:
            pr_auc = average_precision_score(y_bin, preds)
        except Exception:
            pr_auc = None

        return {
            "mae": float(mae),
            "pr_auc": float(pr_auc) if pr_auc is not None else None,
            "n_samples": len(df),
            "n_features": len(self.feature_cols),
        }

    def predict(self, df: pd.DataFrame) -> pd.Series:
        """预测成膜概率。"""
        if self.model is None:
            self.load()
        df = df.dropna(subset=["aldehyde_smiles", "amine_smiles"]).reset_index(drop=True)
        X = featurize_dataframe(df, **self.feature_flags)
        # tree_v4 起：pkl 内含 te_rates 时，把单体历史成膜率先验补为特征列
        # （未见过的单体回退全量全局均值；旧 pkl 无 te_rates 则跳过，向后兼容）
        if self.te_rates is not None:
            te_df = apply_film_rates(df, self.te_rates)
            for col in te_df.columns:
                X[col] = te_df[col].values
        # tree_v5 起：pkl 内含 fp_params 时，把醛/胺单体指纹补为特征列
        # （解析失败的单体补 0；旧 pkl 无 fp_params 则跳过，向后兼容）
        if self.fp_params is not None:
            fp_df = featurize_fingerprints(df, **self.fp_params)
            X = pd.concat([X, fp_df], axis=1)
        # reindex 保证列与训练时一致：3D 计算失败的样本/缺失列一律补 0
        X_num = X.reindex(columns=self.feature_cols).fillna(0)
        # 回归输出可能略超出 [0, 1]，按概率语义裁剪
        preds = self.model.predict(X_num).clip(0.0, 1.0)
        return pd.Series(preds, index=df.index, name="film_probability")

    def predict_single(self, ald_smiles: str, amine_smiles: str) -> float:
        """预测单个单体对。"""
        df = pd.DataFrame({"aldehyde_smiles": [ald_smiles], "amine_smiles": [amine_smiles]})
        return float(self.predict(df).iloc[0])

    def save(self) -> None:
        """保存模型。"""
        if self.model is None:
            raise ValueError("模型未训练")
        joblib.dump({"model": self.model, "feature_cols": self.feature_cols}, self.model_path)

    def load(self) -> None:
        """加载模型。"""
        if not self.model_path.exists():
            raise FileNotFoundError(f"找不到模型文件：{self.model_path}。请先训练。")
        data = joblib.load(self.model_path)
        self.model = data["model"]
        self.feature_cols = data["feature_cols"]
        # 从训练指标恢复特征开关（tree_v2 及更早的 pkl 无此记录，保持默认特征）
        metrics = data.get("metrics") or {}
        self.feature_flags = {
            k: metrics[k]
            for k in ("use_rules", "reduced_rules", "use_interaction",
                      "use_3d", "use_dimer", "n_confs")
            if k in metrics
        }
        # tree_v4 起 pkl 内含 target encoding 映射表；旧 pkl 无此键 → None
        self.te_rates = data.get("te_rates")
        # tree_v5 起 pkl 内含指纹参数；旧 pkl 无此键 → None
        self.fp_params = data.get("fp_params")


def train_tree_baseline(data_path: str | Path | None = None) -> TreeFilmPredictor:
    """训练一个树模型基线。"""
    if data_path is None:
        data_path = PROJECT_ROOT / "data" / "raw" / "v5_train_stage1.csv"
    df = pd.read_csv(data_path)
    predictor = TreeFilmPredictor()
    metrics = predictor.train(df, group_by="aldehyde")
    print(f"树模型基线训练完成：{metrics}")
    return predictor


if __name__ == "__main__":
    train_tree_baseline()
