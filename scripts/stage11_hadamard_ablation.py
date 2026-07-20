"""阶段 12（任务2）：Hadamard 交互消融 + 双留出 fold3 诊断。

背景（exp_005 / 阶段 12 任务书）：
- 双留出（醛胺都未见过训练集）PR-AUC 0.63-0.68 是真正瓶颈，瓶颈在化学表征；
- 假设：醛-胺 Hadamard 交互特征（int_*，51 维）可能是"背配对"的记忆通道——
  在 LOGO（胺已见于训练集）下贡献大，但在双留出下不泛化甚至有害。
- 双留出 fold3（n=203）在 v3/v4 下都只有 0.33-0.39，需要单独诊断。

本脚本：
1. 消融：4 个变体 × LOGO + 双留出（3 个分组种子，与 exp_005 相同 42/123/7）：
   - v3_int   : tree_v3 参数 + 全部 142 基特征（含交互）
   - v3_noint : tree_v3 参数 + 去交互 91 基特征
   - v4_int   : v4_mild 参数 + TE + 频率降权 + 含交互（144 维）
   - v4_noint : v4_mild 参数 + TE + 频率降权 + 去交互（93 维）
   去交互通过特征名列（int_* 前缀）从缓存矩阵剔除实现——描述符管线中
   use_interaction=False 只是不拼接 int_* 列，其余特征值不变，两者等价；
   不改动 src/ 任何代码。
2. fold3 诊断（seed=42 双留出第 3 折）：
   逐折性能、标签分布、Murcko 骨架新颖性、官能团/描述统计、
   描述符漂移、标签噪声（重复配对/单体内标签方差）、文献集中度、预测分数行为。

复用 scripts/stage11_dual_holdout.py 的数据加载、折划分与参数定义。

用法：
    .venv\\Scripts\\python.exe scripts/stage11_hadamard_ablation.py                 # 全部消融
    .venv\\Scripts\\python.exe scripts/stage11_hadamard_ablation.py --variant v3_noint
    .venv\\Scripts\\python.exe scripts/stage11_hadamard_ablation.py --fold3         # fold3 诊断
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 复用阶段 11 基础设施（数据加载 / 折划分 / 参数 / 缓存）
from stage11_dual_holdout import (  # noqa: E402
    V3_PARAMS,
    V4_CANDIDATES,
    dual_holdout_folds,
    load_v3,
    load_xy,
    pr_auc,
)
from features.target_encoding import (  # noqa: E402
    apply_film_rates,
    fit_film_rates,
    frequency_sample_weights,
)

ABLATION_JSON = PROJECT_ROOT / "reports" / "hadamard_ablation.json"
FOLD3_JSON = PROJECT_ROOT / "reports" / "fold3_diagnosis.json"

SEEDS = [42, 123, 7]  # 与 exp_005 双留出种子稳健性协议一致

# 4 个消融变体：params / use_te / use_freq_weights / keep_int
VARIANTS = {
    "v3_int": dict(params=V3_PARAMS, use_te=False, use_weights=False, keep_int=True),
    "v3_noint": dict(params=V3_PARAMS, use_te=False, use_weights=False, keep_int=False),
    "v4_int": dict(params=V4_CANDIDATES["v4_mild"], use_te=True, use_weights=True, keep_int=True),
    "v4_noint": dict(params=V4_CANDIDATES["v4_mild"], use_te=True, use_weights=True, keep_int=False),
}


# ---------------------------------------------------------------- 特征与 CV

def int_col_keep_mask(feature_cols: list[str]) -> np.ndarray:
    """True = 保留（非交互列）；int_* 前缀即 Hadamard 交互特征。"""
    return np.array([not c.startswith("int_") for c in feature_cols])


def build_fold_features(X: np.ndarray, df: pd.DataFrame,
                        train_idx: np.ndarray, use_te: bool) -> np.ndarray:
    """与 stage11 相同的防泄漏铁律：TE 映射表只用训练折拟合。"""
    if not use_te:
        return X
    rates = fit_film_rates(df.iloc[train_idx])
    te_all = apply_film_rates(df, rates).values
    return np.hstack([X, te_all])


def run_cv(X: np.ndarray, y: np.ndarray, df: pd.DataFrame,
           folds, params: dict, use_te: bool, weights: np.ndarray | None,
           tag: str, collect: list | None = None):
    """跑 CV，返回 (oof_pred, 汇总指标, 逐折指标)。collect 传入列表时收集逐折预测。"""
    pred = np.full(len(df), np.nan)
    per_fold = []
    for i, (tr, va) in enumerate(folds):
        t0 = time.time()
        if len(va) == 0:
            per_fold.append({"fold": i, "n_val": 0})
            print(f"  [{tag}] fold {i}: 验证集为空，跳过", flush=True)
            continue
        X_full = build_fold_features(X, df, tr, use_te)
        m = XGBRegressor(**params, random_state=42, n_jobs=4)
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
            "pr_auc": pr_auc(y[va], p),
            "mae": float(mean_absolute_error(y[va], p)),
            "pred_mean": float(p.mean()),
        })
        if collect is not None:
            collect.append((i, va.copy(), p.copy()))
        print(f"  [{tag}] fold {i}: n_train={len(tr)} n_val={len(va)} "
              f"pr_auc={per_fold[-1]['pr_auc']:.4f} ({time.time() - t0:.1f}s)", flush=True)

    mask = ~np.isnan(pred)
    metrics = {
        "n_val_covered": int(mask.sum()),
        "coverage": float(mask.mean()),
        "pr_auc": pr_auc(y[mask], pred[mask]),
        "mae": float(mean_absolute_error(y[mask], pred[mask])),
    }
    return pred, metrics, per_fold


# ---------------------------------------------------------------- 结果落盘

def _load_json(path: Path, default: dict) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _save_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=float)
    tmp.replace(path)


# ---------------------------------------------------------------- 消融主流程

def cmd_ablation(only: list[str] | None = None) -> None:
    X_base, y, df = load_xy()
    _, _, cols = load_v3()
    keep_mask = int_col_keep_mask(cols)
    n_int = int((~keep_mask).sum())
    print(f"基特征 {len(cols)} 维，其中交互 int_* {n_int} 维，去交互后 {int(keep_mask.sum())} 维", flush=True)

    weights_all = frequency_sample_weights(df).values
    ald_groups = df["aldehyde_smiles"].astype("category").cat.codes.values
    logo_folds = list(GroupKFold(n_splits=10).split(X_base, y, ald_groups))
    dual_folds_by_seed = {s: dual_holdout_folds(df, n_splits=5, seed=s) for s in SEEDS}

    results = _load_json(ABLATION_JSON, {"variants": {}})
    results["meta"] = {
        "note": "Hadamard 交互消融：去交互 = 剔除 int_* 列（等价于描述符管线 use_interaction=False）",
        "n_samples": len(df),
        "n_base_features": int(X_base.shape[1]),
        "n_interaction_features": n_int,
        "seeds": SEEDS,
        "protocols": {"logo": "GroupKFold-10 by aldehyde（无种子依赖）",
                      "dual": "自定义网格分组 5 折：验证集的醛和胺均不在训练集出现"},
        "variant_defs": {n: {"use_te": c["use_te"], "use_freq_weights": c["use_weights"],
                             "keep_interaction": c["keep_int"], "params": c["params"]}
                         for n, c in VARIANTS.items()},
    }

    for name, cfg in VARIANTS.items():
        if only and name not in only:
            continue
        X = X_base if cfg["keep_int"] else X_base[:, keep_mask]
        w = weights_all if cfg["use_weights"] else None
        n_feat = int(X.shape[1]) + (2 if cfg["use_te"] else 0)
        print(f"\n=== {name} (TE={cfg['use_te']}, 降权={cfg['use_weights']}, "
              f"交互={cfg['keep_int']}, {n_feat} 维) ===", flush=True)

        entry = results["variants"].get(name, {})

        # LOGO（无种子依赖，只跑一次）
        _, logo_metrics, logo_per_fold = run_cv(
            X, y, df, logo_folds, cfg["params"], cfg["use_te"], w, f"{name}/LOGO")
        entry["logo"] = {**logo_metrics, "per_fold": logo_per_fold}
        results["variants"][name] = entry
        _save_json(ABLATION_JSON, results)

        # 双留出 × 多分组种子
        dual_runs = entry.get("dual_by_seed", {})
        for s in SEEDS:
            _, m, pf = run_cv(
                X, y, df, dual_folds_by_seed[s], cfg["params"], cfg["use_te"], w,
                f"{name}/双留出/s{s}")
            dual_runs[str(s)] = {**m, "per_fold": pf}
            entry["dual_by_seed"] = dual_runs
            results["variants"][name] = entry
            _save_json(ABLATION_JSON, results)  # 每个种子落盘一次

        pr_list = [dual_runs[str(s)]["pr_auc"] for s in SEEDS]
        mae_list = [dual_runs[str(s)]["mae"] for s in SEEDS]
        entry["dual_summary"] = {
            "pr_auc_mean": float(np.mean(pr_list)),
            "pr_auc_std": float(np.std(pr_list)),
            "mae_mean": float(np.mean(mae_list)),
            "pr_auc_by_seed": pr_list,
        }
        results["variants"][name] = entry
        _save_json(ABLATION_JSON, results)
        print(f"[variant] {name} 完成 -> {ABLATION_JSON}", flush=True)

    # 汇总表
    print("\n========== Hadamard 交互消融汇总（PR-AUC）==========")
    print(f"{'variant':<12}{'n_feat':>7}{'LOGO':>9}{'双留出 s42':>11}{'s123':>9}{'s7':>9}{'均值':>9}")
    for name in VARIANTS:
        r = results["variants"].get(name, {})
        if "logo" not in r or "dual_summary" not in r:
            print(f"{name:<12}（未完成）")
            continue
        n_feat = results["meta"]["n_base_features"] - (0 if VARIANTS[name]["keep_int"]
                                                       else results["meta"]["n_interaction_features"]) \
            + (2 if VARIANTS[name]["use_te"] else 0)
        by_seed = r["dual_summary"]["pr_auc_by_seed"]
        print(f"{name:<12}{n_feat:>7}{r['logo']['pr_auc']:>9.4f}"
              f"{by_seed[0]:>11.4f}{by_seed[1]:>9.4f}{by_seed[2]:>9.4f}"
              f"{r['dual_summary']['pr_auc_mean']:>9.4f}")


# ---------------------------------------------------------------- fold3 诊断

def _scaffold_of(smiles: str) -> str | None:
    """Bemis-Murcko 骨架 SMILES；无环返回 '(acyclic)'，解析失败返回 None。"""
    from rdkit import Chem
    from rdkit.Chem.Scaffolds import MurckoScaffold
    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return None
    try:
        scaf = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
        return scaf if scaf else "(acyclic)"
    except Exception:
        return None


def _monomer_stats(smiles_list, role: str) -> pd.DataFrame:
    """对唯一单体计算官能团/描述统计（轻量，2D 描述符）。"""
    from features.descriptors import compute_single_monomer_features
    rows = []
    for s in sorted(set(smiles_list)):
        feats = compute_single_monomer_features(str(s), role=role) or {}
        rows.append({
            "smiles": s,
            "scaffold": _scaffold_of(s),
            "n_reactive_sites": feats.get("n_reactive_sites"),
            "n_aromatic_rings": feats.get("n_aromatic_rings"),
            "n_rings": feats.get("n_rings"),
            "tpsa": feats.get("tpsa"),
            "mw": feats.get("mw"),
            "has_heterocycle": feats.get("has_heterocycle"),
            "has_acetylene": feats.get("has_acetylene"),
            "aromatic_frac": feats.get("aromatic_frac"),
        })
    return pd.DataFrame(rows)


def _fold_composition(df: pd.DataFrame, y: np.ndarray, va: np.ndarray,
                      train_ald_scaf: set, train_amine_scaf: set) -> dict:
    """一个验证折的组成画像：标签、单体频次、骨架新颖性、文献集中度。"""
    val_df = df.iloc[va]
    yv = y[va]
    ald_stats = _monomer_stats(val_df["aldehyde_smiles"], "aldehyde")
    amine_stats = _monomer_stats(val_df["amine_smiles"], "amine")

    ald_novel = ald_stats["scaffold"].map(lambda s: s is not None and s not in train_ald_scaf)
    amine_novel = amine_stats["scaffold"].map(lambda s: s is not None and s not in train_amine_scaf)
    ald_scaf_map = dict(zip(ald_stats["smiles"], ald_novel))
    amine_scaf_map = dict(zip(amine_stats["smiles"], amine_novel))
    sample_ald_novel = val_df["aldehyde_smiles"].map(ald_scaf_map).fillna(False)
    sample_amine_novel = val_df["amine_smiles"].map(amine_scaf_map).fillna(False)

    paper_col = "paper_id" if "paper_id" in val_df.columns else None
    paper_vc = val_df[paper_col].value_counts() if paper_col else pd.Series(dtype=int)

    return {
        "n_val": int(len(va)),
        "label": {
            "mean": float(yv.mean()),
            "pos_rate": float((yv >= 0.5).mean()),
            "value_counts": {str(k): int(v) for k, v in
                             pd.Series(yv).value_counts().sort_index().items()},
        },
        "monomers": {
            "n_unique_ald": int(val_df["aldehyde_smiles"].nunique()),
            "n_unique_amine": int(val_df["amine_smiles"].nunique()),
            "ald_dataset_freq": {k: int(v) for k, v in
                                 df["aldehyde_smiles"].value_counts()
                                 [val_df["aldehyde_smiles"].unique()].items()},
            "amine_dataset_freq": {k: int(v) for k, v in
                                   df["amine_smiles"].value_counts()
                                   [val_df["amine_smiles"].unique()].items()},
        },
        "scaffold_novelty": {
            "ald_novel_uniq_frac": float(ald_novel.mean()) if len(ald_novel) else None,
            "amine_novel_uniq_frac": float(amine_novel.mean()) if len(amine_novel) else None,
            "sample_ald_novel_frac": float(sample_ald_novel.mean()),
            "sample_amine_novel_frac": float(sample_amine_novel.mean()),
            "sample_either_novel_frac": float((sample_ald_novel | sample_amine_novel).mean()),
            "ald_scaffolds": {r["smiles"]: r["scaffold"] for _, r in ald_stats.iterrows()},
            "amine_scaffolds": {r["smiles"]: r["scaffold"] for _, r in amine_stats.iterrows()},
        },
        "descriptor_profile": {
            "ald": {k: (float(ald_stats[k].mean()) if ald_stats[k].notna().any() else None)
                    for k in ("n_reactive_sites", "n_aromatic_rings", "n_rings", "tpsa",
                              "mw", "has_heterocycle", "has_acetylene", "aromatic_frac")},
            "amine": {k: (float(amine_stats[k].mean()) if amine_stats[k].notna().any() else None)
                      for k in ("n_reactive_sites", "n_aromatic_rings", "n_rings", "tpsa",
                                "mw", "has_heterocycle", "has_acetylene", "aromatic_frac")},
            "ald_sites_counts": {str(k): int(v) for k, v in
                                 ald_stats["n_reactive_sites"].value_counts().sort_index().items()},
            "amine_sites_counts": {str(k): int(v) for k, v in
                                   amine_stats["n_reactive_sites"].value_counts().sort_index().items()},
        },
        "papers": ({
            "n_unique": int(len(paper_vc)),
            "top5": {str(k): int(v) for k, v in paper_vc.head(5).items()},
            "top1_share": float(paper_vc.iloc[0] / len(val_df)) if len(paper_vc) else None,
        } if paper_col else None),
    }


def cmd_fold3(fold_index: int = 3, seed: int = 42) -> None:
    X_base, y, df = load_xy()
    _, _, cols = load_v3()
    folds = dual_holdout_folds(df, n_splits=5, seed=seed)

    # ---- 1) 逐折性能（v3 / v4_mild，与 exp_005 口径一致）----
    weights_all = frequency_sample_weights(df).values
    per_fold_perf, collected = {}, []
    for tag, params, use_te, w in (
        ("v3_int", V3_PARAMS, False, None),
        ("v4_int", V4_CANDIDATES["v4_mild"], True, weights_all),
    ):
        _, _, pf = run_cv(X_base, y, df, folds, params, use_te, w,
                          f"fold3诊断/{tag}", collect=collected if tag == "v4_int" else None)
        per_fold_perf[tag] = pf

    # v3 也收集预测（分开收集避免列表混杂）
    collected_v3 = []
    run_cv(X_base, y, df, folds, V3_PARAMS, False, None, "fold3诊断/v3_pred",
           collect=collected_v3)

    # ---- 2) 全部折的组成画像（fold3 需要对照组）----
    # 训练集骨架集合按折计算（每折训练集不同）
    fold_profiles = []
    for k, (tr, va) in enumerate(folds):
        train_df = df.iloc[tr]
        train_ald_scaf = {_scaffold_of(s) for s in train_df["aldehyde_smiles"].unique()}
        train_amine_scaf = {_scaffold_of(s) for s in train_df["amine_smiles"].unique()}
        train_ald_scaf.discard(None)
        train_amine_scaf.discard(None)
        prof = _fold_composition(df, y, va, train_ald_scaf, train_amine_scaf)
        prof["fold"] = k
        fold_profiles.append(prof)
        print(f"[fold3诊断] 折 {k} 组成画像完成 (n_val={prof['n_val']})", flush=True)

    f3 = fold_profiles[fold_index]
    tr3, va3 = folds[fold_index]

    # ---- 3) 标签噪声 ----
    # 3a. 全数据集重复配对标签冲突率
    pair_key = df["aldehyde_smiles"] + "||" + df["amine_smiles"]
    pair_nunique = pd.Series(y, index=pair_key).groupby(level=0).nunique()
    dup_pairs = pair_nunique[pair_nunique > 1]
    # 3b. fold3 验证配对在别处是否重复出现、标签是否一致
    val_pairs = pair_key.iloc[va3]
    pair_to_labels = pd.Series(y, index=pair_key).groupby(level=0).apply(lambda s: sorted(set(s)))
    f3_pair_conflict = sum(1 for p in val_pairs if len(pair_to_labels.get(p, [])) > 1)
    # 3c. fold3 单体的全数据集标签分布（均值/方差）——单体内标签分散度
    def monomer_label_spread(smiles_col, val_smiles):
        g = pd.Series(y, index=df[smiles_col]).groupby(level=0)
        out = {}
        for s in sorted(set(val_smiles)):
            if s in g.groups:
                v = g.get_group(s)
                out[s] = {"n": int(len(v)), "mean": float(v.mean()),
                          "std": float(v.std()), "min": float(v.min()), "max": float(v.max())}
        return out
    label_noise = {
        "global_dup_pair_frac": float((pair_nunique > 1).mean()),
        "global_n_conflict_pairs": int(len(dup_pairs)),
        "fold3_pair_conflict_n": int(f3_pair_conflict),
        "fold3_ald_label_spread": monomer_label_spread("aldehyde_smiles",
                                                       df.iloc[va3]["aldehyde_smiles"]),
        "fold3_amine_label_spread": monomer_label_spread("amine_smiles",
                                                         df.iloc[va3]["amine_smiles"]),
    }

    # ---- 4) fold3 预测行为 ----
    pred_behavior = {}
    for tag, coll in (("v3_int", collected_v3), ("v4_int", collected)):
        fold_pred = {i: (va, p) for i, va, p in coll}
        if fold_index in fold_pred:
            va, p = fold_pred[fold_index]
            other_p = np.concatenate([pp for i, (_, pp) in fold_pred.items() if i != fold_index])
            other_va = np.concatenate([vv for i, (vv, _) in fold_pred.items() if i != fold_index])
            pred_behavior[tag] = {
                "fold3_pred_mean": float(p.mean()),
                "fold3_pred_std": float(p.std()),
                "other_folds_pred_mean": float(other_p.mean()),
                "other_folds_pred_std": float(other_p.std()),
                "fold3_pos_pred_mean": float(p[y[va] >= 0.5].mean()) if (y[va] >= 0.5).any() else None,
                "fold3_neg_pred_mean": float(p[y[va] < 0.5].mean()) if (y[va] < 0.5).any() else None,
                "other_pos_pred_mean": float(other_p[y[other_va] >= 0.5].mean()),
                "other_neg_pred_mean": float(other_p[y[other_va] < 0.5].mean()),
            }

    # ---- 5) 半交叉区（fold3 醛 × 其他胺 / 其他醛 × fold3 胺）标签对照 ----
    ald_in_g3 = set(df.iloc[va3]["aldehyde_smiles"])
    amine_in_g3 = set(df.iloc[va3]["amine_smiles"])
    ald_mask = df["aldehyde_smiles"].isin(ald_in_g3)
    amine_mask = df["amine_smiles"].isin(amine_in_g3)
    half_cross = {
        "ald_g3_x_amine_other": {"n": int((ald_mask & ~amine_mask).sum()),
                                 "label_mean": float(y[ald_mask & ~amine_mask].mean())},
        "ald_other_x_amine_g3": {"n": int((~ald_mask & amine_mask).sum()),
                                 "label_mean": float(y[~ald_mask & amine_mask].mean())},
        "g3_x_g3(验证区)": {"n": int((ald_mask & amine_mask).sum()),
                             "label_mean": float(y[ald_mask & amine_mask].mean())},
    }

    # ---- 6) 特征方向保持性 + 原始特征区域漂移（解释 fold3 为何反向/失效）----
    # 6a. 类信号方向：每折内 正例-负例 的关键特征均值差，与训练折同口径对比；
    #     若 fold3 差值符号与训练一致而模型仍反向，说明失败不在"化学方向翻转"。
    KEY_FEATS = ["ald_n_aromatic_rings_per_site", "ald_aromatic_frac", "ald_mw_per_site",
                 "amine_n_aromatic_rings_per_site", "int_hadamard_tpsa_per_site",
                 "int_hadamard_n_aromatic_rings_per_site", "ald_3d_radius_ratio", "site_ratio"]
    key_idx = {f: cols.index(f) for f in KEY_FEATS if f in cols}
    direction = {}
    for k, (tr, va) in enumerate(folds):
        pos_tr, pos_va = y[tr] >= 0.5, y[va] >= 0.5
        entry = {"train_label_mean": float(y[tr].mean())}
        for f, i in key_idx.items():
            entry[f] = {
                "val_pos_mean": float(X_base[va][pos_va][:, i].mean()),
                "val_neg_mean": float(X_base[va][~pos_va][:, i].mean()),
                "train_pos_mean": float(X_base[tr][pos_tr][:, i].mean()),
                "train_neg_mean": float(X_base[tr][~pos_tr][:, i].mean()),
            }
        direction[f"fold{k}"] = entry

    # 6b. 区域漂移：验证样本落在训练折 [5%, 95%] 分位区间之外的比例（原始/3D 特征）
    RAW_FEATS = ["ald_mw", "ald_n_aromatic_rings", "ald_n_rings", "amine_mw",
                 "amine_n_aromatic_rings", "ald_3d_mol_volume", "amine_3d_mol_volume",
                 "ald_3d_radius_ratio", "ald_tpsa", "amine_tpsa"]
    raw_idx = {f: cols.index(f) for f in RAW_FEATS if f in cols}
    drift = {}
    for k, (tr, va) in enumerate(folds):
        entry = {}
        for f, i in raw_idx.items():
            lo, hi = np.percentile(X_base[tr][:, i], [5, 95])
            v = X_base[va][:, i]
            entry[f] = float(((v < lo) | (v > hi)).mean())
        drift[f"fold{k}"] = entry

    report = {
        "seed": seed,
        "fold_index": fold_index,
        "per_fold_perf": per_fold_perf,
        "fold_profiles": fold_profiles,
        "label_noise": label_noise,
        "pred_behavior": pred_behavior,
        "half_cross_labels": half_cross,
        "feature_direction": direction,
        "region_drift_frac": drift,
        "dataset_label_mean": float(y.mean()),
    }
    _save_json(FOLD3_JSON, report)
    print(f"[fold3诊断] 完成 -> {FOLD3_JSON}", flush=True)


# ---------------------------------------------------------------- 入口

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", type=str, default=None,
                    help="只跑某个变体（v3_int / v3_noint / v4_int / v4_noint）")
    ap.add_argument("--fold3", action="store_true", help="运行双留出 fold3 诊断")
    ap.add_argument("--fold-index", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.fold3:
        cmd_fold3(args.fold_index, args.seed)
    else:
        cmd_ablation(only=[args.variant] if args.variant else None)


if __name__ == "__main__":
    main()
