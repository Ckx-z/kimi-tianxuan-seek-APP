"""GNN v5.4 反馈微调（v1.8.0，需求一）：在 v5.4 权重上冻结底层微调顶层。

- 数据：base CSV（tree 训练同源的 v6 集）+ 反馈行（confirmed feedback 导出的
  aldehyde_smiles/amine_smiles/is_film CSV），反馈行加权重（正样本
  FEEDBACK_POS_W、负样本 FEEDBACK_NEG_W，叠乘 v5.4 的频率/组合级加权）。
- 迁移学习：冻结 input_proj + 前 (num_layers-1) 层 message passing
  （--freeze 可调层数），仅微调顶层 GINE 层 + attention + pooling + film_head。
- 早停：验证集 = 反馈行分层抽 15% + 基础集随机抽 5%，监控 val PR-AUC。
- 产出：<output>/v5_model.pt（v5.4 同构 checkpoint）+ calibrator.pkl
  （验证集 Isotonic）+ retrain_meta.json；训练进度写 <output>/progress.jsonl
  （job 状态接口据此展示阶段与指标）。

运行环境：dphuanjing（torch/PyG）；模型代码单一来源 gnn_runtime/src。
用法：
    <dphuanjing python> gnn_training/finetune.py --base-csv <v6.csv> \
        --feedback-csv <feedback.csv> --base-ckpt models/gnn_v5.4/v5_model.pt \
        --output <gnn_models>/<version>
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import torch
from rdkit import Chem, RDLogger
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score
from torch.utils.data import DataLoader

# 模型代码单一来源：gnn_runtime/src（本文件位于 <repo>/gnn_training/）
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "gnn_runtime"))

from src.screening.gnn_v4.model import V4Model  # noqa: E402
from src.screening.gnn_v3.featurizer import smiles_to_graph  # noqa: E402
from src.chemistry.hard_rules import get_rule_vector, RULE_DIM  # noqa: E402

RDLogger.logger().setLevel(RDLogger.ERROR)

# ---- v5.4 同构配置（内嵌，避免依赖 yaml 文件） ----
CFG = {
    "featurizer": {"atom_dim": 43, "edge_dim": 5},
    "encoder": {"in_dim": 43, "hidden_dim": 128, "num_layers": 3,
                "dropout": 0.15, "jk_pooling": "mean"},
    "attention": {"hidden_dim": 128, "num_heads": 4, "dropout": 0.15},
    "pooling": {"hidden_dim": 128, "pool_dim": 128, "num_queries": 4},
    "heads": {"hidden_dim": 128,
              "film_head": {"hidden_dims": [256, 128], "dropout": 0.25}},
    "loss": {"focal_alpha": 0.75, "focal_gamma": 2.0},
    "model": {"use_3d": False, "use_rules": True, "use_global": True,
              "monomer_3d_dim": 10, "dimer_3d_dim": 10, "dim_rules": RULE_DIM},
}

GROUP_WEIGHT = 2.0       # 不可成网配对组合级加权（v5.4 口径）
FEEDBACK_POS_W = 5.0     # 反馈正样本加权（纠偏文献/实验正样本）
FEEDBACK_NEG_W = 3.0     # 反馈负样本加权
FREEZE_LAYERS = 2        # 冻结 input_proj + 前 2 层 message passing（共 3 层）
VAL_FEEDBACK_RATIO = 0.15
VAL_BASE_RATIO = 0.05

_SM_ALDEHYDE = "[CX3H](=O)"
_SM_PRI_AMINE = "[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]"
_SM_SEC_AMINE = "[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"
_PAT = {k: Chem.MolFromSmarts(s) for k, s in
        {"ald": _SM_ALDEHYDE, "pri": _SM_PRI_AMINE, "sec": _SM_SEC_AMINE}.items()}


def log(msg: str) -> None:
    print(msg, flush=True)


def _progress(path: Path, line: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 工具（移植自旧项目）

def _functionality(smiles: str, role: str) -> int:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0
    if role == "aldehyde":
        return len(mol.GetSubstructMatches(_PAT["ald"]))
    return (len(mol.GetSubstructMatches(_PAT["pri"]))
            + len(mol.GetSubstructMatches(_PAT["sec"])))


def _can_network(a: str, b: str) -> bool:
    fa, fb = _functionality(a, "aldehyde"), _functionality(b, "amine")
    return (fa >= 2 and fb >= 2) or (fa >= 3 and fb >= 1) or (fb >= 3 and fa >= 1)


def _compute_freq_weights(rows: list[dict]) -> dict[tuple[str, str], float]:
    ald_freq: dict[str, int] = {}
    am_freq: dict[str, int] = {}
    for r in rows:
        if r["is_film"] != "1":
            continue
        ald_freq[r["aldehyde_smiles"]] = ald_freq.get(r["aldehyde_smiles"], 0) + 1
        am_freq[r["amine_smiles"]] = am_freq.get(r["amine_smiles"], 0) + 1
    max_ald = max(ald_freq.values()) if ald_freq else 1
    max_am = max(am_freq.values()) if am_freq else 1
    weights: dict[tuple[str, str], float] = {}
    for r in rows:
        af = ald_freq.get(r["aldehyde_smiles"], 1)
        amf = am_freq.get(r["amine_smiles"], 1)
        raw = 1.0 / max((af / max_ald) * (amf / max_am), 0.1)
        weights[(r["aldehyde_smiles"], r["amine_smiles"])] = min(max(raw, 0.5), 1.5)
    return weights


class FocalLoss(torch.nn.Module):
    """Focal Loss（连续标签，v5.4 口径）。"""

    def __init__(self, alpha: float = 0.75, gamma: float = 2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, logits, targets, weights=None):
        probs = torch.sigmoid(logits)
        p_t = torch.clamp(1.0 - torch.abs(targets - probs), min=1e-7, max=1.0 - 1e-7)
        alpha_t = self.alpha * targets + (1 - self.alpha) * (1 - targets)
        focal_weight = alpha_t * (1 - p_t).pow(self.gamma)
        bce = torch.nn.functional.binary_cross_entropy_with_logits(
            logits, targets, reduction="none")
        loss = focal_weight * bce
        if weights is not None:
            loss = loss * weights
        return loss.mean()


def _collate(batch: list) -> dict | None:
    batch = [b for b in batch if b is not None]
    if not batch:
        return None
    bs = len(batch)
    ald_x = torch.cat([b["ald_x"] for b in batch])
    amine_x = torch.cat([b["amine_x"] for b in batch])
    ald_ei, amine_ei, ald_ea, amine_ea = [], [], [], []
    ald_bl, amine_bl = [], []
    ald_off, amine_off = 0, 0
    for i, b in enumerate(batch):
        ald_ei.append(b["ald_edge_index"] + ald_off)
        amine_ei.append(b["amine_edge_index"] + amine_off)
        ald_ea.append(b["ald_edge_attr"])
        amine_ea.append(b["amine_edge_attr"])
        ald_bl.append(torch.full((b["ald_num_atoms"],), i, dtype=torch.long))
        amine_bl.append(torch.full((b["amine_num_atoms"],), i, dtype=torch.long))
        ald_off += b["ald_num_atoms"]
        amine_off += b["amine_num_atoms"]
    result = {
        "ald_x": ald_x, "ald_edge_index": torch.cat(ald_ei, dim=1),
        "ald_edge_attr": torch.cat(ald_ea), "ald_batch": torch.cat(ald_bl),
        "amine_x": amine_x, "amine_edge_index": torch.cat(amine_ei, dim=1),
        "amine_edge_attr": torch.cat(amine_ea), "amine_batch": torch.cat(amine_bl),
        "batch_size": bs,
        "film_label": torch.stack([b["film_label"] for b in batch]),
        "quality_weight": torch.stack([b["quality_weight"] for b in batch]),
        "rule_vec": torch.stack([b["rule_vec"] for b in batch]),
    }
    return result


def _build_one(args):
    idx, row = args
    g_ald = smiles_to_graph(row["aldehyde_smiles"], role=0, with_global=True)
    g_amine = smiles_to_graph(row["amine_smiles"], role=1, with_global=True)
    graph = {"ald": g_ald, "amine": g_amine} if g_ald and g_amine else None
    rule = get_rule_vector(row["aldehyde_smiles"], row["amine_smiles"])
    return idx, graph, rule


class PairDataset(torch.utils.data.Dataset):
    """v5.4 数据集 + 反馈行加权（多进程建图）。"""

    def __init__(self, rows: list[dict], feedback_keys: set[tuple[str, str]],
                 freq_weights: dict | None = None, n_workers: int = 12,
                 pos_w: float = FEEDBACK_POS_W, neg_w: float = FEEDBACK_NEG_W):
        self.rows = rows
        self._feedback_keys = feedback_keys
        self._pos_w = pos_w
        self._neg_w = neg_w
        self.freq_weights = freq_weights or {}
        import multiprocessing as mp
        tasks = [(i, r) for i, r in enumerate(rows)]
        self.graphs: list = [None] * len(rows)
        self.rules: list = [None] * len(rows)
        with mp.Pool(n_workers) as pool:
            for idx, graph, rule in pool.imap(_build_one, tasks, chunksize=32):
                self.graphs[idx] = graph
                self.rules[idx] = rule
        log(f"建图完成：{len(rows)} 行（反馈 {len(feedback_keys)} 行加权）")

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, idx):
        g = self.graphs[idx]
        r = self.rows[idx]
        if g is None:
            return None
        freq_w = self.freq_weights.get(
            (r["aldehyde_smiles"], r["amine_smiles"]), 1.0)
        group_w = 1.0 if _can_network(
            r["aldehyde_smiles"], r["amine_smiles"]) else GROUP_WEIGHT
        weight = freq_w * group_w
        key = (r["aldehyde_smiles"], r["amine_smiles"])
        if key in self._feedback_keys:
            weight *= (self._pos_w if float(r["is_film"]) >= 0.5
                       else self._neg_w)
        item = {
            "ald_x": g["ald"].x, "ald_edge_index": g["ald"].edge_index,
            "ald_edge_attr": g["ald"].edge_attr,
            "ald_num_atoms": g["ald"].x.shape[0],
            "amine_x": g["amine"].x, "amine_edge_index": g["amine"].edge_index,
            "amine_edge_attr": g["amine"].edge_attr,
            "amine_num_atoms": g["amine"].x.shape[0],
            "film_label": torch.tensor(float(r["is_film"]), dtype=torch.float),
            "quality_weight": torch.tensor(weight, dtype=torch.float),
            "rule_vec": torch.tensor(self.rules[idx], dtype=torch.float),
        }
        return item


# ---------------------------------------------------------------- 数据合并/过滤

def _filter_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        a = Chem.MolFromSmiles(r["aldehyde_smiles"])
        b = Chem.MolFromSmiles(r["amine_smiles"])
        if a is None or b is None:
            continue
        if a.GetNumHeavyAtoms() > 80 or b.GetNumHeavyAtoms() > 80:
            continue
        out.append(r)
    return out


def merge_rows(base_rows: list[dict], feedback_rows: list[dict]) -> tuple[list, set]:
    """合并反馈行；返回 (rows, feedback_keys)。

    反馈组合已在基础集中时**不重复追加行**，但仍加入 feedback_keys——
    这些组合正是「打分不合理」的纠偏对象，需要加权重学（×POS/NEG_W）。
    """
    base_keys = {(r["aldehyde_smiles"], r["amine_smiles"]) for r in base_rows}
    feedback_keys: set[tuple[str, str]] = set()
    merged = list(base_rows)
    added, weighted_existing = 0, 0
    for r in feedback_rows:
        key = (r["aldehyde_smiles"], r["amine_smiles"])
        if key in base_keys:
            feedback_keys.add(key)
            weighted_existing += 1
            continue
        if key in feedback_keys:
            continue
        row = {k: r.get(k, "") for k in
               ("paper_id", "source_db", "aldehyde_smiles", "amine_smiles",
                "is_film")}
        row.setdefault("paper_id", "gnn_feedback")
        row.setdefault("source_db", "feedback")
        merged.append(row)
        feedback_keys.add(key)
        added += 1
    log(f"反馈合并：{len(feedback_rows)} 行 → 新增 {added} 行、"
        f"基础集内加权 {weighted_existing} 行")
    return merged, feedback_keys


def _split_indices(rows: list[dict], feedback_keys: set) -> tuple[list, list]:
    """验证集 = 反馈行分层 15% + 基础集 5%。

    反馈行过少（<5）时全部留在训练集（纠偏样本必须被模型看到），
    验证集只从基础集抽 5%。
    """
    fb_idx = [i for i, r in enumerate(rows)
              if (r["aldehyde_smiles"], r["amine_smiles"]) in feedback_keys]
    base_idx = [i for i, r in enumerate(rows)
                if (r["aldehyde_smiles"], r["amine_smiles"]) not in feedback_keys]
    rng = np.random.default_rng(42)
    val_fb: list[int] = []
    if len(fb_idx) >= 5:
        val_fb = rng.choice(fb_idx,
                            size=max(1, int(len(fb_idx) * VAL_FEEDBACK_RATIO)),
                            replace=False).tolist()
    val_base = rng.choice(base_idx,
                          size=max(1, int(len(base_idx) * VAL_BASE_RATIO)),
                          replace=False).tolist() if base_idx else []
    val_set = set(val_fb) | set(val_base)
    train = [i for i in range(len(rows)) if i not in val_set]
    return train, sorted(val_set)


def _calibration_indices(rows: list[dict], feedback_keys: set) -> list[int]:
    """校准器拟合样本：全部反馈行 + 基础集按标签分层 10%。

    校准映射要覆盖部署分布（含成膜区间），单靠早停验证集（负样本占优）
    会把中等原始分单调压到 0（v1.8.0 pilot 真机踩坑）。
    """
    fb_idx = [i for i, r in enumerate(rows)
              if (r["aldehyde_smiles"], r["amine_smiles"]) in feedback_keys]
    pos = [i for i, r in enumerate(rows)
           if (r["aldehyde_smiles"], r["amine_smiles"]) not in feedback_keys
           and float(r["is_film"]) >= 0.5]
    neg = [i for i, r in enumerate(rows)
           if (r["aldehyde_smiles"], r["amine_smiles"]) not in feedback_keys
           and float(r["is_film"]) < 0.5]
    rng = np.random.default_rng(7)
    sel_pos = rng.choice(pos, size=max(0, int(len(pos) * 0.10)),
                         replace=False).tolist() if pos else []
    sel_neg = rng.choice(neg, size=max(0, int(len(neg) * 0.10)),
                         replace=False).tolist() if neg else []
    out = list(fb_idx) + sel_pos + sel_neg
    return sorted(set(out))


# ---------------------------------------------------------------- 微调

def _freeze(model: V4Model, freeze_layers: int) -> list[str]:
    """冻结底层：input_proj + 前 freeze_layers 层 message passing。"""
    frozen_prefixes = ["encoder.encoder.input_proj"]
    frozen_prefixes += [f"encoder.encoder.layers.{i}"
                        for i in range(min(freeze_layers, 99))]
    n = 0
    for name, p in model.named_parameters():
        if any(name.startswith(pref) for pref in frozen_prefixes):
            p.requires_grad = False
            n += 1
    trainable = sum(1 for p in model.parameters() if p.requires_grad)
    log(f"冻结：{frozen_prefixes}（{n} 个张量）；可训练张量 {trainable}")
    return frozen_prefixes


def _validate(model, loader, device) -> tuple[np.ndarray, np.ndarray]:
    from torch_geometric.data import Data
    model.eval()
    probs, labels = [], []
    with torch.no_grad():
        for b in loader:
            if b is None:
                continue
            b = {k: (v.to(device) if isinstance(v, torch.Tensor) else v)
                 for k, v in b.items()}
            ald = Data(x=b["ald_x"], edge_index=b["ald_edge_index"],
                       edge_attr=b["ald_edge_attr"])
            amine = Data(x=b["amine_x"], edge_index=b["amine_edge_index"],
                         edge_attr=b["amine_edge_attr"])
            logits = model(ald, amine, b["ald_batch"], b["amine_batch"],
                           b["batch_size"], ald_3d=None, amine_3d=None,
                           dimer_3d=None, rule_vec=b.get("rule_vec"))
            probs.append(torch.sigmoid(logits).cpu().numpy())
            labels.append(b["film_label"].cpu().numpy())
    return (np.concatenate(probs).reshape(-1),
            np.concatenate(labels).reshape(-1))


def main() -> None:
    ap = argparse.ArgumentParser(description="GNN v5.4 反馈微调")
    ap.add_argument("--base-csv", required=True, help="基础训练 CSV（v6）")
    ap.add_argument("--feedback-csv", required=True,
                    help="反馈 CSV（aldehyde_smiles,amine_smiles,is_film）")
    ap.add_argument("--base-ckpt", required=True, help="v5.4 基础权重")
    ap.add_argument("--output", required=True, help="输出版本目录")
    ap.add_argument("--freeze", type=int, default=FREEZE_LAYERS)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--feedback-pos-w", type=float, default=FEEDBACK_POS_W)
    ap.add_argument("--device", type=str,
                    default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = out_dir / "progress.jsonl"
    _progress(progress, {"phase": "data_parse", "ts": __import__("time").time()})

    with open(args.base_csv, "r", encoding="utf-8-sig") as f:
        base_rows = list(csv.DictReader(f))
    with open(args.feedback_csv, "r", encoding="utf-8-sig") as f:
        feedback_rows = list(csv.DictReader(f))
    rows, feedback_keys = merge_rows(base_rows, feedback_rows)
    rows = _filter_rows(rows)
    log(f"合并后 {len(rows)} 行（反馈新增 {len(feedback_keys)} 行）")

    _progress(progress, {"phase": "feature_build",
                         "n_rows": len(rows), "n_feedback": len(feedback_keys)})
    freq_weights = _compute_freq_weights(rows)
    ds = PairDataset(rows, feedback_keys, freq_weights=freq_weights,
                     pos_w=args.feedback_pos_w)
    train_idx, val_idx = _split_indices(rows, feedback_keys)
    train_ds = torch.utils.data.Subset(ds, train_idx)
    val_ds = torch.utils.data.Subset(ds, val_idx)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              collate_fn=_collate, drop_last=False)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                            collate_fn=_collate)

    # 从基础 checkpoint 取 config（v5.4 全同配置覆盖）
    ckpt = torch.load(args.base_ckpt, map_location="cpu", weights_only=False)
    cfg = copy.deepcopy(CFG)
    cfg["model"]["use_3d"] = bool(ckpt.get("use_3d", False))
    cfg["model"]["use_rules"] = bool(ckpt.get("use_rules", True))
    cfg["model"]["use_global"] = bool(ckpt.get("use_global", False))
    cfg["model"]["dim_rules"] = RULE_DIM

    model = V4Model(cfg).to(args.device)
    model.load_state_dict(ckpt["model_state"])
    frozen = _freeze(model, args.freeze)

    _progress(progress, {"phase": "fine_tune", "epochs": args.epochs,
                         "freeze": frozen, "lr": args.lr})
    loss_fn = FocalLoss(cfg["loss"]["focal_alpha"], cfg["loss"]["focal_gamma"])
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                            lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=args.epochs, eta_min=1e-6)

    best_pr_auc, best_state, no_improve = 0.0, None, 0
    for epoch in range(args.epochs):
        model.train()
        total_loss = 0.0
        n_batch = 0
        from torch_geometric.data import Data as _Data
        for b in train_loader:
            if b is None:
                continue
            b = {k: (v.to(args.device) if isinstance(v, torch.Tensor) else v)
                 for k, v in b.items()}
            ald = _Data(x=b["ald_x"], edge_index=b["ald_edge_index"],
                        edge_attr=b["ald_edge_attr"])
            amine = _Data(x=b["amine_x"], edge_index=b["amine_edge_index"],
                          edge_attr=b["amine_edge_attr"])
            opt.zero_grad()
            logits = model(ald, amine, b["ald_batch"], b["amine_batch"],
                           b["batch_size"], ald_3d=None, amine_3d=None,
                           dimer_3d=None, rule_vec=b.get("rule_vec"))
            loss = loss_fn(logits, b["film_label"], b.get("quality_weight"))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total_loss += loss.item()
            n_batch += 1
        sched.step()
        probs, labels = _validate(model, val_loader, args.device)
        bin_labels = (labels >= 0.5).astype(int)
        pr_auc = (float(average_precision_score(bin_labels, probs))
                  if len(set(bin_labels.tolist())) > 1 else 0.0)
        if pr_auc > best_pr_auc:
            best_pr_auc, best_state, no_improve = pr_auc, \
                copy.deepcopy(model.state_dict()), 0
        else:
            no_improve += 1
        _progress(progress, {"phase": "fine_tune", "epoch": epoch,
                             "train_loss": round(total_loss / max(n_batch, 1), 5),
                             "val_pr_auc": round(pr_auc, 4),
                             "best_pr_auc": round(best_pr_auc, 4)})
        log(f"E{epoch:3d} loss={total_loss / max(n_batch, 1):.4f} "
            f"val_pr_auc={pr_auc:.4f} best={best_pr_auc:.4f}")
        if no_improve >= args.patience:
            log(f"早停 @ epoch {epoch}")
            break
    model.load_state_dict(best_state)

    # ---- 落盘 checkpoint（先于校准，失败也不丢模型）----
    save_path = out_dir / "v5_model.pt"
    torch.save({
        "model_state": best_state,
        "config": cfg,
        "fold_pr_aucs": [best_pr_auc],
        "best_fold": 0,
        "scaler_3d": None, "scaler_dimer": None,
        "use_3d": cfg["model"]["use_3d"],
        "use_rules": cfg["model"]["use_rules"],
        "use_global": True,
        "finetune": {
            "base": Path(args.base_ckpt).name,
            "freeze": frozen,
            "feedback_rows": len(feedback_keys),
            "lr": args.lr, "epochs": args.epochs,
        },
    }, save_path)
    log(f"模型已保存: {save_path}")

    # ---- Isotonic 校准（分层校准样本：反馈行 + 基础集分层 10%）----
    cal_path = out_dir / "calibrator.pkl"
    try:
        cal_idx = _calibration_indices(rows, feedback_keys)
        cal_loader = DataLoader(torch.utils.data.Subset(ds, cal_idx),
                                batch_size=args.batch_size, shuffle=False,
                                collate_fn=_collate)
        probs, labels = _validate(model, cal_loader, args.device)
        cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        cal.fit(probs, labels)
        with open(cal_path, "wb") as f:
            pickle.dump(cal, f)
        log(f"校准器已保存: {cal_path}（分层样本 n={len(probs)}）")
    except Exception as exc:
        log(f"校准失败（跳过）: {exc}")
        cal_path = Path("")

    meta = {
        "version": out_dir.name,
        "base_version": "gnn_v5.4",
        "base_ckpt": str(args.base_ckpt),
        "feedback_keys": sorted([f"{a} | {b}" for a, b in feedback_keys]),
        "freeze": frozen,
        "params": {"lr": args.lr, "epochs": args.epochs,
                   "batch_size": args.batch_size, "patience": args.patience,
                   "feedback_pos_w": args.feedback_pos_w,
                   "feedback_neg_w": FEEDBACK_NEG_W},
        "metrics": {"val_pr_auc": round(best_pr_auc, 4),
                    "n_train": len(train_idx), "n_val": len(val_idx)},
        "calibrator": bool(cal_path) and cal_path.is_file(),
    }
    (out_dir / "retrain_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _progress(progress, {"phase": "done", "val_pr_auc": round(best_pr_auc, 4)})
    log(f"[done] val_pr_auc={best_pr_auc:.4f}")


if __name__ == "__main__":
    main()
