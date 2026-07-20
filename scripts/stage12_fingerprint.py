"""阶段 12：表征升级——Morgan 指纹入模（tree_v5）。

背景（exp_005 遗留）：双留出（醛胺均未见）下所有模型在 0.63-0.68 挣扎，
更强正则反而更差 → 瓶颈在化学表征而非容量。本脚本把醛/胺单体的
Morgan 指纹（及 MACCS 对照）拼入 tree_v4 配置（TE + 频率降权 + 弱正则），
检验子结构表征能否突破双留出瓶颈。

设计：
1. 特征：X = [tree_v3 的 142 维基础特征] + [TE 2 列] + [醛/胺指纹各 N 位]。
   基础特征直接复用 data/interim/stage11_xy_cache.pkl（避免重跑 3D）；
   指纹只依赖 SMILES、不含标签 → 全局计算不构成泄漏，缓存于
   data/interim/stage12_fp_cache/。
2. 评估：与 stage11 完全同口径 —— LOGO（GroupKFold-10 按醛，无种子依赖）
   + 双留出 5 折 × 3 个分组种子（42/123/7，与 stage11 报告一致，可直接对比）。
   TE 防泄漏铁律继承：每折只用训练折拟合映射表。
3. 变体（小步取舍，不过度调参）：
   - v5_fp1024      Morgan r2 / 1024 位 × 2 侧，TE + 降权 + mild（主配置）
   - v5_fp2048      Morgan r2 / 2048 位 × 2 侧（位长取舍）
   - v5_maccs       MACCS 166 位 × 2 侧（稀疏小型指纹对照）
   - v5_fp1024_noTE 1024 位但去 TE（exp_005：双留下 noTE 更优，验证是否复现）
4. finalize：按双留出 3 种子均值选主，全量训练并保存 models/tree_v5.pkl
   （自描述：metrics + 特征开关 + te_rates + fp_params）。

用法（全部变体约 10-20 分钟，每步独立落盘可续跑）：
    .venv\\Scripts\\python.exe scripts/stage12_fingerprint.py --variant v5_fp1024
    .venv\\Scripts\\python.exe scripts/stage12_fingerprint.py --all
    .venv\\Scripts\\python.exe scripts/stage12_fingerprint.py --finalize [--best v5_fp1024]
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
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import stage11_dual_holdout as s11  # 复用：数据加载/折划分/参数/基线
from features.fingerprints import (
    featurize_fingerprints,
    fingerprint_column_names,
    fingerprint_params,
)
from features.target_encoding import (
    FEATURE_NAMES as TE_FEATURES,
    apply_film_rates,
    fit_film_rates,
    frequency_sample_weights,
)

MODEL_V5 = PROJECT_ROOT / "models" / "tree_v5.pkl"
METRICS_JSON = PROJECT_ROOT / "models" / "tree_v5_metrics.json"
REPORT_JSON = PROJECT_ROOT / "reports" / "stage12_fingerprint.json"
FP_CACHE_DIR = PROJECT_ROOT / "data" / "interim" / "stage12_fp_cache"

# 与 stage11 报告一致的双留出分组种子（保证结果可直接对比）
SEEDS = (42, 123, 7)
N_JOBS = 16  # 32 核机器；指纹列多，放宽并行度（stage11 为 4）

# 全部变体共用 tree_v4 胜者配置（v4_mild：TE + 频率降权 + 弱正则），只改指纹
VARIANTS = {
    "v5_fp1024": {"fp": ("morgan", 2, 1024), "use_te": True},
    "v5_fp2048": {"fp": ("morgan", 2, 2048), "use_te": True},
    "v5_maccs": {"fp": ("maccs", 2, 166), "use_te": True},
    "v5_fp1024_noTE": {"fp": ("morgan", 2, 1024), "use_te": False},
}


# ---------------------------------------------------------------- 指纹缓存

def load_fp_matrix(df: pd.DataFrame, kind: str, radius: int, n_bits: int) -> np.ndarray:
    """按 (kind, radius, n_bits) 缓存的全体样本指纹矩阵（uint8）。"""
    p = fingerprint_params(kind, radius, n_bits)
    FP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = FP_CACHE_DIR / f"{p['kind']}_r{p['radius']}_{p['n_bits']}.pkl"
    if path.exists():
        return joblib.load(path)
    t0 = time.time()
    fp = featurize_fingerprints(df, kind=p["kind"], radius=p["radius"], n_bits=p["n_bits"])
    arr = fp.values
    joblib.dump(arr, path)
    print(f"[fp] {p['kind']} r{p['radius']} {p['n_bits']}bit: {arr.shape}, "
          f"{time.time() - t0:.1f}s -> {path}", flush=True)
    return arr


# ---------------------------------------------------------------- CV 评估

def run_cv_fp(X_base: np.ndarray, fp: np.ndarray, y: np.ndarray, df: pd.DataFrame,
              folds, params: dict, use_te: bool, weights: np.ndarray | None,
              tag: str) -> dict:
    """与 s11.run_cv 同口径，但特征为 [X_base (+TE 折内拟合) + 指纹]。
    逐折报告标配（D23）：返回指标含 per_fold 逐折明细 + fold_summary。"""
    pred = np.full(len(df), np.nan)
    pred_f0 = np.full(len(df), np.nan)
    pred_f1 = np.full(len(df), np.nan)
    per_fold = []

    for i, (tr, va) in enumerate(folds):
        t0 = time.time()
        if len(va) == 0:
            per_fold.append({"fold": i, "n_val": 0})
            print(f"  [{tag}] fold {i}: 验证集为空，跳过", flush=True)
            continue
        parts = [X_base]
        if use_te:  # 防泄漏铁律：TE 映射表只用训练折拟合
            rates = fit_film_rates(df.iloc[tr])
            parts.append(apply_film_rates(df, rates).values)
        parts.append(fp)  # 指纹只依赖 SMILES，无标签泄漏
        X_full = np.hstack(parts)

        m = XGBRegressor(**params, random_state=42, n_jobs=N_JOBS)
        fit_kwargs = {}
        if weights is not None:
            fit_kwargs["sample_weight"] = weights[tr]
        m.fit(X_full[tr], y[tr], **fit_kwargs)
        p = m.predict(X_full[va])
        pred[va] = p
        per_fold.append({
            "fold": i,
            "n_train": int(len(tr)),
            "n_val": int(len(va)),
            "pos_rate_val": float((y[va] >= 0.5).mean()),
            "pr_auc": s11.pr_auc(y[va], p),
            "mae": float(mean_absolute_error(y[va], p)),
        })

        gm = y[tr].mean()
        pred_f0[va] = gm
        amine_rate_tr = df.iloc[tr].groupby("amine_smiles")["is_film"].mean()
        pred_f1[va] = df.iloc[va]["amine_smiles"].map(amine_rate_tr).fillna(gm).values
        print(f"  [{tag}] fold {i}: n_train={len(tr)} n_val={len(va)} "
              f"pr_auc={per_fold[-1]['pr_auc']:.4f} ({time.time() - t0:.1f}s)", flush=True)

    mask = ~np.isnan(pred)
    return {
        "n_val_covered": int(mask.sum()),
        "coverage": float(mask.mean()),
        "pr_auc": s11.pr_auc(y[mask], pred[mask]),
        "mae": float(mean_absolute_error(y[mask], pred[mask])),
        "F0_global_mean_pr_auc": s11.pr_auc(y[mask], pred_f0[mask]),
        "F1_amine_freq_pr_auc": s11.pr_auc(y[mask], pred_f1[mask]),
        # D23 逐折报告标配：逐折明细 + fold 级均值±std（合并指标会掩盖难折）
        "per_fold": per_fold,
        "fold_summary": s11.fold_summary(per_fold),
    }


# ---------------------------------------------------------------- 结果落盘

def load_results() -> dict:
    if REPORT_JSON.exists():
        with open(REPORT_JSON, encoding="utf-8") as f:
            return json.load(f)
    # 基线数字取自 stage11 报告（同协议同种子，直接可比）
    s11r = s11.load_results()
    return {
        "protocols": {
            "logo": "GroupKFold-10 by aldehyde（无种子依赖）",
            "dual": f"自定义网格分组 5 折 × 种子 {list(SEEDS)}：验证集的醛和胺均不在训练集出现",
        },
        "common_config": {
            "base": "tree_v4 胜者配置 v4_mild（XGBoost 弱正则 + 频率降权）",
            "params": s11.V4_CANDIDATES["v4_mild"],
            "freq_weight_formula": "1/sqrt(ald_freq * amine_freq), 均值归一化",
        },
        "baselines_from_stage11": {
            "note": "同协议同种子，直接摘自 reports/stage11_dual_holdout.json",
            "logo_pr_auc": {
                "v3_ref": s11r["variants"]["v3_ref"]["logo"]["pr_auc"],
                "v4_mild": s11r["variants"]["v4_mild"]["logo"]["pr_auc"],
                "v4_mild_noTE": s11r["variants"]["v4_mild_noTE"]["logo"]["pr_auc"],
            },
            "dual_pr_auc_mean": s11r["dual_seed_robustness"],
        },
        "variants": {},
    }


def save_results(results: dict) -> None:
    tmp = REPORT_JSON.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=float)
    tmp.replace(REPORT_JSON)


def cmd_variant(name: str) -> None:
    spec = VARIANTS[name]
    params = dict(s11.V4_CANDIDATES["v4_mild"])
    use_te = spec["use_te"]
    X_base, y, df = s11.load_xy()
    fp = load_fp_matrix(df, *spec["fp"])
    weights = frequency_sample_weights(df).values

    p = fingerprint_params(*spec["fp"])
    n_feat = X_base.shape[1] + (len(TE_FEATURES) if use_te else 0) + fp.shape[1]
    print(f"=== {name}: {p['kind']} r{p['radius']} {p['n_bits']}bit ×2 侧, "
          f"TE={use_te}, 特征数={n_feat} ===", flush=True)

    results = load_results()
    results["n_samples"] = len(df)
    entry = results["variants"].setdefault(name, {
        "params": params, "use_te": use_te, "use_freq_weights": True,
        "fp": p, "n_features": n_feat})

    # ---- LOGO（无种子依赖）----
    print(" LOGO（GroupKFold-10 按醛）:", flush=True)
    ald_groups = df["aldehyde_smiles"].astype("category").cat.codes.values
    logo_folds = list(GroupKFold(n_splits=10).split(X_base, y, ald_groups))
    entry["logo"] = run_cv_fp(X_base, fp, y, df, logo_folds, params, use_te,
                              weights, f"{name}/LOGO")
    save_results(results)

    # ---- 双留出 × 3 种子 ----
    by_seed = {}
    for seed in SEEDS:
        print(f" 双留出（醛+胺 5 折, seed={seed}）:", flush=True)
        folds = s11.dual_holdout_folds(df, n_splits=5, seed=seed)
        by_seed[str(seed)] = run_cv_fp(X_base, fp, y, df, folds, params, use_te,
                                       weights, f"{name}/dual_s{seed}")
        entry["dual_by_seed"] = by_seed
        save_results(results)  # 每个种子落盘一次，可断点续跑

    aucs = [by_seed[str(s)]["pr_auc"] for s in SEEDS]
    maes = [by_seed[str(s)]["mae"] for s in SEEDS]
    entry["dual_mean"] = {
        "pr_auc_mean": float(np.mean(aucs)), "pr_auc_std": float(np.std(aucs)),
        "mae_mean": float(np.mean(maes)), "mae_std": float(np.std(maes)),
        "pr_auc_by_seed": aucs, "mae_by_seed": maes,
    }
    save_results(results)
    print(f"[variant] {name} 完成: LOGO {entry['logo']['pr_auc']:.4f}, "
          f"双留出均值 {entry['dual_mean']['pr_auc_mean']:.4f}"
          f"±{entry['dual_mean']['pr_auc_std']:.4f} -> {REPORT_JSON}", flush=True)


# ---------------------------------------------------------------- 最终模型

def finalize(best_name: str | None = None) -> None:
    t0 = time.time()
    results = load_results()
    done = [n for n, r in results["variants"].items() if "dual_mean" in r]
    if not done:
        raise RuntimeError("尚无完成双留出 3 种子的变体，先运行 --variant/--all")
    if best_name is None:
        best_name = max(done, key=lambda n: results["variants"][n]["dual_mean"]["pr_auc_mean"])
    if best_name not in done:
        raise RuntimeError(f"变体 {best_name} 未完成，已完成：{done}")
    best = results["variants"][best_name]
    print(f">>> 选主: {best_name} (双留出 3 种子均值 "
          f"{best['dual_mean']['pr_auc_mean']:.4f}±{best['dual_mean']['pr_auc_std']:.4f})",
          flush=True)

    spec = VARIANTS[best_name]
    params = dict(s11.V4_CANDIDATES["v4_mild"])
    use_te = spec["use_te"]
    fp_p = fingerprint_params(*spec["fp"])

    X_base, y, df = s11.load_xy()
    v3, flags, base_cols = s11.load_v3()
    fp = load_fp_matrix(df, *spec["fp"])
    weights = frequency_sample_weights(df).values

    # ---- 全量训练最终模型；TE 映射表用全量拟合并存入 pkl ----
    print(f"=== 训练最终 tree_v5（{best_name}，全量）===", flush=True)
    parts = [X_base]
    te_rates_full = None
    if use_te:
        te_rates_full = fit_film_rates(df)
        parts.append(apply_film_rates(df, te_rates_full).values)
    parts.append(fp)
    X_final = np.hstack(parts)
    final_model = XGBRegressor(**params, random_state=42, n_jobs=N_JOBS)
    final_model.fit(X_final, y, sample_weight=weights)

    pred_in = final_model.predict(X_final)
    in_sample = {"pr_auc": s11.pr_auc(y, pred_in),
                 "mae": float(mean_absolute_error(y, pred_in))}
    print(f"tree_v5 in-sample: {in_sample}", flush=True)

    ald_cols, amine_cols = fingerprint_column_names(*spec["fp"])
    feature_cols = base_cols + (list(TE_FEATURES) if use_te else []) + ald_cols + amine_cols
    metrics = {
        # 与 tree_v3/v4 对齐的主指标口径（LOGO CV）
        "mae": best["logo"]["mae"],
        "pr_auc": best["logo"]["pr_auc"],
        "n_samples": len(df),
        "n_features": len(feature_cols),
        "group_by": "aldehyde",
        **flags,
        "use_conditions": False,
        "rule_neg_strategy": "remove_hard_rule",
        "remove_all_rule": False,
        # tree_v4 继承字段
        "use_te": use_te,
        "use_freq_weights": True,
        "freq_weight_formula": "1/sqrt(ald_freq * amine_freq), 均值归一化",
        "param_set": "v4_mild",
        "xgb_params": params,
        # 阶段 12 新增：指纹自描述
        "use_fingerprint": True,
        "fp_kind": fp_p["kind"],
        "fp_radius": fp_p["radius"],
        "fp_n_bits": fp_p["n_bits"],
        "variant": best_name,
        "logo_cv": best["logo"],
        "dual_holdout_by_seed": best["dual_by_seed"],
        "dual_holdout_mean": best["dual_mean"],
        "dual_protocol": f"5 折网格分组 × 种子 {list(SEEDS)}：验证集的醛和胺均不出现在训练集",
        "in_sample": in_sample,
        "in_sample_gap_logo": float(in_sample["pr_auc"] - best["logo"]["pr_auc"]),
        "in_sample_gap_dual": float(in_sample["pr_auc"] - best["dual_mean"]["pr_auc_mean"]),
    }
    joblib.dump({
        "model": final_model,
        "feature_cols": feature_cols,
        "config": v3.get("config"),
        "metrics": metrics,
        "te_rates": te_rates_full,   # use_te=False 时为 None（向后兼容）
        "fp_params": fp_p,           # 预测侧据此补指纹列
    }, MODEL_V5)
    print(f"已保存模型: {MODEL_V5}", flush=True)

    with open(METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=float)

    results["best_variant"] = best_name
    save_results(results)
    print_summary(results)
    print(f"\n[finalize] 耗时 {(time.time() - t0):.1f}s", flush=True)


def print_summary(results: dict) -> None:
    print("\n========== 汇总（LOGO / 双留出 3 种子均值）==========")
    print(f"{'variant':<18}{'LOGO AUC':>10}{'LOGO MAE':>10}"
          f"{'双留出 AUC':>16}{'双留出 MAE':>12}")
    b = results.get("baselines_from_stage11", {})
    for name, auc in (b.get("logo_pr_auc") or {}).items():
        d = b["dual_pr_auc_mean"].get(name.replace("v3_ref", "v3_ref"), {})
        pm = d.get("pr_auc_mean", float("nan"))
        mm = d.get("mae_mean", float("nan"))
        print(f"{name + ' (s11)':<18}{auc:>10.4f}{'—':>10}{pm:>16.4f}{mm:>12.4f}")
    for name, r in results["variants"].items():
        if "dual_mean" not in r:
            print(f"{name:<18}（未完成）")
            continue
        dm = r["dual_mean"]
        print(f"{name:<18}{r['logo']['pr_auc']:>10.4f}{r['logo']['mae']:>10.4f}"
              f"{dm['pr_auc_mean']:>10.4f}±{dm['pr_auc_std']:<5.4f}{dm['mae_mean']:>12.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", type=str, default=None, choices=list(VARIANTS),
                    help="运行单个变体")
    ap.add_argument("--all", action="store_true", help="运行全部变体")
    ap.add_argument("--finalize", action="store_true",
                    help="选主 + 全量训练 + 保存 tree_v5")
    ap.add_argument("--best", type=str, default=None, choices=list(VARIANTS),
                    help="finalize 时手动指定选主变体（默认按双留出 3 种子均值）")
    args = ap.parse_args()

    if args.variant is not None:
        cmd_variant(args.variant)
    elif args.all:
        for name in VARIANTS:
            cmd_variant(name)
    elif args.finalize:
        finalize(args.best)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
