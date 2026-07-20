"""阶段 11：醛胺双留出评估 + 统计先验入模 + 频率降权 + 正则化（tree_v4）。

背景（exp_004）：
- 胺历史成膜率单特征 PR-AUC 0.864 > tree_v3（142 维）的 0.772；
- tree_v3 在样本内 PR-AUC 0.976 vs CV 0.772，间隙 0.20；
- 仅留醛（LOGO）的评估默许了胺统计泄漏（同一胺可同时出现在训练折和验证折）。

本脚本：
1. 双留出 CV：自定义网格分组，同一折内验证集的醛和胺都不在训练集出现；
   与原 LOGO（GroupKFold-10 按醛）同时报告。
2. target encoding：te_ald_film_rate / te_amine_film_rate 两列，
   CV 每折内只用训练折拟合映射表（防泄漏铁律）；
   最终模型用全量数据拟合并把映射表存进 pkl。
3. 频率降权：w = 1/sqrt(醛频次 × 胺频次)，归一化到均值 1
   （频次不含标签信息，可全局计算，不构成泄漏）。
4. 正则化：3 组候选 XGBoost 参数（mild / medium / strong），
   按双留出 PR-AUC 选主，再对胜者做 TE / 权重消融。
5. 输出对比报告 + 保存 models/tree_v4.pkl（含 te_rates 与特征开关，
   保持 tree_v3.pkl 的自描述格式）。

用法（完整运行，约 20-40 分钟）：
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py

分阶段运行（每步独立落盘，可中断续跑；特征化是最慢的一步，按块切分）：
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --featurize 0 --n-chunks 6
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --merge-features --n-chunks 6
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --variant v3_ref
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --variant v4_mild
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --variant v4_medium
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --variant v4_strong
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --variant v4_medium_noTE
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --variant v4_medium_noWeight
    .venv\\Scripts\\python.exe scripts/stage11_dual_holdout.py --finalize
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from rdkit import RDLogger
from sklearn.metrics import average_precision_score, mean_absolute_error
from sklearn.model_selection import GroupKFold
from xgboost import XGBRegressor

RDLogger.DisableLog("rdApp.*")  # 静默 RDKit 解析告警洪流（已知脏 SMILES，特征补 0）

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from features.descriptors import featurize_dataframe
from features.target_encoding import (
    FEATURE_NAMES as TE_FEATURES,
    apply_film_rates,
    fit_film_rates,
    frequency_sample_weights,
)

DATA_PATH = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"
MODEL_V3 = PROJECT_ROOT / "models" / "tree_v3.pkl"
MODEL_V4 = PROJECT_ROOT / "models" / "tree_v4.pkl"
REPORT_JSON = PROJECT_ROOT / "reports" / "stage11_dual_holdout.json"
METRICS_JSON = PROJECT_ROOT / "models" / "tree_v4_metrics.json"
FEAT_DIR = PROJECT_ROOT / "data" / "interim" / "stage11_feat_chunks"
XY_CACHE = PROJECT_ROOT / "data" / "interim" / "stage11_xy_cache.pkl"

# tree_v3 原始参数（configs/default.yaml），作为参照
with open(PROJECT_ROOT / "configs" / "default.yaml", encoding="utf-8") as f:
    V3_PARAMS = yaml.safe_load(f)["models"]["tree"]["params"]

# tree_v4 候选正则化参数
V4_CANDIDATES = {
    "v4_mild": dict(n_estimators=600, max_depth=5, learning_rate=0.04,
                    subsample=0.8, colsample_bytree=0.8,
                    reg_alpha=0.5, reg_lambda=3.0, min_child_weight=10,
                    objective="reg:squarederror", eval_metric="mae"),
    "v4_medium": dict(n_estimators=800, max_depth=4, learning_rate=0.03,
                      subsample=0.7, colsample_bytree=0.7,
                      reg_alpha=1.0, reg_lambda=5.0, min_child_weight=10,
                      objective="reg:squarederror", eval_metric="mae"),
    "v4_strong": dict(n_estimators=1000, max_depth=3, learning_rate=0.03,
                      subsample=0.7, colsample_bytree=0.6,
                      reg_alpha=2.0, reg_lambda=10.0, min_child_weight=20,
                      objective="reg:squarederror", eval_metric="mae"),
}


def pr_auc(y, pred):
    return float(average_precision_score((np.asarray(y) >= 0.5).astype(int), pred))


# ---------------------------------------------------------------- 数据与特征

def load_df_y() -> tuple[pd.DataFrame, np.ndarray]:
    """与 analyze_tree_vs_frequency.py 相同的加载与过滤逻辑。"""
    df = pd.read_csv(DATA_PATH)
    mask = ~df["source_db"].astype(str).str.startswith("hard_rule")
    df = df[mask].dropna(subset=["aldehyde_smiles", "amine_smiles", "is_film"]).reset_index(drop=True)
    y = df["is_film"].values.astype(float)
    return df, y


def load_v3() -> tuple[dict, dict, list]:
    v3 = joblib.load(MODEL_V3)
    flags = {k: v3["metrics"][k] for k in
             ("use_rules", "reduced_rules", "use_interaction", "use_3d", "use_dimer", "n_confs")}
    return v3, flags, list(v3["feature_cols"])


def featurize_chunk(chunk_idx: int, n_chunks: int) -> None:
    """特征化第 chunk_idx 块（按行切分，行间独立），落盘到 FEAT_DIR。"""
    df, _ = load_df_y()
    _, flags, _ = load_v3()
    FEAT_DIR.mkdir(parents=True, exist_ok=True)
    idx = np.array_split(np.arange(len(df)), n_chunks)[chunk_idx]
    t0 = time.time()
    X = featurize_dataframe(df.iloc[idx], **flags)
    out = FEAT_DIR / f"chunk_{chunk_idx}.pkl"
    X.to_pickle(out)
    print(f"[featurize] chunk {chunk_idx}/{n_chunks}: {len(X)} 行, "
          f"{time.time() - t0:.1f}s -> {out}", flush=True)


def merge_features(n_chunks: int) -> None:
    """合并全部特征块，对齐 tree_v3 的 142 列，缓存 X_base / y / df。"""
    df, y = load_df_y()
    _, _, cols = load_v3()
    parts = []
    for i in range(n_chunks):
        p = FEAT_DIR / f"chunk_{i}.pkl"
        if not p.exists():
            raise FileNotFoundError(f"缺特征块：{p}，先运行 --featurize {i}")
        parts.append(pd.read_pickle(p))
    X = pd.concat(parts).sort_index()
    assert len(X) == len(df) and (X.index == df.index).all(), "特征块合并后与 df 不对齐"
    X_base = X.reindex(columns=cols).fillna(0).values
    joblib.dump({"X_base": X_base, "y": y, "df": df}, XY_CACHE)
    print(f"[merge] X_base {X_base.shape} -> {XY_CACHE}", flush=True)


def load_xy() -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    if not XY_CACHE.exists():
        raise FileNotFoundError(f"缺特征缓存：{XY_CACHE}，先运行 --featurize/--merge-features")
    c = joblib.load(XY_CACHE)
    return c["X_base"], c["y"], c["df"]


# ---------------------------------------------------------------- 折划分

def dual_holdout_folds(df: pd.DataFrame, n_splits: int = 5, seed: int = 42):
    """醛胺双留出折：把唯一醛、唯一胺分别（按频次贪心均衡）分到 K 组，
    第 k 折的验证集 = 醛∈组k 且 胺∈组k 的样本；训练集 = 醛∉组k 且 胺∉组k。
    落在"半交叉"位置（只有一个单体属于组 k）的样本该折不使用。

    K=5 时全部折合计验证覆盖率约 1/K（~20% 样本获得双留出预测）。
    """
    rng = np.random.RandomState(seed)

    def assign_groups(values: pd.Series) -> dict:
        vc = values.value_counts()
        order = vc.index.to_numpy()
        rng.shuffle(order)  # 同频次内随机，避免顺序偏置
        order = sorted(order, key=lambda s: -vc[s])
        loads = np.zeros(n_splits)
        mapping = {}
        for s in order:
            k = int(np.argmin(loads))
            mapping[s] = k
            loads[k] += vc[s]
        return mapping

    ald_fold = assign_groups(df["aldehyde_smiles"])
    amine_fold = assign_groups(df["amine_smiles"])
    ald_k = df["aldehyde_smiles"].map(ald_fold).values
    amine_k = df["amine_smiles"].map(amine_fold).values

    folds = []
    for k in range(n_splits):
        val_idx = np.where((ald_k == k) & (amine_k == k))[0]
        train_idx = np.where((ald_k != k) & (amine_k != k))[0]
        folds.append((train_idx, val_idx))
    return folds


def make_folds(df: pd.DataFrame, y: np.ndarray, X_base: np.ndarray):
    ald_groups = df["aldehyde_smiles"].astype("category").cat.codes.values
    logo_folds = list(GroupKFold(n_splits=10).split(X_base, y, ald_groups))
    dual_folds = dual_holdout_folds(df, n_splits=5, seed=42)
    return logo_folds, dual_folds


# ---------------------------------------------------------------- CV 评估

def build_fold_features(X_base: np.ndarray, df: pd.DataFrame,
                        train_idx: np.ndarray, use_te: bool) -> np.ndarray:
    """按防泄漏铁律生成特征：TE 映射表只用训练折拟合。"""
    if not use_te:
        return X_base
    rates = fit_film_rates(df.iloc[train_idx])
    te_all = apply_film_rates(df, rates).values
    return np.hstack([X_base, te_all])


def run_cv(X_base: np.ndarray, y: np.ndarray, df: pd.DataFrame,
           folds, params: dict, use_te: bool, weights: np.ndarray | None,
           tag: str) -> tuple[np.ndarray, dict]:
    """在给定折划分上跑 CV，返回 out-of-fold 预测与 F0/F1 基线指标。"""
    pred = np.full(len(df), np.nan)
    pred_f0 = np.full(len(df), np.nan)
    pred_f1 = np.full(len(df), np.nan)

    for i, (tr, va) in enumerate(folds):
        t0 = time.time()
        if len(va) == 0:
            print(f"  [{tag}] fold {i}: 验证集为空，跳过", flush=True)
            continue
        X_full = build_fold_features(X_base, df, tr, use_te)
        m = XGBRegressor(**params, random_state=42, n_jobs=4)
        fit_kwargs = {}
        if weights is not None:
            fit_kwargs["sample_weight"] = weights[tr]
        m.fit(X_full[tr], y[tr], **fit_kwargs)
        pred[va] = m.predict(X_full[va])

        # 同折基线：F0 全局均值、F1 胺历史成膜率（双留出下胺未见过 → 回退均值）
        gm = y[tr].mean()
        pred_f0[va] = gm
        amine_rate_tr = df.iloc[tr].groupby("amine_smiles")["is_film"].mean()
        pred_f1[va] = df.iloc[va]["amine_smiles"].map(amine_rate_tr).fillna(gm).values
        print(f"  [{tag}] fold {i}: n_train={len(tr)} n_val={len(va)} "
              f"({time.time() - t0:.1f}s)", flush=True)

    mask = ~np.isnan(pred)
    metrics = {
        "n_val_covered": int(mask.sum()),
        "coverage": float(mask.mean()),
        "pr_auc": pr_auc(y[mask], pred[mask]),
        "mae": float(mean_absolute_error(y[mask], pred[mask])),
        "F0_global_mean_pr_auc": pr_auc(y[mask], pred_f0[mask]),
        "F1_amine_freq_pr_auc": pr_auc(y[mask], pred_f1[mask]),
    }
    return pred, metrics


def resolve_variant(name: str) -> tuple[dict, bool, bool]:
    """variant 名 → (xgb 参数, use_te, use_freq_weights)。"""
    if name == "v3_ref":
        return V3_PARAMS, False, False
    if name in V4_CANDIDATES:
        return V4_CANDIDATES[name], True, True
    if name.endswith("_noTE"):
        return V4_CANDIDATES[name[:-len("_noTE")]], False, True
    if name.endswith("_noWeight"):
        return V4_CANDIDATES[name[:-len("_noWeight")]], True, False
    raise KeyError(f"未知 variant：{name}")


# ---------------------------------------------------------------- 结果落盘

def load_results() -> dict:
    if REPORT_JSON.exists():
        with open(REPORT_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"protocols": {"logo": "GroupKFold-10 by aldehyde",
                          "dual": "自定义网格分组 5 折：验证集的醛和胺均不在训练集出现"},
            "variants": {}}


def save_results(results: dict) -> None:
    tmp = REPORT_JSON.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=float)
    tmp.replace(REPORT_JSON)


def cmd_variant(name: str, protocol: str = "both") -> None:
    params, use_te, use_weights = resolve_variant(name)
    X_base, y, df = load_xy()
    weights = frequency_sample_weights(df).values
    w = weights if use_weights else None
    logo_folds, dual_folds = make_folds(df, y, X_base)

    results = load_results()
    results["n_samples"] = len(df)
    results["n_base_features"] = int(X_base.shape[1])
    entry = results["variants"].setdefault(
        name, {"params": params, "use_te": use_te, "use_freq_weights": use_weights})

    print(f"=== {name} (TE={use_te}, 频率降权={use_weights}) ===", flush=True)
    if protocol in ("both", "logo"):
        print(" LOGO（GroupKFold-10 按醛）:", flush=True)
        _, entry["logo"] = run_cv(X_base, y, df, logo_folds, params, use_te, w, f"{name}/LOGO")
        save_results(results)  # 每跑完一个协议就落盘
    if protocol in ("both", "dual"):
        print(" 双留出（醛+胺 5 折）:", flush=True)
        _, entry["dual"] = run_cv(X_base, y, df, dual_folds, params, use_te, w, f"{name}/双留出")
        save_results(results)
    print(f"[variant] {name} 完成 -> {REPORT_JSON}", flush=True)


# ---------------------------------------------------------------- 最终模型

def finalize() -> None:
    t0 = time.time()
    X_base, y, df = load_xy()
    v3, flags, feature_cols_base = load_v3()
    weights = frequency_sample_weights(df).values

    results = load_results()
    done = [n for n in V4_CANDIDATES
            if "logo" in results["variants"].get(n, {}) and "dual" in results["variants"].get(n, {})]
    if len(done) < len(V4_CANDIDATES):
        raise RuntimeError(f"候选未跑完：已完成 {done}，需要 {list(V4_CANDIDATES)}")

    # 按双留出 PR-AUC 选主
    best_name = max(V4_CANDIDATES, key=lambda n: results["variants"][n]["dual"]["pr_auc"])
    best = results["variants"][best_name]
    print(f">>> 最佳候选: {best_name} (双留出 PR-AUC {best['dual']['pr_auc']:.4f})", flush=True)

    # ---- 最终模型：全量训练，TE 映射表用全量数据拟合并存入 pkl ----
    print("=== 训练最终 tree_v4（全量）===", flush=True)
    final_params = V4_CANDIDATES[best_name]
    te_rates_full = fit_film_rates(df)
    X_final = np.hstack([X_base, apply_film_rates(df, te_rates_full).values])
    final_model = XGBRegressor(**final_params, random_state=42, n_jobs=4)
    final_model.fit(X_final, y, sample_weight=weights)

    pred_in = final_model.predict(X_final)
    in_sample = {"pr_auc": pr_auc(y, pred_in), "mae": float(mean_absolute_error(y, pred_in))}
    v3_pred_in = v3["model"].predict(X_base)
    v3_in_sample = {"pr_auc": pr_auc(y, v3_pred_in), "mae": float(mean_absolute_error(y, v3_pred_in))}
    print(f"tree_v4 in-sample: {in_sample}", flush=True)
    print(f"tree_v3 in-sample: {v3_in_sample}", flush=True)

    gap_v4 = in_sample["pr_auc"] - best["logo"]["pr_auc"]
    gap_v3 = v3_in_sample["pr_auc"] - results["variants"]["v3_ref"]["logo"]["pr_auc"]
    gap_v4_dual = in_sample["pr_auc"] - best["dual"]["pr_auc"]
    print(f"在样本内间隙(LOGO): v3={gap_v3:.4f} → v4={gap_v4:.4f}", flush=True)

    feature_cols_v4 = feature_cols_base + list(TE_FEATURES)
    metrics = {
        # 与 tree_v3.pkl 对齐的主指标口径（LOGO CV）
        "mae": best["logo"]["mae"],
        "pr_auc": best["logo"]["pr_auc"],
        "n_samples": len(df),
        "n_features": len(feature_cols_v4),
        "group_by": "aldehyde",
        **flags,
        "use_conditions": False,
        "rule_neg_strategy": "remove_hard_rule",
        "remove_all_rule": False,
        # 阶段 11 新增自描述字段
        "use_te": True,
        "use_freq_weights": True,
        "freq_weight_formula": "1/sqrt(ald_freq * amine_freq), 均值归一化",
        "param_set": best_name,
        "xgb_params": {k: v for k, v in final_params.items()},
        "logo_cv": best["logo"],
        "dual_holdout_cv": best["dual"],
        "dual_protocol": "5 折网格分组：验证集的醛和胺均不出现在训练集",
        "in_sample": in_sample,
        "in_sample_gap_logo": float(gap_v4),
        "in_sample_gap_dual": float(gap_v4_dual),
    }
    joblib.dump({
        "model": final_model,
        "feature_cols": feature_cols_v4,
        "config": v3.get("config"),
        "metrics": metrics,
        "te_rates": te_rates_full,
    }, MODEL_V4)
    print(f"已保存模型: {MODEL_V4}", flush=True)

    with open(METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=float)

    results["best_candidate"] = best_name
    results["in_sample"] = {"tree_v3": v3_in_sample, "tree_v4": in_sample,
                            "gap_v3_logo": float(gap_v3), "gap_v4_logo": float(gap_v4),
                            "gap_v4_dual": float(gap_v4_dual)}
    results["v3_pkl_reported"] = {"pr_auc": v3["metrics"]["pr_auc"], "mae": v3["metrics"]["mae"]}
    save_results(results)
    print(f"已保存报告: {REPORT_JSON}", flush=True)

    # ---- 汇总表 ----
    print("\n========== 汇总（PR-AUC / MAE）==========")
    print(f"{'variant':<24}{'LOGO AUC':>10}{'LOGO MAE':>10}{'LOGO F1':>9}"
          f"{'双留出 AUC':>12}{'双留出 MAE':>12}{'双留出 F1':>11}")
    for name, r in results["variants"].items():
        if "logo" not in r or "dual" not in r:
            print(f"{name:<24}（未完成）")
            continue
        print(f"{name:<24}{r['logo']['pr_auc']:>10.4f}{r['logo']['mae']:>10.4f}"
              f"{r['logo']['F1_amine_freq_pr_auc']:>9.4f}"
              f"{r['dual']['pr_auc']:>12.4f}{r['dual']['mae']:>12.4f}"
              f"{r['dual']['F1_amine_freq_pr_auc']:>11.4f}")
    print(f"\n[finalize] 耗时 {(time.time() - t0):.1f}s", flush=True)


# ---------------------------------------------------------------- 完整运行

def full_run() -> None:
    df, y = load_df_y()
    _, flags, cols = load_v3()
    if not XY_CACHE.exists():
        print("特征缓存不存在，执行全量特征化（较慢）...", flush=True)
        t0 = time.time()
        X = featurize_dataframe(df, **flags)
        X_base = X.reindex(columns=cols).fillna(0).values
        joblib.dump({"X_base": X_base, "y": y, "df": df}, XY_CACHE)
        print(f"全量特征化完成: {X_base.shape}, {(time.time() - t0) / 60:.1f} 分钟", flush=True)

    weights = frequency_sample_weights(df).values
    print(f"频率降权: min={weights.min():.3f} max={weights.max():.3f} "
          f"(w = 1/sqrt(醛频次×胺频次)，均值归一化为 1)", flush=True)

    cmd_variant("v3_ref")
    for name in V4_CANDIDATES:
        cmd_variant(name)

    # 按当前结果确定胜者后跑消融
    results = load_results()
    best_name = max(V4_CANDIDATES, key=lambda n: results["variants"][n]["dual"]["pr_auc"])
    cmd_variant(f"{best_name}_noTE")
    cmd_variant(f"{best_name}_noWeight")
    finalize()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--featurize", type=int, default=None, metavar="I",
                    help="特征化第 I 块（配合 --n-chunks）")
    ap.add_argument("--n-chunks", type=int, default=6)
    ap.add_argument("--merge-features", action="store_true", help="合并特征块并缓存 X/y/df")
    ap.add_argument("--variant", type=str, default=None,
                    help="运行单个 variant（v3_ref / v4_* / v4_*_noTE / v4_*_noWeight）")
    ap.add_argument("--protocol", choices=["both", "logo", "dual"], default="both",
                    help="只跑某一种评估协议（用于超时后续跑）")
    ap.add_argument("--finalize", action="store_true", help="选主 + 训练最终模型 + 保存 tree_v4")
    args = ap.parse_args()

    if args.featurize is not None:
        featurize_chunk(args.featurize, args.n_chunks)
    elif args.merge_features:
        merge_features(args.n_chunks)
    elif args.variant is not None:
        cmd_variant(args.variant, args.protocol)
    elif args.finalize:
        finalize()
    else:
        full_run()


if __name__ == "__main__":
    main()
