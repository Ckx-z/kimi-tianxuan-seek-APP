"""阶段 12 收尾：训练最终 tree_v4_noTE 模型 + 生成路由用训练单体池。

背景（exp_005/exp_006/exp_007 结论汇总）：
- v4_mild_noTE（v4_mild 配置去 TE：频率降权 + 弱正则 + Hadamard 交互）是
  双留出协议下所有模型中的最强者（3 种子均值 PR-AUC 0.6824），
  但此前只作为 CV 变体存在，未保存为独立 pkl；
- 双模型路由（D22）：醛/胺都已见 → tree_v4（TE 先验）；任一未见 → tree_v4_noTE。

本脚本：
1. 复用 stage11 的特征缓存（stage11_xy_cache.pkl）与 v4_mild 参数，
   在全量 3094 行上训练最终 noTE 模型（含频率降权，无 TE 列）；
2. 按 tree_v3/v4 的自描述格式保存 models/tree_v4_noTE.pkl
   （metrics 内含特征开关 + LOGO/双留出多种子指标；不含 te_rates，
   TreeFilmPredictor 加载后自动走无 TE 路径，向后兼容）；
3. 从训练数据生成 models/monomer_pool.json（路由键：训练中出现过的
   醛/胺 SMILES 列表），避免预测时每次读 CSV；
   并与 tree_v4.pkl 的 te_rates 键交叉校验一致。

用法：
    .venv\\Scripts\\python.exe scripts/stage12_train_noTE.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import mean_absolute_error
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from features.target_encoding import frequency_sample_weights  # noqa: E402
from stage11_dual_holdout import (  # noqa: E402
    MODEL_V4,
    REPORT_JSON,
    V4_CANDIDATES,
    load_results,
    load_v3,
    load_xy,
    pr_auc,
)

MODEL_NOTE = PROJECT_ROOT / "models" / "tree_v4_noTE.pkl"
METRICS_JSON = PROJECT_ROOT / "models" / "tree_v4_noTE_metrics.json"
POOL_JSON = PROJECT_ROOT / "models" / "monomer_pool.json"


def build_monomer_pool(df) -> dict:
    """从训练数据生成路由用单体池（训练中出现过的醛/胺 SMILES）。"""
    return {
        "aldehydes": sorted(df["aldehyde_smiles"].unique().tolist()),
        "amines": sorted(df["amine_smiles"].unique().tolist()),
        "n_samples": len(df),
        "source": "data/interim/v5_train_stage1_cond_filled.csv（过滤 hard_rule_sampled 后）",
        "note": "双模型路由键（D22）：醛/胺都在池内 → tree_v4；任一不在 → tree_v4_noTE",
    }


def main() -> None:
    t0 = time.time()
    X_base, y, df = load_xy()
    v3, flags, feature_cols_base = load_v3()
    assert X_base.shape[1] == len(feature_cols_base), "缓存特征列数与 tree_v3 不一致"

    results = load_results()
    note_cv = results["variants"].get("v4_mild_noTE") or {}
    if "logo" not in note_cv or "dual" not in note_cv:
        raise RuntimeError("stage11 报告缺 v4_mild_noTE 变体，先运行 scripts/stage11_dual_holdout.py")
    robust = (results.get("dual_seed_robustness") or {}).get("v4_mild_noTE") or {}

    # ---- 最终模型：全量训练（v4_mild 参数 + 频率降权，无 TE 列）----
    print("=== 训练最终 tree_v4_noTE（全量 3094 行）===", flush=True)
    params = V4_CANDIDATES["v4_mild"]
    weights = frequency_sample_weights(df).values
    final_model = XGBRegressor(**params, random_state=42, n_jobs=4)
    final_model.fit(X_base, y, sample_weight=weights)

    pred_in = final_model.predict(X_base)
    in_sample = {"pr_auc": pr_auc(y, pred_in), "mae": float(mean_absolute_error(y, pred_in))}
    print(f"tree_v4_noTE in-sample: {in_sample}", flush=True)

    metrics = {
        # 与 tree_v3.pkl 对齐的主指标口径（LOGO CV）
        "mae": note_cv["logo"]["mae"],
        "pr_auc": note_cv["logo"]["pr_auc"],
        "n_samples": len(df),
        "n_features": len(feature_cols_base),
        "group_by": "aldehyde",
        **flags,
        "use_conditions": False,
        "rule_neg_strategy": "remove_hard_rule",
        "remove_all_rule": False,
        # 阶段 12 自描述字段
        "use_te": False,
        "use_freq_weights": True,
        "freq_weight_formula": "1/sqrt(ald_freq * amine_freq), 均值归一化",
        "param_set": "v4_mild_noTE",
        "xgb_params": {k: v for k, v in params.items()},
        "logo_cv": note_cv["logo"],
        "dual_holdout_cv": note_cv["dual"],
        "dual_seed_robustness": robust,
        "dual_protocol": "5 折网格分组：验证集的醛和胺均不出现在训练集",
        "in_sample": in_sample,
        "routing_role": "外推臂（D22）：任一单体未见于训练池时使用本模型",
    }
    joblib.dump({
        "model": final_model,
        "feature_cols": feature_cols_base,
        "config": v3.get("config"),
        "metrics": metrics,
        # 注意：不含 te_rates —— TreeFilmPredictor.load() 读到 None，
        # predict() 自动跳过 TE 补列，走 v3 同款无先验路径（向后兼容）
    }, MODEL_NOTE)
    print(f"已保存模型: {MODEL_NOTE}", flush=True)

    with open(METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=float)
    print(f"已保存指标: {METRICS_JSON}", flush=True)

    # ---- 路由用训练单体池 ----
    pool = build_monomer_pool(df)
    # 与 tree_v4.pkl 的 te_rates 键交叉校验（同一训练数据，必须一致）
    v4 = joblib.load(MODEL_V4)
    te = v4.get("te_rates") or {}
    assert set(pool["aldehydes"]) == set((te.get("ald_rate") or {}).keys()), "醛池与 te_rates 不一致"
    assert set(pool["amines"]) == set((te.get("amine_rate") or {}).keys()), "胺池与 te_rates 不一致"
    with open(POOL_JSON, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=1)
    print(f"已保存单体池: {POOL_JSON}（醛 {len(pool['aldehydes'])} / 胺 {len(pool['amines'])}，"
          f"与 tree_v4 te_rates 键校验一致）", flush=True)

    print(f"[done] 耗时 {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
