"""单对预测 — 输入醛+胺 SMILES，输出成膜概率 + MC Dropout 不确定性。

Usage:
  python predict_pair.py --ald "O=CC1=CC=C(C=O)C=C1" --amine "NC1=CC=C(N)C=C1"
  python predict_pair.py --ald "O=CC1=CC=C(C=O)C=C1" --amine "NC1=CC=C(N)C=C1" --model models/v5.0/v5_model.pt --mc 20
"""
from __future__ import annotations

import argparse
import sys
import os

import torch
import numpy as np
from rdkit import Chem, RDLogger

RDLogger.logger().setLevel(RDLogger.ERROR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.screening.gnn_v3.featurizer import smiles_to_graph
from src.screening.gnn_v4.model import V4Model
from src.chemistry.hard_rules import get_rule_vector, RULE_DIM


def parse_args():
    p = argparse.ArgumentParser(description="单对成膜概率预测")
    p.add_argument("--ald", required=True, help="醛单体 SMILES")
    p.add_argument("--amine", required=True, help="胺单体 SMILES")
    p.add_argument("--model", default="models/v5.0/v5_model.pt", help="模型权重路径")
    p.add_argument("--mc", type=int, default=10, help="MC Dropout 采样次数 (默认 10)")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return p.parse_args()


def main():
    args = parse_args()
    device = args.device

    # ── 1. 加载模型 ──
    if not os.path.exists(args.model):
        print(f"错误: 模型文件不存在: {args.model}")
        sys.exit(1)

    ckpt = torch.load(args.model, map_location=device, weights_only=False)
    cfg = ckpt.get("config", {})
    use_3d = ckpt.get("use_3d", False)
    use_rules = ckpt.get("use_rules", True)
    if use_3d:
        cfg["model"]["use_3d"] = True
    cfg["model"]["use_rules"] = use_rules
    cfg["model"]["dim_rules"] = RULE_DIM

    model = V4Model(cfg).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    if use_3d and ckpt.get("scaler_3d"):
        sd = ckpt.get("scaler_dimer")
        model.set_3d_scaler(
            monomer_mean=ckpt["scaler_3d"]["mean"],
            monomer_std=ckpt["scaler_3d"]["std"],
            dimer_mean=sd["mean"] if sd else None,
            dimer_std=sd["std"] if sd else None,
        )

    # ── 2. 验证 SMILES ──
    ald_mol = Chem.MolFromSmiles(args.ald)
    amine_mol = Chem.MolFromSmiles(args.amine)
    if ald_mol is None:
        print(f"错误: 无法解析醛 SMILES: {args.ald}")
        sys.exit(1)
    if amine_mol is None:
        print(f"错误: 无法解析胺 SMILES: {args.amine}")
        sys.exit(1)

    # ── 3. 构建分子图 ──
    ald_graph = smiles_to_graph(args.ald, role=0)
    amine_graph = smiles_to_graph(args.amine, role=1)
    if ald_graph is None:
        print(f"错误: 醛分子图构建失败: {args.ald}")
        sys.exit(1)
    if amine_graph is None:
        print(f"错误: 胺分子图构建失败: {args.amine}")
        sys.exit(1)

    ald_graph = type(ald_graph)(
        x=ald_graph.x.to(device),
        edge_index=ald_graph.edge_index.to(device),
        edge_attr=ald_graph.edge_attr.to(device),
    )
    amine_graph = type(amine_graph)(
        x=amine_graph.x.to(device),
        edge_index=amine_graph.edge_index.to(device),
        edge_attr=amine_graph.edge_attr.to(device),
    )

    # ── 4. 规则向量 ──
    rule_vec = torch.tensor(
        get_rule_vector(args.ald, args.amine),
        dtype=torch.float, device=device,
    ).unsqueeze(0)

    # ── 5. MC Dropout 推理 ──
    model.enable_mc_dropout()
    mc_probs = []
    with torch.no_grad():
        for _ in range(args.mc):
            logit = model.predict_single(ald_graph, amine_graph, rule_vec=rule_vec)
            mc_probs.append(torch.sigmoid(logit).item())
    model.eval()

    prob_mean = float(np.mean(mc_probs))
    prob_std = float(np.std(mc_probs))

    # ── 6. 输出 ──
    print()
    print("=" * 56)
    print("  成膜预测结果")
    print("=" * 56)
    print(f"  醛 SMILES : {args.ald}")
    print(f"  胺 SMILES : {args.amine}")
    print(f"  {'─' * 40}")
    print(f"  成膜概率  : {prob_mean:.4f}")
    print(f"  不确定性  : ±{prob_std:.4f}  (MC Dropout, n={args.mc})")
    print(f"  {'─' * 40}")
    if prob_mean >= 0.8:
        verdict = "高概率成膜"
    elif prob_mean >= 0.5:
        verdict = "中等概率，可能成膜"
    elif prob_mean >= 0.3:
        verdict = "低概率，不易成膜"
    else:
        verdict = "极低概率，几乎不成膜"
    print(f"  判定      : {verdict}")
    print("=" * 56)
    print()


if __name__ == "__main__":
    main()
