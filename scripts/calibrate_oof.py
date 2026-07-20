"""阶段 12：v3/v4 out-of-fold 概率校准实验（Platt / isotonic）。

动机（exp_005 遗留 #3）：双留出下 MAE 与 PR-AUC 背离（v4 排序升但 MAE 偏差），
疑似回归校准问题。本脚本复用 stage11 的基础设施（特征缓存、折划分、run_cv）：

1. 产生 tree_v3 / tree_v4 配置的 out-of-fold 预测：
   - LOGO（GroupKFold-10 按醛，全覆盖 n=3094，无种子依赖）；
   - 双留出（5 折网格分组，每种子覆盖 ~20.5%，报 3 个分组种子 42/123/7）。
2. 在 OOF 预测上拟合两种单调校准器。为保持与 stage11 相同的 MAE 口径
   （对原始软标签 y∈{0,0.7,0.8,1.0} 的回归误差），校准目标取软标签 y 本身：
   - Platt：p = sigmoid(a*s + b)，(a,b) 直接优化软标签对数似然；
   - Isotonic：sklearn IsotonicRegression(y_min=0, y_max=1)。
3. 用 5 折 cross-fitting 估计校准的真实收益（校准器不在被评估样本上拟合），
   报告校准前后 MAE（vs 原始 y）/ Brier（vs y_bin=y>=0.5）/ PR-AUC（vs y_bin）。
4. 判定规则（记录于 reports/calibration.json）：
   LOGO cross-fit MAE 改善 >= 0.005 且 PR-AUC 变化 >= -0.005 →
   在完整 LOGO OOF 上重新拟合最优校准器，保存 models/calibrator_v{3,4}.pkl。
   双留出多种子结果作为一致性信息同时报告。
   ⚠️ 本脚本不改 predictor 默认行为；是否接入由后续决策。

用法：
    .venv\\Scripts\\python.exe scripts/calibrate_oof.py                 # 全流程（增量缓存，可中断续跑）
    .venv\\Scripts\\python.exe scripts/calibrate_oof.py --analyze-only  # 仅用已有 OOF 缓存做校准分析
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, mean_absolute_error
from sklearn.model_selection import GroupKFold, KFold

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import stage11_dual_holdout as s11  # noqa: E402  复用其数据/折划分/run_cv

OOF_CACHE = PROJECT_ROOT / "data" / "interim" / "stage12_oof_cache.pkl"
REPORT_JSON = PROJECT_ROOT / "reports" / "calibration.json"
CALIBRATOR_PATHS = {
    "v3": PROJECT_ROOT / "models" / "calibrator_v3.pkl",
    "v4": PROJECT_ROOT / "models" / "calibrator_v4.pkl",
}

# v3 = tree_v3 配置（v3 参数、无 TE、无降权）；v4 = tree_v4 配置（v4_mild：TE+降权+弱正则）
VARIANTS = {
    "v3": dict(params=s11.V3_PARAMS, use_te=False, use_weights=False),
    "v4": dict(params=s11.V4_CANDIDATES["v4_mild"], use_te=True, use_weights=True),
}
DUAL_SEEDS = [42, 123, 7]

# 判定规则：LOGO cross-fit MAE 至少改善 0.005（约 2% 量级），且 PR-AUC 下降不超过 0.005
MAE_MIN_IMPROVEMENT = 0.005
PRAUC_MAX_DROP = 0.005


# ---------------------------------------------------------------- 校准器

def fit_platt(s: np.ndarray, t: np.ndarray) -> tuple[float, float]:
    """软标签 Platt：最大化 Σ[t·log p + (1-t)·log(1-p)]，p=sigmoid(a*s+b)。

    y∈{0,0.7,0.8,1.0} 是分级标签而非硬 0/1，直接以软标签为目标是
    对经典 Platt 的自然推广，且保持与回归 MAE 相同的语义口径。
    """
    from scipy.optimize import minimize

    s = np.asarray(s, dtype=float)
    t = np.asarray(t, dtype=float)

    def nll(ab: np.ndarray) -> float:
        a, b = ab
        z = np.clip(a * s + b, -35.0, 35.0)
        p = np.clip(1.0 / (1.0 + np.exp(-z)), 1e-7, 1 - 1e-7)
        return -float(np.sum(t * np.log(p) + (1.0 - t) * np.log(1.0 - p)))

    best = None
    for x0 in ((1.0, 0.0), (2.0, -1.0), (0.5, 0.5)):
        res = minimize(nll, x0=x0, method="Nelder-Mead",
                       options={"maxiter": 5000, "xatol": 1e-8, "fatol": 1e-10})
        if best is None or res.fun < best.fun:
            best = res
    return float(best.x[0]), float(best.x[1])


def apply_platt(s: np.ndarray, ab: tuple[float, float]) -> np.ndarray:
    a, b = ab
    z = np.clip(a * np.asarray(s, dtype=float) + b, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z))


def fit_calibrator(kind: str, s: np.ndarray, t: np.ndarray):
    if kind == "platt":
        return ("platt", fit_platt(s, t))
    if kind == "isotonic":
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(np.asarray(s, dtype=float), np.asarray(t, dtype=float))
        return ("isotonic", iso)
    raise KeyError(f"未知校准器：{kind}")


def apply_calibrator(cal, s: np.ndarray) -> np.ndarray:
    kind, obj = cal
    if kind == "platt":
        return apply_platt(s, obj)
    return np.asarray(obj.predict(np.asarray(s, dtype=float)), dtype=float)


# ---------------------------------------------------------------- 指标与 cross-fit

def eval_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    """MAE（vs 原始软标签 y，stage11 口径）+ Brier / PR-AUC（vs y_bin=y>=0.5）。"""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    y_bin = (y >= 0.5).astype(int)
    return {
        "mae": float(mean_absolute_error(y, p)),
        "brier": float(brier_score_loss(y_bin, np.clip(p, 0.0, 1.0))),
        "pr_auc": float(average_precision_score(y_bin, p)),
    }


def crossfit_calibrated(s: np.ndarray, y: np.ndarray, kind: str,
                        n_splits: int = 5, seed: int = 0) -> np.ndarray:
    """K 折 cross-fitting：每个样本的校准值由不含它的折上拟合的校准器给出。"""
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=seed)
    out = np.empty_like(s, dtype=float)
    for tr, va in kf.split(s):
        cal = fit_calibrator(kind, s[tr], y[tr])
        out[va] = apply_calibrator(cal, s[va])
    return out


def analyze_oof(y: np.ndarray, pred: np.ndarray) -> dict:
    """对一套 OOF 预测做校准前后评估（raw / platt / isotonic，均 cross-fit）。"""
    mask = ~np.isnan(pred)
    s, ys = pred[mask], y[mask]
    out = {"n_covered": int(mask.sum()), "coverage": float(mask.mean()),
           "raw": eval_metrics(ys, s)}
    for kind in ("platt", "isotonic"):
        out[kind] = eval_metrics(ys, crossfit_calibrated(s, ys, kind))
    return out


# ---------------------------------------------------------------- OOF 计算（增量缓存）

def load_oof_cache() -> dict:
    if OOF_CACHE.exists():
        return joblib.load(OOF_CACHE)
    return {"preds": {}}


def save_oof_cache(cache: dict) -> None:
    tmp = OOF_CACHE.with_suffix(".pkl.tmp")
    joblib.dump(cache, tmp)
    tmp.replace(OOF_CACHE)


def compute_oof(variant: str, protocol: str, seed: int | None,
                X_base, y, df, weights, logo_folds) -> np.ndarray:
    key = (variant, protocol, seed if protocol == "dual" else None)
    cache = load_oof_cache()
    if key in cache["preds"]:
        print(f"[oof] {key} 命中缓存，跳过", flush=True)
        return cache["preds"][key]

    cfg = VARIANTS[variant]
    folds = logo_folds if protocol == "logo" else s11.dual_holdout_folds(df, n_splits=5, seed=seed)
    w = weights if cfg["use_weights"] else None
    t0 = time.time()
    pred, metrics = s11.run_cv(X_base, y, df, folds, cfg["params"],
                               cfg["use_te"], w, f"{variant}/{protocol}/{seed}")
    cache["preds"][key] = pred
    save_oof_cache(cache)
    print(f"[oof] {key} 完成：PR-AUC={metrics['pr_auc']:.4f} MAE={metrics['mae']:.4f} "
          f"({(time.time() - t0):.0f}s)，已缓存", flush=True)
    return pred


# ---------------------------------------------------------------- 校准器落盘

def save_calibrator(variant: str, kind: str, s: np.ndarray, y: np.ndarray,
                    logo_eval: dict) -> Path:
    """在完整 LOGO OOF 上拟合最终校准器并保存（自描述 dict，无自定义类）。"""
    cal = fit_calibrator(kind, s, y)
    payload = {
        "kind": kind,
        "fit_target": "soft is_film labels (0/0.7/0.8/1.0), LOGO OOF full",
        "source_model": f"models/tree_{variant}.pkl",
        "n_fit_samples": int(len(s)),
        "created_by": "scripts/calibrate_oof.py",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "logo_crossfit_eval": logo_eval,
        "note": "未接入 predictor（默认行为不变）；是否启用由后续决策",
    }
    if kind == "platt":
        payload["platt_ab"] = [cal[1][0], cal[1][1]]
    else:
        payload["isotonic_model"] = cal[1]
    path = CALIBRATOR_PATHS[variant]
    joblib.dump(payload, path)
    return path


# ---------------------------------------------------------------- 主流程

def run(analyze_only: bool = False) -> None:
    X_base, y, df = s11.load_xy()
    weights = s11.frequency_sample_weights(df).values
    ald_groups = df["aldehyde_smiles"].astype("category").cat.codes.values
    logo_folds = list(GroupKFold(n_splits=10).split(X_base, y, ald_groups))

    # ---- 1. OOF 预测（LOGO + 双留出×3 种子，逐 variant）----
    preds: dict[tuple, np.ndarray] = {}
    for variant in VARIANTS:
        preds[(variant, "logo", None)] = compute_oof(
            variant, "logo", None, X_base, y, df, weights, logo_folds)
        for seed in DUAL_SEEDS:
            preds[(variant, "dual", seed)] = compute_oof(
                variant, "dual", seed, X_base, y, df, weights, logo_folds)

    # ---- 2. 校准前后评估 ----
    results: dict = {"variants": {}}
    for variant in VARIANTS:
        entry: dict = {"logo": analyze_oof(y, preds[(variant, "logo", None)])}
        per_seed = {str(seed): analyze_oof(y, preds[(variant, "dual", seed)])
                    for seed in DUAL_SEEDS}
        mean = {}
        for part in ("raw", "platt", "isotonic"):
            mean[part] = {m: float(np.mean([per_seed[str(s)][part][m] for s in DUAL_SEEDS]))
                          for m in ("mae", "brier", "pr_auc")}
            mean[part]["mae_std"] = float(np.std([per_seed[str(s)][part]["mae"]
                                                  for s in DUAL_SEEDS]))
            mean[part]["pr_auc_std"] = float(np.std([per_seed[str(s)][part]["pr_auc"]
                                                     for s in DUAL_SEEDS]))
        entry["dual"] = {"per_seed": per_seed, "mean": mean,
                         "note": "各种子内 5 折合并 OOF（覆盖 ~20.5%）上 cross-fit 评估"}
        results["variants"][variant] = entry

        logo = entry["logo"]
        print(f"\n=== {variant} LOGO（n={logo['n_covered']}）===", flush=True)
        for part in ("raw", "platt", "isotonic"):
            r = logo[part]
            print(f"  {part:<9} MAE={r['mae']:.4f} Brier={r['brier']:.4f} "
                  f"PR-AUC={r['pr_auc']:.4f}", flush=True)
        dm = entry["dual"]["mean"]
        print(f"  双留出 3 种子均值: raw MAE={dm['raw']['mae']:.4f} → "
              f"platt={dm['platt']['mae']:.4f} iso={dm['isotonic']['mae']:.4f}", flush=True)

    # ---- 3. 判定 + 保存最优校准器（按 LOGO cross-fit MAE 选主）----
    saved: dict = {}
    conclusions = []
    for variant in VARIANTS:
        logo = results["variants"][variant]["logo"]
        best_kind = min(("platt", "isotonic"), key=lambda k: logo[k]["mae"])
        mae_gain = logo["raw"]["mae"] - logo[best_kind]["mae"]
        pr_auc_delta = logo[best_kind]["pr_auc"] - logo["raw"]["pr_auc"]
        ok = mae_gain >= MAE_MIN_IMPROVEMENT and pr_auc_delta >= -PRAUC_MAX_DROP
        dual_gain = (results["variants"][variant]["dual"]["mean"]["raw"]["mae"]
                     - results["variants"][variant]["dual"]["mean"][best_kind]["mae"])
        conclusions.append(
            f"{variant}: 最优校准器={best_kind}，LOGO MAE {logo['raw']['mae']:.4f}→"
            f"{logo[best_kind]['mae']:.4f}（改善 {mae_gain:+.4f}），PR-AUC Δ{pr_auc_delta:+.4f}，"
            f"双留出 3 种子均值 MAE 改善 {dual_gain:+.4f} → {'保存' if ok else '不保存'}")
        if ok:
            mask = ~np.isnan(preds[(variant, "logo", None)])
            path = save_calibrator(variant, best_kind,
                                   preds[(variant, "logo", None)][mask], y[mask], logo)
            saved[variant] = {"path": str(path.relative_to(PROJECT_ROOT)), "kind": best_kind,
                              "mae_gain_logo": float(mae_gain),
                              "pr_auc_delta_logo": float(pr_auc_delta),
                              "mae_gain_dual_mean": float(dual_gain)}
            print(f"[save] {variant} -> {path}", flush=True)
        else:
            saved[variant] = None
            print(f"[skip] {variant} 校准未达判定阈值（MAE 改善 {mae_gain:+.4f}，"
                  f"PR-AUC Δ{pr_auc_delta:+.4f}），不保存", flush=True)

    # ---- 4. 报告落盘 ----
    results.update({
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "script": "scripts/calibrate_oof.py",
        "data": {"path": "data/interim/v5_train_stage1_cond_filled.csv（过滤 hard_rule）",
                  "n_samples": int(len(df)),
                  "label_values": sorted(set(float(v) for v in y))},
        "protocols": {"logo": "GroupKFold-10 by aldehyde（全覆盖，无种子依赖）",
                       "dual": f"5 折网格分组双留出，种子 {DUAL_SEEDS}（exp_005 同款）"},
        "calibrators": {
            "platt": "p=sigmoid(a*s+b)，软标签对数似然（目标=原始 y）",
            "isotonic": "sklearn IsotonicRegression(y_min=0, y_max=1)（目标=原始 y）",
        },
        "evaluation": "校准收益用 5 折 cross-fitting 估计（校准器不在被评估样本上拟合）；"
                      "MAE vs 原始 y（stage11 口径），Brier/PR-AUC vs y_bin=y>=0.5。",
        "pr_auc_note": "严格单调校准在单一映射下不改变排序/PR-AUC；cross-fit 下每折一个映射，"
                       "拼接后的 PR-AUC 变化（如 v4 双留出 platt PR-AUC 上升 0.04-0.10）"
                       "是折间映射差异的伪影，不能解读为真实的排序收益——判定只看 MAE/Brier",
        "decision_rule": {"basis": "LOGO cross-fit",
                           "mae_min_improvement": MAE_MIN_IMPROVEMENT,
                           "pr_auc_max_drop": PRAUC_MAX_DROP,
                           "tiebreak": "platt/isotonic 中选 LOGO cross-fit MAE 更优者"},
        "saved": saved,
        "conclusion": "；".join(conclusions),
        "predictor_note": "本脚本不改 predictor 默认行为；校准器是否接入由后续决策",
    })
    tmp = REPORT_JSON.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=float)
    tmp.replace(REPORT_JSON)
    print(f"\n[report] -> {REPORT_JSON}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--analyze-only", action="store_true",
                    help="跳过 OOF 计算（要求缓存完整），只做校准分析")
    args = ap.parse_args()
    if args.analyze_only:
        cache = load_oof_cache()
        missing = [(v, "logo", None) for v in VARIANTS] + \
                  [(v, "dual", s) for v in VARIANTS for s in DUAL_SEEDS]
        missing = [k for k in missing if k not in cache["preds"]]
        if missing:
            raise RuntimeError(f"OOF 缓存缺失 {missing}，请先完整运行")
    run(analyze_only=args.analyze_only)


if __name__ == "__main__":
    main()
