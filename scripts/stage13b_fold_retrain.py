"""阶段 13 阶段 B：逐折重训 GNN（排除记忆泄漏），提取"闭卷" pair embedding。

背景（exp_009 / D24）：
- 阶段 A 用全量 GNN v5.3 的 embedding 使双留出 0.6824 → 0.844，但 GNN 训练集与本数据
  单体几乎完全重叠，无法区分"可迁移化学表征"与"配对标签记忆"；
- 阶段 B：对每个双留出折，**只用该折训练行从零重训 GNN**（验证折单体 GNN 从未见过），
  再提取 pair_emb——与树模型评估完全同信息口径，泄漏通道关闭。

设计（与 v5.3 训练行为逐位对齐，差异仅数据子集与折划分）：
- 训练数据：本折训练行写临时 CSV（aldehyde_smiles/amine_smiles/is_film），
  `PairDataset(use_3d=True, use_rules=True, freq_weights=None)`——3D 列缺失自动全零
  （v5.3 训练时 3D 即全零），freq_weights=None 等权（v5.3 频率降权因 "1" vs "1.0"
  字符串 bug 从未生效，None 即等效复刻）。
- 早停：折内训练行随机 10% 作 early-stop val（二值化 PR-AUC，patience=15）。
- 超参完全复刻 v5.3：V4Model(ckpt['config']) 从零初始化（**绝不加载 v5.3 权重**——
  权重本身即泄漏）、AdamW 5e-4/1e-4、CosineLR、FocalLoss(0.75, 2.0)、batch 64、
  max_epochs 200、grad_clip 1.0、AMP。
- 每折产出（data/interim/，不入 git；存在即跳过，断点续跑）：
  - `gnn_emb_foldb_s{seed}_f{k}.npy`：闭卷 pair_emb [3094, 512]（全行提取，树模型用）
  - `gnn_valprobs_foldb_s{seed}_f{k}.npy`：折内 GNN 直接预测验证折的概率（附赠诊断：
    GNN 自身的闭卷外推能力）
  - `gnn_foldb_meta_s{seed}.json`：逐折 best_epoch / best_pr_auc / 耗时 / 诊断指标

⚠️ 运行环境：dphuanjing（torch + PyG + rdkit）：
    E:\\ANACONDA\\envs\\dphuanjing\\python.exe scripts/stage13b_fold_retrain.py --dual-seed 42
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Subset
from torch_geometric.data import Data

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# stage13_extract_gnn_emb 与本脚本同目录（sys.path[0]），import 时它会插入旧项目根
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from stage13_extract_gnn_emb import (  # noqa: E402
    CHECKPOINT,
    OUT_DIR,
    extract,
    featurize_all,
    load_df,
)

OLD_PROJECT_ROOT = Path(r"C:\Users\ckx\Desktop\tianxuan seek")
sys.path.insert(0, str(OLD_PROJECT_ROOT))

from scripts.train_v4 import PairDataset, collate_fn  # noqa: E402
from src.screening.gnn_v4.model import V4Model  # noqa: E402
from src.screening.gnn_v4.v4_loss import FocalLoss  # noqa: E402
from src.screening.gnn_v4.v4_trainer import V4Trainer  # noqa: E402
from src.chemistry.hard_rules import RULE_DIM  # noqa: E402

FOLDS_JSON = OUT_DIR / "stage13b_folds.json"
TMP_DIR = OUT_DIR / "stage13b_tmp"
BATCH_SIZE = 64
MAX_EPOCHS = 200
PATIENCE = 15
ES_VAL_FRAC = 0.10  # 折内训练行随机 10% 作 early-stop val


def load_folds(seed: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """从 stage13b_folds.json 读取折索引（由 .venv 侧 stage11 原版函数生成）。

    ⚠️ 为什么不自己算：dual_holdout_folds 的分组依赖 pandas value_counts 顺序，
    跨 pandas 版本（dphuanjing py3.8 vs .venv py3.12）折分配会漂移——已在两环境
    实测不一致。折定义唯一真源 = stage11_dual_holdout.dual_holdout_folds，
    由 .venv 侧落盘为本 JSON，杜绝版本漂移。
    """
    if not FOLDS_JSON.exists():
        raise FileNotFoundError(
            f"缺折定义文件：{FOLDS_JSON}，先用 .venv 侧 stage11 函数生成")
    with open(FOLDS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    folds = data[str(seed)]
    return [(np.asarray(f["train_idx"]), np.asarray(f["val_idx"])) for f in folds]


def get_config() -> dict:
    """v5.3 ckpt 的训练配置（与 rebuild_model 相同的强制写入，但不加载权重）。"""
    ckpt = torch.load(CHECKPOINT, map_location="cpu", weights_only=False)
    cfg = ckpt.get("config", {})
    if ckpt.get("use_3d", False):
        cfg["model"]["use_3d"] = True
    cfg["model"]["use_rules"] = ckpt.get("use_rules", True)
    cfg["model"]["dim_rules"] = RULE_DIM
    return cfg


def write_fold_csv(df: pd.DataFrame, idx: np.ndarray, path: Path) -> None:
    df.iloc[idx][["aldehyde_smiles", "amine_smiles", "is_film"]].to_csv(
        path, index=False)


def es_split(n: int, seed: int, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """折内训练行 → 90% 训练 / 10% early-stop val（保证 val 二值化后双类齐全）。"""
    n_val = max(int(round(n * ES_VAL_FRAC)), 20)
    for attempt in range(20):
        rng = np.random.RandomState(seed + attempt)
        perm = rng.permutation(n)
        es_val, es_train = perm[:n_val], perm[n_val:]
        bin_val = (y[es_val] >= 0.5).astype(int)
        if len(set(bin_val.tolist())) > 1:
            return es_train, es_val
    raise RuntimeError("early-stop val 划分无法覆盖双类")


def train_fold_gnn(cfg: dict, df: pd.DataFrame, train_idx: np.ndarray,
                   val_idx: np.ndarray, seed: int, fold: int,
                   device: torch.device) -> tuple[V4Model, dict]:
    """在一折训练行上从零重训 GNN，返回 (加载 best_state 的模型, 元信息)。"""
    t0 = time.time()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    train_csv = TMP_DIR / f"train_s{seed}_f{fold}.csv"
    write_fold_csv(df, train_idx, train_csv)

    full_ds = PairDataset(str(train_csv), use_3d=True, use_rules=True,
                          freq_weights=None)
    y_local = df.iloc[train_idx]["is_film"].values.astype(float)
    es_train, es_val = es_split(len(full_ds), seed * 100 + fold, y_local)

    _collate = lambda b: collate_fn(b, True, True)  # noqa: E731
    train_loader = DataLoader(Subset(full_ds, es_train.tolist()), batch_size=BATCH_SIZE,
                              shuffle=True, collate_fn=_collate)
    val_loader = DataLoader(Subset(full_ds, es_val.tolist()), batch_size=BATCH_SIZE,
                            shuffle=False, collate_fn=_collate)

    torch.manual_seed(seed * 1000 + fold)
    np.random.seed(seed * 1000 + fold)
    model = V4Model(cfg).to(device)  # 从零初始化——绝不加载 v5.3 权重（权重即泄漏）
    loss_fn = FocalLoss(0.75, 2.0)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-4, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=MAX_EPOCHS, eta_min=1e-6)
    trainer = V4Trainer(model, loss_fn, opt, sched, device=str(device),
                        patience=PATIENCE, grad_clip=1.0, max_epochs=MAX_EPOCHS,
                        use_amp=True)

    for epoch in range(MAX_EPOCHS):
        m = trainer.step(train_loader, val_loader, epoch)
        if epoch % 10 == 0 or trainer.should_stop():
            print(f"  [s{seed}/f{fold}] E{epoch:3d} loss={m['train_loss']:.4f} "
                  f"es_val_pr_auc={m['val_pr_auc']:.4f} best={m['best_pr_auc']:.4f}",
                  flush=True)
        if trainer.should_stop():
            print(f"  [s{seed}/f{fold}] 早停 @ {epoch}", flush=True)
            break
    trainer.load_best()

    # 附赠诊断：折内 GNN 直接预测双留出验证折（闭卷外推能力）
    val_csv = TMP_DIR / f"val_s{seed}_f{fold}.csv"
    write_fold_csv(df, val_idx, val_csv)
    val_ds = PairDataset(str(val_csv), use_3d=True, use_rules=True, freq_weights=None)
    valid_mask = np.array([g is not None for g in val_ds.graphs])
    gnn_probs = gnn_predict_probs(model, val_ds, device)
    y_val = df.iloc[val_idx]["is_film"].values.astype(float)
    gnn_pr_auc = pr_auc_bin(y_val[valid_mask], np.asarray(gnn_probs))

    meta = {
        "n_train_rows": int(len(train_idx)), "n_es_val": int(len(es_val)),
        "best_epoch": int(trainer.best_epoch), "best_es_pr_auc": float(trainer.best_pr_auc),
        "gnn_direct_val": {"n_val": int(len(val_idx)), "n_valid": int(valid_mask.sum()),
                            "pr_auc": gnn_pr_auc},
        "train_seconds": float(time.time() - t0),
    }
    probs_path = OUT_DIR / f"gnn_valprobs_foldb_s{seed}_f{fold}.npy"
    np.save(probs_path, np.asarray(gnn_probs, dtype=np.float32))
    # 有效掩码（图构建失败的验证行被 collate 跳过）：评估脚本据此对齐 y_val
    mask_path = OUT_DIR / f"gnn_valmask_foldb_s{seed}_f{fold}.npy"
    np.save(mask_path, valid_mask)
    return model, meta


@torch.no_grad()
def gnn_predict_probs(model: V4Model, ds: PairDataset, device: torch.device) -> list[float]:
    """对 PairDataset 中图有效的行批量预测概率（保持行序，跳过 None）。"""
    loader = DataLoader(ds, batch_size=128, shuffle=False,
                        collate_fn=lambda b: collate_fn(b, True, True))
    model.eval()
    probs: list[float] = []
    for batch in loader:
        if batch is None:
            continue
        b = {k: (v.to(device) if isinstance(v, torch.Tensor) else v)
             for k, v in batch.items()}
        ald = Data(x=b["ald_x"], edge_index=b["ald_edge_index"], edge_attr=b["ald_edge_attr"])
        am = Data(x=b["amine_x"], edge_index=b["amine_edge_index"], edge_attr=b["amine_edge_attr"])
        logits = model(ald, am, b["ald_batch"], b["amine_batch"], b["batch_size"],
                       ald_3d=b.get("ald_3d"), amine_3d=b.get("amine_3d"),
                       dimer_3d=b.get("dimer_3d"), rule_vec=b.get("rule_vec"))
        probs.extend(torch.sigmoid(logits).cpu().tolist())
    return probs


def pr_auc_bin(y: np.ndarray, pred: np.ndarray) -> float:
    from sklearn.metrics import average_precision_score
    yb = (np.asarray(y) >= 0.5).astype(int)
    if len(set(yb.tolist())) < 2:
        return float("nan")
    return float(average_precision_score(yb, pred))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dual-seed", type=int, default=42)
    ap.add_argument("--folds", type=str, default="0,1,2,3,4",
                    help="逗号分隔的折号子集（默认全部 5 折；已存在的 npy 自动跳过）")
    args = ap.parse_args()
    seed = args.dual_seed
    fold_list = [int(x) for x in args.folds.split(",")]

    t0 = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device = {device}，dual_seed = {seed}，folds = {fold_list}", flush=True)

    df = load_df()
    folds = load_folds(seed)
    cfg = get_config()

    # 全行图只构建一次（所有折复用；提取在训练后进行，图本身无标签信息）
    ald_graphs, amine_graphs, n_fail = featurize_all(df)
    print(f"图构建完成：失败 {n_fail}/{len(df)}（补零向量）", flush=True)

    meta_path = OUT_DIR / f"gnn_foldb_meta_s{seed}.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

    for k in fold_list:
        emb_path = OUT_DIR / f"gnn_emb_foldb_s{seed}_f{k}.npy"
        if emb_path.exists():
            print(f"[s{seed}/f{k}] embedding 已存在，跳过（断点续跑）", flush=True)
            continue
        train_idx, val_idx = folds[k]
        print(f"\n=== [s{seed}/f{k}] 从零重训 GNN：训练行 {len(train_idx)}，"
              f"验证行 {len(val_idx)} ===", flush=True)
        model, fold_meta = train_fold_gnn(cfg, df, train_idx, val_idx, seed, k, device)

        # 用闭卷 GNN 提取全行 pair embedding（树模型随后只取折内对应行）
        pair_emb, _ = extract(model, df, ald_graphs, amine_graphs, device)
        if np.isnan(pair_emb).any():
            raise RuntimeError(f"[s{seed}/f{k}] embedding 含 NaN")
        np.save(emb_path, pair_emb)
        fold_meta["emb_path"] = str(emb_path)
        meta[f"fold_{k}"] = fold_meta
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False, default=float)
        print(f"[s{seed}/f{k}] 完成：best_epoch={fold_meta['best_epoch']} "
              f"es_pr_auc={fold_meta['best_es_pr_auc']:.4f} "
              f"gnn直接外推 pr_auc={fold_meta['gnn_direct_val']['pr_auc']:.4f} "
              f"耗时 {fold_meta['train_seconds'] / 60:.1f} min -> {emb_path}", flush=True)
        del model
        if device.type == "cuda":
            torch.cuda.empty_cache()

    print(f"\n[done] seed {seed} 总耗时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
