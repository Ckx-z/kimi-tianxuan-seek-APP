"""阶段 13：GNN embedding 迁移入树模型的可行性评估（阶段 A，exp_009）。

背景：
- 双未见单体泛化 0.63-0.68 是瓶颈；手工描述符触顶、Morgan 指纹证伪（exp_006）。
- 本脚本把 stage13_extract_gnn_emb.py 提取的 GNN v5.3 embedding 作为特征，
  在与历史完全同口径的协议下评估：双留出 3 种子（42/123/7）+ LOGO，
  逐折明细 + fold_summary（D23 标配）。
- 统一 v4_mild 参数 + 频率降权（即 noTE 配置，只换特征），基线 = v4_mild_noTE
  双留出 3 种子均值 0.6824。

⚠️ 泄漏警告（阶段 A 的解释边界）：
GNN v5.3 训练集（v5_train_stage1_aug_v2.csv 6392 行）与本数据 3094 行的单体几乎
完全重叠——双留出验证折的单体 GNN 训练时见过，embedding 可能携带记忆标签，
本阶段结果是**乐观上界**：无提升即可证伪（逐折重训只会更差）；有提升也必须
经阶段 B（逐折重训 GNN 排除记忆）确认后才算数。

判定门槛（事先约定）：
- 双留出 3 种子均值较基线 0.6824 提升 < +0.03 或方向不一致 → 证伪，路线关闭；
- 提升 ≥ +0.03 且 3 种子方向一致 → 阶段 A 通过，立项阶段 B。

用法（.venv 环境，约 30-60 分钟）：
    .venv\\Scripts\\python.exe scripts/stage13_gnn_embedding_eval.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from features.target_encoding import frequency_sample_weights  # noqa: E402
from stage11_dual_holdout import (  # noqa: E402
    V4_CANDIDATES,
    load_xy,
    make_folds,
    pr_auc,
    run_cv,
)

EMB_DIR = PROJECT_ROOT / "data" / "interim"
REPORT_JSON = PROJECT_ROOT / "reports" / "gnn_embedding_eval.json"

DUAL_SEEDS = (42, 123, 7)
BASELINE_DUAL_MEAN = 0.6824  # v4_mild_noTE 双留出 3 种子均值（stage11/stage12 历史值）
GATE_DELTA = 0.03


def load_embeddings(df) -> tuple[np.ndarray, np.ndarray]:
    """加载 embedding 并与 stage11 缓存的 df 逐行断言对齐。"""
    pair = np.load(EMB_DIR / "gnn_emb_v53_pair.npy")
    mono = np.load(EMB_DIR / "gnn_emb_v53_mono.npy")
    with open(EMB_DIR / "gnn_emb_v53_index.json", encoding="utf-8") as f:
        index = json.load(f)
    assert len(pair) == len(df) == index["n_rows"], "embedding 行数与 df 不一致"
    assert list(df["aldehyde_smiles"]) == index["aldehyde_smiles"], "醛 SMILES 不对齐"
    assert list(df["amine_smiles"]) == index["amine_smiles"], "胺 SMILES 不对齐"
    return pair, mono


def gain_share(params: dict, X: np.ndarray, y: np.ndarray, weights: np.ndarray,
               n_base: int) -> dict:
    """全量训练 emb_pair_plus，按 描述符/embedding 分组统计 gain 占比。

    对照 exp_006 教训（指纹位 gain 占 53% = 按单体记忆），看 embedding 维的实际贡献。
    """
    m = XGBRegressor(**params, random_state=42, n_jobs=4, importance_type="gain")
    m.fit(X, y, sample_weight=weights)
    imp = np.asarray(m.feature_importances_, dtype=float)
    total = imp.sum()
    return {
        "descriptor_gain_share": float(imp[:n_base].sum() / total) if total else 0.0,
        "embedding_gain_share": float(imp[n_base:].sum() / total) if total else 0.0,
        "n_base_features": int(n_base),
        "n_emb_features": int(X.shape[1] - n_base),
    }


def main() -> None:
    t0 = time.time()
    X_base, y, df = load_xy()
    pair_emb, mono_emb = load_embeddings(df)
    n_base = X_base.shape[1]
    params = V4_CANDIDATES["v4_mild"]
    weights = frequency_sample_weights(df).values

    variants = {
        "emb_base": X_base,                              # 基线复跑（应 ≈ 历史 noTE）
        "emb_pair_only": pair_emb,                       # 512 纯配对 embedding
        "emb_pair_plus": np.hstack([X_base, pair_emb]),  # 654 描述符 + 配对 embedding
        "emb_mono_plus": np.hstack([X_base, mono_emb]),  # 398 描述符 + 单体 embedding
    }
    print(f"样本 {len(df)} 行；基线特征 {n_base} 维；"
          f"pair_emb {pair_emb.shape[1]} 维；mono_emb {mono_emb.shape[1]} 维", flush=True)

    results = {
        "protocols": {"logo": "GroupKFold-10 by aldehyde",
                      "dual": "自定义网格分组 5 折：验证集的醛和胺均不在训练集出现"},
        "config": {"params": {k: v for k, v in params.items()},
                   "use_te": False, "use_freq_weights": True,
                   "baseline": "v4_mild_noTE 双留出 3 种子均值 0.6824",
                   "gate": f"双留出 3 种子均值提升 ≥ +{GATE_DELTA} 且方向一致 → 进阶段 B"},
        "leakage_caveat": "GNN v5.3 训练集与本数据单体几乎完全重叠，阶段 A 结果为乐观上界",
        "variants": {},
    }

    for name, X in variants.items():
        entry = {"n_features": int(X.shape[1])}
        print(f"\n=== {name}（{X.shape[1]} 维）===", flush=True)
        logo_folds, _ = make_folds(df, y, X, dual_seed=42)
        _, entry["logo"] = run_cv(X, y, df, logo_folds, params, False, weights,
                                  f"{name}/LOGO")
        for seed in DUAL_SEEDS:
            _, dual_folds = make_folds(df, y, X, dual_seed=seed)
            key = "dual" if seed == 42 else f"dual_s{seed}"
            _, entry[key] = run_cv(X, y, df, dual_folds, params, False, weights,
                                   f"{name}/双留出/s{seed}")
        dual_means = [entry["dual"]["pr_auc"], entry["dual_s123"]["pr_auc"],
                      entry["dual_s7"]["pr_auc"]]
        entry["dual_3seed_mean"] = float(np.mean(dual_means))
        entry["dual_3seed_values"] = [float(v) for v in dual_means]
        results["variants"][name] = entry
        print(f"[{name}] 双留出 3 种子: {[f'{v:.4f}' for v in dual_means]} "
              f"均值 {entry['dual_3seed_mean']:.4f}", flush=True)

    # gain 分组诊断（emb_pair_plus）
    print("\n=== gain 分组诊断（emb_pair_plus 全量训练）===", flush=True)
    results["gain_diagnostic_pair_plus"] = gain_share(
        params, variants["emb_pair_plus"], y, weights, n_base)
    print(results["gain_diagnostic_pair_plus"], flush=True)

    # 判定
    base_mean = results["variants"]["emb_base"]["dual_3seed_mean"]
    summary = {"emb_base_rerun_mean": base_mean, "historical_baseline": BASELINE_DUAL_MEAN}
    for name in ("emb_pair_only", "emb_pair_plus", "emb_mono_plus"):
        m = results["variants"][name]["dual_3seed_mean"]
        vals = results["variants"][name]["dual_3seed_values"]
        delta = m - base_mean
        consistent = all(v > base for v, base in
                         zip(vals, results["variants"]["emb_base"]["dual_3seed_values"]))
        verdict = "PASS" if (delta >= GATE_DELTA and consistent) else "FALSIFIED"
        summary[name] = {"dual_3seed_mean": m, "delta_vs_base_rerun": float(delta),
                         "per_seed_consistent": bool(consistent), "verdict_vs_gate": verdict}
    results["decision_summary"] = summary

    tmp = REPORT_JSON.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=float)
    tmp.replace(REPORT_JSON)

    print("\n========== 汇总（双留出 PR-AUC，3 种子均值）==========")
    for name, e in results["variants"].items():
        print(f"{name:<16}{e['dual_3seed_mean']:.4f}  (LOGO {e['logo']['pr_auc']:.4f})")
    print(f"\n判定（门槛 +{GATE_DELTA} 且逐种子一致）：")
    for name, s in summary.items():
        if name.startswith("emb_") and name != "emb_base_rerun_mean":
            print(f"  {name}: delta={s['delta_vs_base_rerun']:+.4f} "
                  f"consistent={s['per_seed_consistent']} → {s['verdict_vs_gate']}")
    print(f"\n报告 -> {REPORT_JSON}，总耗时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
