"""阶段 12：双模型路由端到端评估（D22，exp_008）。

问题：路由策略（醛/胺都已见 → tree_v4；任一未见 → tree_v4_noTE）
相对单一模型（v3 配置 / v4 / noTE）是否更优？

两个协议（与 stage11 同口径：合并折后算 PR-AUC/MAE，多种子报均值±std）：

协议 A —— 随机 KFold（5 折 × 3 种子），模拟 App 混合查询流：
  每折的训练部分组成"已见单体池"，验证样本按路由键分发：
  both-seen（醛胺都在训练折出现）走 v4，any-unseen 走 noTE。
  对比 routed vs v3/v4/noTE 的合并指标 + 分桶指标。

协议 B —— LOGO 分桶（GroupKFold-10 按醛，无种子依赖）：
  验证集醛必未见 → 路由恒走 noTE；按"胺是否已见于训练折"分桶，
  量化规定路由键在"醛新胺熟"混合桶上的代价（v4 的胺 TE 仍有信号）。

分块运行（每块独立落盘，可中断续跑）：
    .venv\\Scripts\\python.exe scripts/stage12_routing_eval.py --proto A --seed 42
    .venv\\Scripts\\python.exe scripts/stage12_routing_eval.py --proto A --seed 123
    .venv\\Scripts\\python.exe scripts/stage12_routing_eval.py --proto A --seed 7
    .venv\\Scripts\\python.exe scripts/stage12_routing_eval.py --proto B
    .venv\\Scripts\\python.exe scripts/stage12_routing_eval.py --finalize
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold, KFold
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from features.target_encoding import (  # noqa: E402
    apply_film_rates,
    fit_film_rates,
    frequency_sample_weights,
)
from stage11_dual_holdout import V3_PARAMS, V4_CANDIDATES, load_xy, pr_auc  # noqa: E402

FOLD_DIR = PROJECT_ROOT / "reports" / "routing_eval_folds"
REPORT_JSON = PROJECT_ROOT / "reports" / "routing_eval.json"

# 策略 = 模型配置；routed 由 v4/noTE 预测按路由键合成，不单独训练
MODEL_CONFIGS = {
    "v3": (V3_PARAMS, False, False),
    "v4": (V4_CANDIDATES["v4_mild"], True, True),
    "noTE": (V4_CANDIDATES["v4_mild"], False, True),
}
SEEDS = (42, 123, 7)


def fit_predict(X_base, y, df, tr, va, params, use_te, weights):
    """按防泄漏铁律训练并预测验证折（TE 映射表只用训练折拟合）。"""
    X_full = X_base
    if use_te:
        rates = fit_film_rates(df.iloc[tr])
        X_full = np.hstack([X_base, apply_film_rates(df, rates).values])
    m = XGBRegressor(**params, random_state=42, n_jobs=-1)
    kw = {"sample_weight": weights[tr]} if weights is not None else {}
    m.fit(X_full[tr], y[tr], **kw)
    return m.predict(X_full[va])


def run_folds(tag: str, X_base, y, df, folds, weights, seen_mask_fn) -> None:
    """在指定折划分上训练 3 个模型配置并合成 routed 预测，逐折落盘。

    seen_mask_fn(tr, va) -> (ald_seen_mask, amine_seen_mask)：验证样本的
    醛/胺是否在训练折出现过（协议 A 两侧都可能已见；协议 B 醛必未见）。
    """
    FOLD_DIR.mkdir(parents=True, exist_ok=True)
    for k, (tr, va) in enumerate(folds):
        out = FOLD_DIR / f"{tag}_fold{k}.pkl"
        if out.exists():
            print(f"  [{tag}] fold {k}: 已存在，跳过", flush=True)
            continue
        t0 = time.time()
        preds = {}
        for name, (params, use_te, use_w) in MODEL_CONFIGS.items():
            w = weights if use_w else None
            preds[name] = fit_predict(X_base, y, df, tr, va, params, use_te, w)

        ald_seen, amine_seen = seen_mask_fn(tr, va)
        both_seen = ald_seen & amine_seen
        # 路由：醛胺都已见 → v4；任一未见 → noTE
        routed = np.where(both_seen, preds["v4"], preds["noTE"])

        joblib.dump({
            "y_val": y[va],
            "ald_seen": ald_seen,
            "amine_seen": amine_seen,
            "both_seen": both_seen,
            "preds": {**preds, "routed": routed},
        }, out)
        print(f"  [{tag}] fold {k}: n_val={len(va)} both_seen={int(both_seen.sum())} "
              f"({time.time() - t0:.1f}s)", flush=True)


def proto_a(seed: int) -> None:
    X_base, y, df = load_xy()
    weights = frequency_sample_weights(df).values
    kf = KFold(n_splits=5, shuffle=True, random_state=seed)
    folds = list(kf.split(X_base))

    def seen_mask_fn(tr, va):
        ald_pool = set(df.iloc[tr]["aldehyde_smiles"])
        amine_pool = set(df.iloc[tr]["amine_smiles"])
        return (df.iloc[va]["aldehyde_smiles"].isin(ald_pool).values,
                df.iloc[va]["amine_smiles"].isin(amine_pool).values)

    print(f"=== 协议 A（随机 KFold-5，seed={seed}）===", flush=True)
    run_folds(f"A_seed{seed}", X_base, y, df, folds, weights, seen_mask_fn)


def proto_b() -> None:
    X_base, y, df = load_xy()
    weights = frequency_sample_weights(df).values
    ald_groups = df["aldehyde_smiles"].astype("category").cat.codes.values
    folds = list(GroupKFold(n_splits=10).split(X_base, y, ald_groups))

    def seen_mask_fn(tr, va):
        # LOGO：验证集醛必未见；胺可能在训练折出现过
        amine_pool = set(df.iloc[tr]["amine_smiles"])
        return (np.zeros(len(va), dtype=bool),
                df.iloc[va]["amine_smiles"].isin(amine_pool).values)

    print("=== 协议 B（LOGO GroupKFold-10 按醛）===", flush=True)
    run_folds("B_logo", X_base, y, df, folds, weights, seen_mask_fn)


def _with_synth_strategies(records: list[dict]) -> None:
    """从已保存的 v4/noTE 预测合成路由策略（无需重训）：

    - routed：规定路由键（D22 任务书）——任一未见 → noTE，双已见 → v4；
    - routed_strict：严格双未见键——仅醛胺均未见 → noTE，其余（含一新一熟）→ v4。
    """
    for r in records:
        r["preds"]["routed"] = np.where(r["both_seen"], r["preds"]["v4"], r["preds"]["noTE"])
        r["preds"]["routed_strict"] = np.where(r["both_seen"] | r["ald_seen"] | r["amine_seen"],
                                               r["preds"]["v4"], r["preds"]["noTE"])


STRATEGIES = ("v3", "v4", "noTE", "routed", "routed_strict")


def _merged_metrics(fold_records: list[dict], mask_key: str | None = None) -> dict:
    """合并多折预测后计算各策略指标；mask_key 指定时分桶计算。"""
    y = np.concatenate([r["y_val"] for r in fold_records])
    out = {}
    for strat in STRATEGIES:
        p = np.concatenate([r["preds"][strat] for r in fold_records])
        if mask_key is None:
            m = np.ones(len(y), dtype=bool)
        else:
            m = np.concatenate([r[mask_key] for r in fold_records])
        out[strat] = {
            "n": int(m.sum()),
            "pr_auc": pr_auc(y[m], p[m]) if m.sum() else None,
            "mae": float(mean_absolute_error(y[m], p[m])) if m.sum() else None,
        }
    return out


def finalize() -> None:
    report = {
        "protocols": {
            "A": "随机 KFold-5 × 3 种子：模拟 App 混合查询流；路由键按训练折单体池",
            "B": "LOGO GroupKFold-10 按醛：验证集醛必未见，按胺已见/未见分桶",
        },
        "routing_rule": "routed（上线规则，D22）= 醛/胺都已见 → tree_v4，任一未见 → tree_v4_noTE；"
                        "routed_strict（对照）= 仅醛胺均未见 → tree_v4_noTE，其余 → tree_v4",
        "model_configs": {k: {"use_te": v[1], "use_freq_weights": v[2]}
                          for k, v in MODEL_CONFIGS.items()},
    }

    # ---- 协议 A：逐种子合并，再报均值±std ----
    proto_a_seeds = []
    for seed in SEEDS:
        records = []
        for k in range(5):
            p = FOLD_DIR / f"A_seed{seed}_fold{k}.pkl"
            if not p.exists():
                raise RuntimeError(f"缺折记录：{p}，先运行 --proto A --seed {seed}")
            records.append(joblib.load(p))
        _with_synth_strategies(records)
        # 细分桶：双已见 / 一新一熟（混合）/ 双未见 / 含未见（=混合+双未见）
        for r in records:
            r["mixed"] = r["ald_seen"] ^ r["amine_seen"]
            r["both_unseen"] = ~r["ald_seen"] & ~r["amine_seen"]
            r["any_unseen"] = ~r["both_seen"]
        seed_entry = {
            "overall": _merged_metrics(records),
            "bucket_both_seen": _merged_metrics(records, "both_seen"),
            "bucket_mixed": _merged_metrics(records, "mixed"),
            "bucket_both_unseen": _merged_metrics(records, "both_unseen"),
            "bucket_any_unseen": _merged_metrics(records, "any_unseen"),
        }
        proto_a_seeds.append(seed_entry)

    def seed_stats(bucket: str, strat: str, metric: str) -> dict:
        vals = [s[bucket][strat][metric] for s in proto_a_seeds]
        return {"values": vals, "mean": float(np.mean(vals)), "std": float(np.std(vals))}

    buckets_a = ("overall", "bucket_both_seen", "bucket_mixed",
                 "bucket_both_unseen", "bucket_any_unseen")
    report["proto_A"] = {
        "seeds": list(SEEDS),
        "per_seed": proto_a_seeds,
        "summary": {
            bucket: {
                strat: {"pr_auc": seed_stats(bucket, strat, "pr_auc"),
                        "mae": seed_stats(bucket, strat, "mae")}
                for strat in STRATEGIES
            }
            for bucket in buckets_a
        },
    }

    # ---- 协议 B：LOGO 合并（无种子），按胺已见/未见分桶 ----
    records_b = []
    for k in range(10):
        p = FOLD_DIR / f"B_logo_fold{k}.pkl"
        if not p.exists():
            raise RuntimeError(f"缺折记录：{p}，先运行 --proto B")
        records_b.append(joblib.load(p))
    _with_synth_strategies(records_b)
    report["proto_B"] = {
        "overall": _merged_metrics(records_b),
        "bucket_amine_seen": _merged_metrics(records_b, "amine_seen"),
    }
    for r in records_b:
        r["amine_unseen"] = ~r["amine_seen"]
    report["proto_B"]["bucket_amine_unseen"] = _merged_metrics(records_b, "amine_unseen")

    tmp = REPORT_JSON.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=float)
    tmp.replace(REPORT_JSON)
    print(f"已保存报告: {REPORT_JSON}", flush=True)

    # ---- 汇总表 ----
    print("\n========== 协议 A：随机 KFold（3 种子均值±std）==========")
    for bucket, zh in (("overall", "整体"), ("bucket_both_seen", "双已见桶"),
                       ("bucket_mixed", "一新一熟桶"), ("bucket_both_unseen", "双未见桶"),
                       ("bucket_any_unseen", "含未见桶")):
        print(f"--- {zh} ---")
        for strat in STRATEGIES:
            s = report["proto_A"]["summary"][bucket][strat]
            n = proto_a_seeds[0][bucket][strat]["n"]
            print(f"  {strat:<14} n={n:<5} PR-AUC {s['pr_auc']['mean']:.4f}±{s['pr_auc']['std']:.4f}"
                  f"  MAE {s['mae']['mean']:.4f}")
    print("\n========== 协议 B：LOGO 分桶（无种子）==========")
    for bucket, zh in (("overall", "整体"), ("bucket_amine_seen", "胺已见桶"),
                       ("bucket_amine_unseen", "胺未见桶")):
        print(f"--- {zh} ---")
        for strat in STRATEGIES:
            s = report["proto_B"][bucket][strat]
            if s["pr_auc"] is None:
                continue
            print(f"  {strat:<14} n={s['n']:<5} PR-AUC {s['pr_auc']:.4f}  MAE {s['mae']:.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--proto", choices=["A", "B"], default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--finalize", action="store_true")
    args = ap.parse_args()

    if args.finalize:
        finalize()
    elif args.proto == "A":
        if args.seed is None:
            raise SystemExit("--proto A 需要 --seed")
        proto_a(args.seed)
    elif args.proto == "B":
        proto_b()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
