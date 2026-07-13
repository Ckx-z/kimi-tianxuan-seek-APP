"""v4 单体筛选 — GNN 成膜预测 + MC Dropout + 近邻外推检测 + 多样性排序。

筛选管线:
  单体池加载 → 可用醛/胺过滤 → 全交叉配对 → 训练集去重
  → 硬约束 (官能团 ≥2 + 拓扑匹配) → 图编码缓存
  → 成对推理 (MC Dropout + 嵌入) → 近邻距离 → 多样性 Top K

Usage:
  python scripts/screen_v4.py
  python scripts/screen_v4.py --model models/v4.0_full3d/v4_model.pt --use-3d
  python scripts/screen_v4.py --top-k 40 --mc-samples 20
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np
import torch
import yaml
from rdkit import Chem, RDLogger
from sklearn.metrics.pairwise import cosine_distances

RDLogger.logger().setLevel(RDLogger.ERROR)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.screening.gnn_v4.model import V4Model
from src.screening.gnn_v3.featurizer import smiles_to_graph
from src.chemistry.conformer import compute_3d_descriptors, DESCRIPTOR_NAMES
from src.chemistry.dimer import compute_dimer_3d, DIMER_DESCRIPTOR_NAMES
from src.chemistry.linker_analyzer import (
    is_functionally_symmetric, has_heterocycle, count_aromatic_rings,
)
from src.utils.logger import setup_logger

logger = setup_logger("screen_v4")

_METALS = {3, 4, 11, 12, 13, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
           31, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 55, 56,
           57, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83}

_HALOGENS = {9, 17, 35, 53}

# SMARTS 模式
_ALD_SMARTS = Chem.MolFromSmarts("[CX3H1](=O)[#6]")  # 醛基 C + 连接碳
_AMINE_SMARTS = Chem.MolFromSmarts("[NH2]")             # 胺基计数
_AMINE_RING_SMARTS = Chem.MolFromSmarts("[NH2][#6]")    # 胺基 N + 连接碳 (对位检查)
_AROMATIC_SMARTS = Chem.MolFromSmarts("[a]")
_BENZENE_SMARTS = Chem.MolFromSmarts("c1ccccc1")
_N_HETERO_SMARTS = Chem.MolFromSmarts("[n]")
# 官能团有效性检测
_HYDRAZIDE_SMARTS = Chem.MolFromSmarts("[NH2][NH]C(=O)")  # 酰肼 NH2
_CARBOHYDRAZIDE_SMARTS = Chem.MolFromSmarts("[NH2][NH]C(=O)[NH][NH2]")  # 碳酰肼
_AMIDE_NH2_SMARTS = Chem.MolFromSmarts("[NH2]C(=O)")      # 酰胺 NH2
_SULFONAMIDE_SMARTS = Chem.MolFromSmarts("[NH2]S(=O)(=O)")  # 磺酰胺
_BORONATE_SMARTS = Chem.MolFromSmarts("[B]([O])([O])")     # 硼酸酯
_ESTER_ALDEHYDE_SMARTS = Chem.MolFromSmarts("[CX3](=O)[O][#6]")  # 酯羰基 (可能被误判为醛基)


def _canon_smiles(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(mol, isomericSmiles=True) if mol else smiles


def _has_metal(smiles: str) -> bool:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return True
    return any(a.GetAtomicNum() in _METALS for a in mol.GetAtoms())


def check_topology(n_ald: int, n_amine: int) -> str | None:
    """醛-胺拓扑匹配。2D imine COF 网络拓扑：
    C3+C2 (或 C2+C3, C3+C3) → 六方，C2+C2 → 四方。
    排除: C4+/C1/官能团数不匹配。
    """
    if n_ald < 2 or n_amine < 2:
        return None
    if n_ald >= 4 or n_amine >= 4:
        return None  # C4+ 不形成规整 2D 网络
    if n_ald == 2 and n_amine == 3:
        return "hexagonal"
    if n_ald == 3 and n_amine == 2:
        return "hexagonal"
    if n_ald == 3 and n_amine == 3:
        return "hexagonal"
    if n_ald == 2 and n_amine == 2:
        return "tetragonal"
    return None


# ── 化学硬约束 ────────────────────────────────────────────

def _count_aromatic_rings(mol: Chem.Mol) -> int:
    """统计芳环数。"""
    if mol is None:
        return 0
    ri = mol.GetRingInfo()
    return sum(1 for ring in ri.AtomRings()
               if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring))


def _flexibility_penalty(ald_smi: str, am_smi: str) -> tuple[float, str, int, int, int]:
    """柔性软降权: 总芳环范围 [4,8], 大+小失衡例外。

    规则:
      - total < 4: 降权 ×0.8 (太简单，小分子不易成膜)
      - total > 8: 降权 ×0.7 (太刚性)
      - 一个 ≥ 4 且另一个 ≤ 3: 不降权 (大+小失衡，好配对)
      - 两个都 ≥ 4: 降权 ×0.7 (刚×刚)
      - 4 ≤ total ≤ 8 且都 ≤ 3: 不降权 (正常)

    Returns:
        (factor, reason, ra, rm, total)
    """
    ald_mol = Chem.MolFromSmiles(ald_smi)
    am_mol = Chem.MolFromSmiles(am_smi)
    if ald_mol is None or am_mol is None:
        return 1.0, "parse_fail", 0, 0, 0

    ra = _count_aromatic_rings(ald_mol)
    rm = _count_aromatic_rings(am_mol)
    total = ra + rm

    if ra > 7 or rm > 7:
        return 0.7, f"oversize(ra={ra},rm={rm})", ra, rm, total
    if total < 4:
        return 0.5, f"too_simple(t={total})", ra, rm, total
    if total > 8:
        return 0.5, f"too_rigid(ra={ra},rm={rm},t={total})", ra, rm, total
    if (ra >= 4 and rm <= 3) or (rm >= 4 and ra <= 3):
        return 1.0, f"big+small(ra={ra},rm={rm})", ra, rm, total
    if ra >= 4 and rm >= 4:
        return 0.7, f"rigid(ra={ra},rm={rm},t={total})", ra, rm, total
    return 1.0, f"normal(t={total})", ra, rm, total


def _count_valid_aldehydes(mol: Chem.Mol) -> int:
    """统计真正的醛基数 (SMARTS 匹配, 排除环内碳和酯)。"""
    if mol.HasSubstructMatch(_ESTER_ALDEHYDE_SMARTS):
        return 0  # 含酯羰基的单体不是真正的醛
    matches = mol.GetSubstructMatches(_ALD_SMARTS)
    valid = 0
    for m in matches:
        c_idx = m[0]
        c_atom = mol.GetAtomWithIdx(c_idx)
        if not c_atom.IsInRing():
            valid += 1
    return valid


def _count_valid_amines(mol: Chem.Mol) -> int:
    """统计真正的伯胺数 (排除酰肼/酰胺/磺酰胺)。"""
    if mol.HasSubstructMatch(_CARBOHYDRAZIDE_SMARTS):
        return 0  # 碳酰肼不是胺
    if mol.HasSubstructMatch(_HYDRAZIDE_SMARTS):
        return 0  # 含酰肼基团不是胺
    if mol.HasSubstructMatch(_AMIDE_NH2_SMARTS):
        return 0  # 含酰胺 NH2 不是胺
    if mol.HasSubstructMatch(_SULFONAMIDE_SMARTS):
        return 0  # 含磺酰胺不是胺
    matches = mol.GetSubstructMatches(_AMINE_SMARTS)
    # 对每个 NH2 检查是否是真正的伯胺
    valid = 0
    for m in matches:
        n_idx = m[0]
        n_atom = mol.GetAtomWithIdx(n_idx)
        # 排除 NH2 连 N (酰肼/肼)、连 C=O (酰胺)、连 S=O (磺酰胺)
        heavy = [nb for nb in n_atom.GetNeighbors() if nb.GetAtomicNum() != 1]
        if len(heavy) != 1:
            continue
        nb = heavy[0]
        if nb.GetAtomicNum() == 7:
            continue  # NH2-N → 肼/酰肼/三氮烯
        if nb.GetAtomicNum() == 6:
            has_carbonyl = any(nb2.GetAtomicNum() == 8 and
                mol.GetBondBetweenAtoms(nb.GetIdx(), nb2.GetIdx()) is not None and
                mol.GetBondBetweenAtoms(nb.GetIdx(), nb2.GetIdx()).GetBondType() == Chem.BondType.DOUBLE
                for nb2 in nb.GetNeighbors())
            if has_carbonyl:
                continue  # NH2-C(=O) → 酰胺
        if nb.GetAtomicNum() == 16:
            has_so = any(nb2.GetAtomicNum() == 8 and
                mol.GetBondBetweenAtoms(nb.GetIdx(), nb2.GetIdx()) is not None and
                mol.GetBondBetweenAtoms(nb.GetIdx(), nb2.GetIdx()).GetBondType() == Chem.BondType.DOUBLE
                for nb2 in nb.GetNeighbors())
            if has_so:
                continue  # NH2-S(=O) → 磺酰胺
        valid += 1
    return valid


def _get_reactive_carbons(mol: Chem.Mol, n_ald: int, n_am: int) -> list[int]:
    """获取所有反应位点连接碳的原子索引 (醛基C/胺基连接C)。"""
    carbons = []
    if n_ald > 0:
        matches = mol.GetSubstructMatches(_ALD_SMARTS)
        carbons.extend(m[2] for m in matches if not mol.GetAtomWithIdx(m[0]).IsInRing())
    if n_am > 0:
        matches = mol.GetSubstructMatches(_AMINE_RING_SMARTS)
        carbons.extend(m[1] for m in matches)
    return carbons


def _check_ring_para_positions(mol: Chem.Mol, reactive_carbons: list[int]) -> tuple[bool, str]:
    """逐环检查对位取代 — C2/C3 通用。
    对每个六元芳环: 若恰有 2 个非H取代点且至少其一连接反应位点 → 必须对位。
    返回 (通过, 失败环信息)。
    """
    rc_set = set(reactive_carbons)
    ring_info = mol.GetRingInfo()
    for ring in ring_info.AtomRings():
        if len(ring) != 6:
            continue  # 仅检查六元环
        ring_set = set(ring)
        # 统计环上每个原子的非H非环取代
        sub_positions = {}
        for ai in ring:
            atom = mol.GetAtomWithIdx(ai)
            for nb in atom.GetNeighbors():
                nidx = nb.GetIdx()
                if nidx not in ring_set and nb.GetAtomicNum() != 1:
                    sub_positions.setdefault(ai, []).append(nidx)
        if len(sub_positions) != 2:
            continue  # 不为2个取代点的不检查
        # 检查是否涉及反应位点
        ring_atoms = list(sub_positions.keys())
        has_rc = any(
            ring_atoms[i] in rc_set or any(nb in rc_set for nb in sub_positions[ring_atoms[i]])
            for i in range(2)
        )
        if not has_rc:
            continue
        # 计算沿环键距
        path = Chem.GetShortestPath(mol, ring_atoms[0], ring_atoms[1])
        if path is None:
            continue
        ring_bonds = sum(1 for k in range(len(path) - 1)
                        if path[k] in ring_set and path[k + 1] in ring_set)
        if ring_bonds <= 2:  # 邻位(1)或间位(2) → 非对位
            return False, f"nonpara(ring6_sub2_bonds{ring_bonds})"
    return True, ""


def check_monomer_penalties(smi: str, n_ald: int, n_am: int) -> tuple[float, list[str]]:
    """单体级软约束 — 返回 (惩罚因子, 原因列表)，替代硬排除。
    因子 < 1 表示降权，原因列表为空表示完全通过。
    """
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return 0.1, ["SMILES解析失败"]

    penalty = 1.0
    reasons = []

    # 1. 芳环检查
    if not mol.HasSubstructMatch(_AROMATIC_SMARTS):
        penalty *= 0.3
        reasons.append("无芳香环")

    # 2. 芳环上限 — 仅记录，不处罚（与 _flexibility_penalty 的刚性规则互补，避免双重惩罚）
    n_rings = count_aromatic_rings(mol)
    if n_rings > 6:
        reasons.append(f"芳环>{n_rings}")

    # 3. 官能团有效性
    if n_ald > 0:
        valid_ald = _count_valid_aldehydes(mol)
        if valid_ald == 0:
            penalty *= 0.2
            reasons.append("无有效醛基")
        elif valid_ald != n_ald:
            penalty *= 0.5
            reasons.append(f"醛基不匹配(称{n_ald}实{valid_ald})")
    if n_am > 0:
        valid_am = _count_valid_amines(mol)
        if valid_am == 0:
            penalty *= 0.2
            reasons.append("无有效伯胺")
        elif valid_am != n_am:
            penalty *= 0.5
            reasons.append(f"胺基不匹配(称{n_am}实{valid_am})")

    # 4. 硼酸酯
    if mol.HasSubstructMatch(_BORONATE_SMARTS):
        penalty *= 0.3
        reasons.append("含硼酸酯")

    # 5. 反应位点对称性 — 两阶段检测:
    #    Morgan FP (r=2) → 局部环境是否等价
    #    CanonicalRankAtoms → 全局拓扑是否等价
    #    局部等价 + 全局不等 → 中心对称分子 (联苯等), 轻罚 ×0.85
    #    局部不等 → 确认不对称, 重罚 ×0.7
    fp_symmetric = is_functionally_symmetric(mol)  # Morgan FP r=2

    # 6. 反应位点对称 (CanonicalRankAtoms)
    ranks_differ = False
    if n_ald >= 2 or n_am >= 2:
        ranks = Chem.CanonicalRankAtoms(mol, breakTies=False)
        if n_ald >= 2:
            ald_matches = mol.GetSubstructMatches(_ALD_SMARTS)
            valid_matches = [m for m in ald_matches
                           if not mol.GetAtomWithIdx(m[0]).IsInRing()]
            ald_ranks = [ranks[m[0]] for m in valid_matches]
            if len(ald_ranks) >= 2 and len(set(ald_ranks)) > 1:
                ranks_differ = True
        if n_am >= 2 and not ranks_differ:
            valid_all = _count_valid_amines(mol) >= n_am
            if valid_all:
                am_matches = mol.GetSubstructMatches(_AMINE_SMARTS)
                # 只取有效伯胺的N原子
                valid_am_N = []
                for m in am_matches:
                    n_idx = m[0]
                    n_atom = mol.GetAtomWithIdx(n_idx)
                    heavy = [nb for nb in n_atom.GetNeighbors() if nb.GetAtomicNum() != 1]
                    if len(heavy) == 1 and heavy[0].GetAtomicNum() == 6:
                        valid_am_N.append(n_idx)
                if len(valid_am_N) >= 2:
                    am_ranks = [ranks[i] for i in valid_am_N]
                    if len(set(am_ranks)) > 1:
                        ranks_differ = True

    if not fp_symmetric:
        penalty *= 0.7
        reasons.append("官能团不对称")
    elif ranks_differ:
        # 局部等价但全局 ranking 不等 → 中心对称/联苯类分子
        penalty *= 0.85
        reasons.append("官能团中心对称(轻罚)")

    # 7. 逐环对位检查 (C2/C3 通用, 五元环跳过)
    if n_ald >= 2 or n_am >= 2:
        rc = _get_reactive_carbons(mol, n_ald, n_am)
        ok, reason = _check_ring_para_positions(mol, rc)
        if not ok:
            penalty *= 0.4
            reasons.append(reason)

    # 7b. C2 同环对位: 两个反应碳在同一六元环上必须对位
    if n_ald == 2 or n_am == 2:
        rc = _get_reactive_carbons(mol, n_ald, n_am)
        if len(rc) == 2:
            ring_info = mol.GetRingInfo()
            for ring in ring_info.AtomRings():
                if len(ring) == 6 and rc[0] in ring and rc[1] in ring:
                    ring_set = set(ring)
                    path = Chem.GetShortestPath(mol, rc[0], rc[1])
                    if path:
                        ring_bonds = sum(1 for k in range(len(path) - 1)
                                        if path[k] in ring_set and path[k + 1] in ring_set)
                        if ring_bonds <= 2:
                            penalty *= 0.4
                            reasons.append(f"nonpara_C2(bonds{ring_bonds})")
                    break

    return penalty, reasons


def _check_both_groups(mol: Chem.Mol, is_aldehyde: bool) -> tuple[bool, str]:
    """检查单体是否同时含醛基和胺基 (自聚合风险)。
    is_aldehyde=True: 检查醛单体是否意外含有效伯胺。
    is_aldehyde=False: 检查胺单体是否意外含有效醛基。
    """
    if is_aldehyde:
        n_extra = _count_valid_amines(mol)
        return n_extra > 0, f"醛单体含{n_extra}个有效胺基"
    else:
        n_extra = _count_valid_aldehydes(mol)
        return n_extra > 0, f"胺单体含{n_extra}个有效醛基"


def _max_side_chain(mol: Chem.Mol) -> int:
    """计算最长非环线性链原子数 (PEG链/烷基链检测)。

    在非环单键构成的森林图中找最长路径。
    """
    non_ring = [i for i in range(mol.GetNumAtoms())
                if not mol.GetAtomWithIdx(i).IsInRing()]
    if len(non_ring) < 4:
        return 0
    nr_set = set(non_ring)
    # 构建非环单键邻接表
    adj = {i: [] for i in non_ring}
    for i in non_ring:
        a = mol.GetAtomWithIdx(i)
        for nb in a.GetNeighbors():
            j = nb.GetIdx()
            if j in nr_set:
                bond = mol.GetBondBetweenAtoms(i, j)
                if bond and bond.GetBondType() == Chem.BondType.SINGLE:
                    adj[i].append(j)
    # 找叶节点并 BFS 两遍法求最长路径 (森林直径)
    leaves = [i for i in non_ring if len(adj[i]) <= 1]
    if not leaves:
        return 0

    def _bfs_farthest(start):
        q = [start]
        dist = {start: 0}
        farthest = start
        for node in q:
            for nb in adj[node]:
                if nb not in dist:
                    dist[nb] = dist[node] + 1
                    q.append(nb)
                    if dist[nb] > dist[farthest]:
                        farthest = nb
        return farthest, dist[farthest]

    # 从任意叶节点出发找最远端，再从最远端找直径
    a, _ = _bfs_farthest(leaves[0])
    _, diameter = _bfs_farthest(a)
    return diameter + 1  # 原子数 = 边数 + 1


def _monomer_pool(emb: "torch.Tensor") -> "np.ndarray":
    """Mean-pool per-atom GNN 编码器嵌入 → 单体级向量 [hidden_dim]。
    用于单体间余弦相似度计算 (外推检测)。
    """
    return emb.mean(dim=0).cpu().numpy()


def check_pair_soft_constraints(ald_info: dict, amine_info: dict,
                                raw_prob: float) -> tuple[float, list[str]]:
    """配对级软约束 — 返回 (调整后概率, 原因列表)。

    包括: 自反应检测、单体级惩罚、C3/杂环奖励、线性链惩罚。
    """
    reasons = []
    penalty = 1.0

    ald_mol = Chem.MolFromSmiles(ald_info["smiles"])
    am_mol = Chem.MolFromSmiles(amine_info["smiles"])
    if ald_mol is None or am_mol is None:
        return raw_prob, ["parse_fail"]

    n_ald = ald_info["n_aldehyde"]
    n_am = amine_info["n_amine"]

    # ── 自反应检测: 醛单体含胺基 或 胺单体含醛基 (point 1) ──
    ald_both, ald_both_reason = _check_both_groups(ald_mol, is_aldehyde=True)
    am_both, am_both_reason = _check_both_groups(am_mol, is_aldehyde=False)
    if ald_both:
        penalty *= 0.2
        reasons.append(ald_both_reason)
    if am_both:
        penalty *= 0.2
        reasons.append(am_both_reason)

    # ── 拓扑不匹配 (C4+/C1/官能团数不匹配) ──
    if ald_info.get("topo_mismatch") or amine_info.get("topo_mismatch"):
        penalty *= 0.3
        reasons.append(f"拓扑不匹配(n_ald={n_ald},n_am={n_am})")

    # ── 单体级软惩罚 (替代原硬约束, point 5) ──
    ald_pen, ald_reasons = check_monomer_penalties(ald_info["smiles"], n_ald, 0)
    am_pen, am_reasons = check_monomer_penalties(amine_info["smiles"], 0, n_am)
    if ald_reasons:
        penalty *= ald_pen
        reasons.extend(f"醛:{r}" for r in ald_reasons)
    if am_reasons:
        penalty *= am_pen
        reasons.extend(f"胺:{r}" for r in am_reasons)

    # ── C3/杂环 — 仅记录，不奖励（避免 adj 全部饱和到 1.0）──
    if n_ald >= 3:
        reasons.append("C3醛")
    if n_am >= 3:
        reasons.append("C3胺")
    if has_heterocycle(ald_mol):
        reasons.append("醛杂环")
    if has_heterocycle(am_mol):
        reasons.append("胺杂环")

    # ── 线性链惩罚 ──
    chain_ald = _count_linear_para_chain(ald_mol)
    chain_am = _count_linear_para_chain(am_mol)
    max_chain = max(chain_ald, chain_am)
    if max_chain > 3:
        excess = max_chain - 3
        chain_pen = max(0.4, 1.0 - excess * 0.08)
        penalty *= chain_pen
        reasons.append(f"对位苯链{max_chain}(×{chain_pen:.2f})")

    # ── 长支链惩罚 (PEG/烷基链过长 → 柔性过大) ──
    sc_ald = _max_side_chain(ald_mol)
    sc_am = _max_side_chain(am_mol)
    max_sc = max(sc_ald, sc_am)
    if max_sc > 10:
        sc_pen = max(0.5, 1.0 - (max_sc - 10) * 0.05)
        penalty *= sc_pen
        reasons.append(f"长支链{max_sc}(×{sc_pen:.2f})")

    adjusted = raw_prob * penalty
    return min(adjusted, 1.0), reasons


def _count_linear_para_chain(mol: Chem.Mol) -> int:
    """计算对位苯环最长连续链长度。"""
    rings = mol.GetSubstructMatches(_BENZENE_SMARTS)
    if len(rings) <= 1:
        return len(rings)
    n = len(rings)
    adj = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            shared = set(rings[i]) & set(rings[j])
            if shared:
                # 检查是否通过非环原子桥接 (如 biphenyl 共享两个碳)
                # 共享原子说明是稠环，不算链
                pass
            else:
                # 检查对位连接: 环 i 和环 j 之间通过 para 位置连接
                # 简化: 检查两环之间是否有 bond
                for ai in rings[i]:
                    for aj in rings[j]:
                        bond = mol.GetBondBetweenAtoms(ai, aj)
                        if bond is not None:
                            adj[i].add(j)
                            adj[j].add(i)

    def dfs(node, visited):
        max_d = 0
        for nb in adj[node]:
            if nb not in visited:
                max_d = max(max_d, dfs(nb, visited | {nb}))
        return max_d + 1

    max_chain = 0
    for i in range(n):
        max_chain = max(max_chain, dfs(i, {i}))
    return max_chain


# ── 多样性排序 ───────────────────────────────────────────

def maxmin_diversity(scores: list[float], embeddings: np.ndarray,
                     top_k: int = 20) -> list[int]:
    """MaxMin 多样性 — 从 top 10% 中按余弦距离最大最小选择。"""
    n = len(scores)
    if n <= top_k:
        return list(range(n))

    pool_size = max(top_k, int(n * 0.1))
    pool_idx = np.argsort(scores)[::-1][:pool_size]
    pool_emb = embeddings[pool_idx]
    pool_scores = np.array(scores)[pool_idx]

    selected = [0]
    remaining = list(range(1, len(pool_idx)))

    while len(selected) < min(top_k, len(pool_idx)):
        sel_emb = pool_emb[selected]
        best_i, best_dist = -1, -1
        for rem in remaining:
            min_d = float(np.min(cosine_distances(
                pool_emb[rem:rem + 1], sel_emb)[0]))
            score = min_d + 0.001 * pool_scores[rem]
            if score > best_dist:
                best_dist = score
                best_i = rem
        selected.append(best_i)
        remaining.remove(best_i)

    return pool_idx[np.array(selected)].tolist()


# ── 单体池加载 ───────────────────────────────────────────

def load_monomer_pool(csv_path: str) -> tuple[list[dict], list[dict]]:
    """加载单体池，过滤金属 → (醛列表, 胺列表)。"""
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    aldehydes, amines = [], []
    for r in rows:
        smi = r.get("smiles", "").strip()
        if not smi or smi == "nan" or _has_metal(smi):
            continue
        n_ald = int(r.get("n_aldehyde", 0) or 0)
        n_am = int(r.get("n_amine", 0) or 0)
        is_ald = (r.get("is_aldehyde", "") == "True") or n_ald > 0
        is_am = (r.get("is_amine", "") == "True") or n_am > 0
        info = {
            "smiles": smi, "best_name": r.get("best_name", ""),
            "source": r.get("source", ""),
            "has_fluorine": r.get("has_fluorine", ""),
            "n_f_atoms": int(r.get("n_f_atoms", 0) or 0),
            "n_aldehyde": n_ald, "n_amine": n_am,
            "commercial_id": r.get("commercial_id", ""),
            "cas": r.get("cas", ""),
        }
        if is_ald:
            aldehydes.append(info)
        if is_am:
            amines.append(info)

    # SMILES 去重
    seen_ald, seen_am = set(), set()
    aldehydes = [a for a in aldehydes
                 if a["smiles"] not in seen_ald and not seen_ald.add(a["smiles"])]
    amines = [a for a in amines
              if a["smiles"] not in seen_am and not seen_am.add(a["smiles"])]
    return aldehydes, amines


def load_training_pairs(csv_path: str) -> set[tuple[str, str]]:
    """加载训练集配对 (canonical SMILES)。"""
    pairs = set()
    with open(csv_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pairs.add((_canon_smiles(r["aldehyde_smiles"]),
                       _canon_smiles(r["amine_smiles"])))
    return pairs


# ── 推理辅助 ─────────────────────────────────────────────

def _run_attention_pooling(model, ald_emb, amine_emb, device):
    """对一对单体执行 attention + pooling，返回 (ea, eb, e_pair)。
    ald_emb, amine_emb 为 [N_atoms, hidden_dim] 已在 device。
    """
    ald_att, amine_att = model.attention(ald_emb, amine_emb)
    ald_b = torch.zeros(ald_att.shape[0], dtype=torch.long, device=device)
    amine_b = torch.zeros(amine_att.shape[0], dtype=torch.long, device=device)
    return model.pooling(ald_att, amine_att, ald_b, amine_b, 1)


def _pair_embedding(ea, eb, e_pair, emb_3d, film_head):
    """构建 FilmHead 输入向量 (LayerNorm 后)。"""
    parts = [ea, eb, ea * eb, e_pair]
    if emb_3d is not None:
        parts.append(emb_3d)
    return film_head.norm(torch.cat(parts, dim=-1))


# ── 主流程 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="v4 单体筛选")
    parser.add_argument("--monomer-pool", type=str,
                        default="data/processed/merged_monomer_pool.csv")
    parser.add_argument("--train-csv", type=str,
                        default="data/processed/v4_train_3d_dimer.csv")
    parser.add_argument("--model", type=str,
                        default="models/v4.0_no3d/v4_model.pt")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--mc-samples", type=int, default=10)
    parser.add_argument("--device", type=str,
                        default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--output", type=str,
                        default="data/processed/v4_screening_results.csv")
    parser.add_argument("--top-output", type=str,
                        default="data/processed/v4_top20_candidates.csv")
    parser.add_argument("--use-3d", action="store_true")
    parser.add_argument("--monomer-3d-only", action="store_true")
    parser.add_argument("--max-pairs", type=int, default=0,
                        help="限制最大筛选配对 (0=全部, 用于调试)")
    parser.add_argument("--ext-threshold", type=float, default=0.90,
                        help="单体余弦相似度阈值 (外推检测, 默认0.90)")
    parser.add_argument("--ext-blend-exponent", type=float, default=2.0,
                        help="外推融合指数 (越大越保守, 默认2.0)")
    parser.add_argument("--ext-prior", type=float, default=0.80,
                        help="外推区域化学先验值 (hard 模式用, 默认 0.80)")
    parser.add_argument("--ext-bias", type=float, default=0.0,
                        help="soft 模式: 外推区域加分偏置 (默认 0.0 = 不改分)")
    parser.add_argument("--extrap-mode", type=str, default="soft",
                        choices=["soft", "hard", "none"],
                        help="外推模式: soft(只标不修, GNN 主导) / hard(改分到先验) / none(禁用)")
    parser.add_argument("--no-extrapolation", action="store_true",
                        help="(已弃用) 请用 --extrap-mode none")
    args = parser.parse_args()

    device = args.device
    logger.info(f"设备: {device}")

    # ── 1. 加载单体池 ──
    logger.info("=== 1/7 加载单体池 ===")
    aldehydes, amines = load_monomer_pool(args.monomer_pool)
    logger.info(f"  加载醛: {len(aldehydes)}, 胺: {len(amines)}")

    # ── 1.5 单体质量诊断 (不排除，仅统计) ──
    logger.info("=== 1.5/7 单体质量诊断 (软约束模式) ===")
    soft_stats = defaultdict(int)
    for m in aldehydes + amines:
        n_ald = int(m["n_aldehyde"])
        n_am = int(m["n_amine"])
        _, reasons = check_monomer_penalties(m["smiles"], n_ald, n_am)
        for r in reasons:
            soft_stats[r] += 1
    if soft_stats:
        for reason, cnt in sorted(soft_stats.items(), key=lambda x: -x[1]):
            logger.info(f"  ~{cnt} 单体: {reason}")
    logger.info(f"  醛: {len(aldehydes)}, 胺: {len(amines)} (全部保留，软约束)")

    # ── 2. 加载训练集 ──
    logger.info("=== 2/7 加载训练集 ===")
    train_pairs = load_training_pairs(args.train_csv)
    with open(args.train_csv, "r", encoding="utf-8") as f:
        train_rows = list(csv.DictReader(f))
    logger.info(f"  训练配对: {len(train_pairs)}, 训练样本: {len(train_rows)}")

    # ── 3. 交叉配对 + 硬过滤 ──
    logger.info("=== 3/7 交叉配对 + 硬过滤 ===")
    t0 = time.time()
    skipped_func = 0  # 单官能团被排除的单体
    skipped_topo = 0  # 拓扑不匹配但仍保留的对
    screening_pairs = []
    for ald in aldehydes:
        n_ald = int(ald["n_aldehyde"])
        if n_ald < 2:
            skipped_func += 1
            continue
        for am in amines:
            n_am = int(am["n_amine"])
            if n_am < 2:
                continue
            ald_s = _canon_smiles(ald["smiles"])
            am_s = _canon_smiles(am["smiles"])
            if (ald_s, am_s) in train_pairs:
                continue
            if ald_s == am_s:
                continue
            topo = check_topology(n_ald, n_am)
            if topo is None:
                skipped_topo += 1
            screening_pairs.append({
                "ald": ald, "amine": am, "topology": topo or "mismatch",
                "ald_smiles_canon": ald_s, "amine_smiles_canon": am_s,
                "topo_mismatch": topo is None,
            })
    n_skipped_am = sum(1 for am in amines if int(am["n_amine"]) < 2)
    logger.info(f"  筛选配对: {len(screening_pairs)} (耗时 {time.time()-t0:.1f}s)")
    logger.info(f"  排除: {skipped_func} 单醛基醛 + {n_skipped_am} 单胺基胺 (无法形成2D网络)")
    if skipped_topo > 0:
        logger.info(f"  拓扑不匹配(已保留为软约束): {skipped_topo} 对")

    if args.max_pairs > 0 and len(screening_pairs) > args.max_pairs:
        import random
        random.seed(42)
        screening_pairs = random.sample(screening_pairs, args.max_pairs)
        logger.info(f"  调试模式 — 限制为 {args.max_pairs} 对")

    # ── 4. 构建图 + 单体编码 ──
    logger.info("=== 4/7 构建图 + 单体编码 ===")
    unique_ald_smi = sorted(set(p["ald_smiles_canon"] for p in screening_pairs))
    unique_amine_smi = sorted(set(p["amine_smiles_canon"] for p in screening_pairs))
    logger.info(f"  独有醛: {len(unique_ald_smi)}, 独有胺: {len(unique_amine_smi)}")

    # 图构建 (role 0=醛, 1=胺)
    ald_graphs = {}
    for smi in unique_ald_smi:
        g = smiles_to_graph(smi, role=0)
        if g is not None:
            ald_graphs[smi] = g
    amine_graphs = {}
    for smi in unique_amine_smi:
        g = smiles_to_graph(smi, role=1)
        if g is not None:
            amine_graphs[smi] = g
    logger.info(f"  图: {len(ald_graphs)} 醛, {len(amine_graphs)} 胺")

    # ── 5. 加载模型 + 单体编码 ──
    logger.info("=== 5/7 加载模型 ===")
    ckpt = torch.load(args.model, map_location=device, weights_only=False)
    cfg = ckpt.get("config", {})
    use_3d = args.use_3d and ckpt.get("use_3d", False)
    if args.use_3d:
        cfg["model"]["use_3d"] = True

    model = V4Model(cfg).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    if use_3d and ckpt.get("scaler_3d"):
        sd = ckpt["scaler_dimer"] if ckpt.get("scaler_dimer") else None
        model.set_3d_scaler(
            monomer_mean=ckpt["scaler_3d"]["mean"],
            monomer_std=ckpt["scaler_3d"]["std"],
            dimer_mean=sd["mean"] if sd else None,
            dimer_std=sd["std"] if sd else None,
        )

    n_params = sum(p.numel() for p in model.parameters())
    logger.info(f"  参数量: {n_params:,} ({n_params/1e6:.2f}M), 3D={'ON' if use_3d else 'OFF'}")

    # 编码所有单体
    @torch.no_grad()
    def encode_batch(graphs: dict, label: str) -> dict:
        cache = {}
        for smi, g in graphs.items():
            g_d = type(g)(x=g.x.to(device), edge_index=g.edge_index.to(device),
                         edge_attr=g.edge_attr.to(device))
            cache[smi] = model.encoder.encoder(g_d)
        logger.info(f"    {label}: {len(cache)} 个")
        return cache

    t0 = time.time()
    ald_emb_cache = encode_batch(ald_graphs, "醛")
    amine_emb_cache = encode_batch(amine_graphs, "胺")
    logger.info(f"  编码耗时: {time.time()-t0:.1f}s")

    # ── 5.5 构建正样本单体参考嵌入 (外推检测) ──
    logger.info(f"=== 5.5/7 构建正样本单体参考嵌入 (mode={args.extrap_mode}) ===")
    pos_ald_ref: dict[str, np.ndarray] = {}   # canonical_smiles → monomer vector
    pos_amine_ref: dict[str, np.ndarray] = {}

    if args.extrap_mode != "none" and not args.no_extrapolation:
        # 从训练正样本收集独有单体 SMILES
        ref_ald_smiles: set[str] = set()
        ref_amine_smiles: set[str] = set()
        for r in train_rows:
            if int(r.get("is_film", 0)) != 1:
                continue
            ref_ald_smiles.add(_canon_smiles(r["aldehyde_smiles"]))
            ref_amine_smiles.add(_canon_smiles(r["amine_smiles"]))

        # 醛参考嵌入 (优先复用已编码缓存)
        for smi in ref_ald_smiles:
            emb = ald_emb_cache.get(smi)
            if emb is None:
                g = smiles_to_graph(smi, role=0)
                if g is None:
                    continue
                g_d = type(g)(x=g.x.to(device), edge_index=g.edge_index.to(device),
                             edge_attr=g.edge_attr.to(device))
                with torch.no_grad():
                    emb = model.encoder.encoder(g_d)
            pos_ald_ref[smi] = _monomer_pool(emb)

        # 胺参考嵌入
        for smi in ref_amine_smiles:
            emb = amine_emb_cache.get(smi)
            if emb is None:
                g = smiles_to_graph(smi, role=1)
                if g is None:
                    continue
                g_d = type(g)(x=g.x.to(device), edge_index=g.edge_index.to(device),
                             edge_attr=g.edge_attr.to(device))
                with torch.no_grad():
                    emb = model.encoder.encoder(g_d)
            pos_amine_ref[smi] = _monomer_pool(emb)

        # 构建 numpy 矩阵 + L2 归一化 (用于批量余弦相似度)
        _ald_ref_matrix = np.array(list(pos_ald_ref.values()))
        _amine_ref_matrix = np.array(list(pos_amine_ref.values()))
        _ald_ref_matrix = _ald_ref_matrix / (np.linalg.norm(_ald_ref_matrix, axis=1, keepdims=True) + 1e-10)
        _amine_ref_matrix = _amine_ref_matrix / (np.linalg.norm(_amine_ref_matrix, axis=1, keepdims=True) + 1e-10)
        logger.info(f"  正样本醛参考: {len(pos_ald_ref)} 个, 胺参考: {len(pos_amine_ref)} 个")
    else:
        _ald_ref_matrix = _amine_ref_matrix = None
        logger.info(f"  外推检测已禁用 (mode={args.extrap_mode})")

    # 预计算 3D 零向量嵌入 (用于 use_3d=True 的模型, 训练集和筛选集都无 3D 描述符)
    if use_3d:
        zero_3d = torch.zeros(1, 10, device=device)
        with torch.no_grad():
            zero_3d_emb = model.conformer_branch(zero_3d, zero_3d, zero_3d)  # [1, 64]
    else:
        zero_3d_emb = None

    # ── 6. 预计算训练集嵌入 ──
    logger.info("=== 6/7 预计算训练集嵌入 ===")
    train_embs_list = []
    train_labels_list = []
    t0 = time.time()
    for r in train_rows:
        ald_s = _canon_smiles(r["aldehyde_smiles"])
        amine_s = _canon_smiles(r["amine_smiles"])
        ald_g = smiles_to_graph(ald_s, role=0)
        amine_g = smiles_to_graph(amine_s, role=1)
        if ald_g is None or amine_g is None:
            continue
        ald_g = type(ald_g)(x=ald_g.x.to(device), edge_index=ald_g.edge_index.to(device),
                           edge_attr=ald_g.edge_attr.to(device))
        amine_g = type(amine_g)(x=amine_g.x.to(device), edge_index=amine_g.edge_index.to(device),
                               edge_attr=amine_g.edge_attr.to(device))
        with torch.no_grad():
            ald_emb = model.encoder.encoder(ald_g)
            amine_emb = model.encoder.encoder(amine_g)
            try:
                ea, eb, e_pair = _run_attention_pooling(model, ald_emb, amine_emb, device)
                emb_vec = _pair_embedding(ea, eb, e_pair, zero_3d_emb, model.film_head)
                train_embs_list.append(emb_vec.cpu().numpy().flatten())
            except Exception as exc:
                logger.debug(f"  train emb failed: {exc}")
                train_embs_list.append(np.zeros(576 if use_3d else 512))
        train_labels_list.append(int(r["is_film"]))
    train_embs = np.array(train_embs_list)
    train_labels = np.array(train_labels_list)
    # 过滤零向量 (图构建失败的回退)
    emb_norms_train = np.linalg.norm(train_embs, axis=1)
    valid_emb_mask = emb_norms_train > 1e-10
    n_skipped = (~valid_emb_mask).sum()
    if n_skipped > 0:
        logger.info(f"  训练嵌入中零向量: {n_skipped} (已过滤)")
    train_embs = train_embs[valid_emb_mask]
    train_labels = train_labels[valid_emb_mask]
    logger.info(f"  训练嵌入: {train_embs.shape}, 耗时 {time.time()-t0:.1f}s")

    # ── 7. 成对推理 ──
    logger.info(f"=== 推理 {len(screening_pairs)} 对 (MC={args.mc_samples}) ===")
    results = []
    t0 = time.time()
    pos_mask = train_labels == 1
    neg_mask = ~pos_mask
    has_pos = pos_mask.any() and train_embs.shape[1] > 1
    has_neg = neg_mask.any() and train_embs.shape[1] > 1

    # 预计算 3D 零向量嵌入 (用于 use_3d=True 的模型, 筛选集无 3D 描述符)
    # (zero_3d_emb 已在训练集嵌入循环前预计算)

    for pi, p in enumerate(screening_pairs):
        ald_s, amine_s = p["ald_smiles_canon"], p["amine_smiles_canon"]
        ald_emb = ald_emb_cache.get(ald_s)
        amine_emb = amine_emb_cache.get(amine_s)
        if ald_emb is None or amine_emb is None:
            continue

        # MC Dropout 推理 (全模型 dropout 激活)
        model.enable_mc_dropout()
        mc_probs = []
        for _ in range(args.mc_samples):
            ea, eb, e_pair = _run_attention_pooling(model, ald_emb, amine_emb, device)
            logit = model.film_head(ea, eb, e_pair, zero_3d_emb)
            mc_probs.append(torch.sigmoid(logit).item())
        model.eval()
        prob_mean = float(np.mean(mc_probs))
        prob_std = float(np.std(mc_probs))

        # 嵌入 (eval 模式)
        model.eval()
        with torch.no_grad():
            ea, eb, e_pair = _run_attention_pooling(model, ald_emb, amine_emb, device)
            emb_vec = _pair_embedding(ea, eb, e_pair, zero_3d_emb, model.film_head)
        emb_np = emb_vec.cpu().numpy().flatten()

        # 近邻距离
        if has_pos and has_neg:
            dists_pos = cosine_distances(emb_np.reshape(1, -1), train_embs[pos_mask])[0]
            dists_neg = cosine_distances(emb_np.reshape(1, -1), train_embs[neg_mask])[0]
            nn_dist_pos = float(np.min(dists_pos))
            nn_dist_neg = float(np.min(dists_neg))
            nn_dist = min(nn_dist_pos, nn_dist_neg)
        else:
            nn_dist = nn_dist_pos = nn_dist_neg = -1.0

        # ── 单体级近邻相似度 + 外推检测 ──
        ald_max_sim = amine_max_sim = monomer_min_sim = -1.0
        is_extrapolation = False
        prob_extrapolated = prob_mean  # 默认不调整

        if args.extrap_mode != "none" and not args.no_extrapolation and _ald_ref_matrix is not None:
            # Mean-pool 单体嵌入 → L2 归一化
            ald_vec = _monomer_pool(ald_emb)
            amine_vec = _monomer_pool(amine_emb)
            ald_vec = ald_vec / (np.linalg.norm(ald_vec) + 1e-10)
            amine_vec = amine_vec / (np.linalg.norm(amine_vec) + 1e-10)

            # 与正样本参考集余弦相似度
            ald_sims = ald_vec @ _ald_ref_matrix.T
            amine_sims = amine_vec @ _amine_ref_matrix.T
            ald_max_sim = float(np.max(ald_sims)) if len(ald_sims) > 0 else -1.0
            amine_max_sim = float(np.max(amine_sims)) if len(amine_sims) > 0 else -1.0
            monomer_min_sim = min(ald_max_sim, amine_max_sim)

            # 外推检测: 两个单体都像正样本，但 GNN 给出异常低分
            if (monomer_min_sim > args.ext_threshold
                    and prob_mean < args.ext_blend_exponent * 0.3):
                is_extrapolation = True
                if args.extrap_mode == "hard":
                    # 硬外推 (原 Plan A): 融合化学先验
                    blend_raw = (monomer_min_sim - args.ext_threshold) / (1.0 - args.ext_threshold)
                    blend_weight = blend_raw ** args.ext_blend_exponent
                    blend_weight = min(blend_weight, 1.0)
                    prob_extrapolated = blend_weight * args.ext_prior + (1.0 - blend_weight) * prob_mean
                else:
                    # 软外推 (新默认): GNN 主导, 不改 prob_mean
                    # 偏置仅在 final_score 阶段通过 ext_bias 应用
                    prob_extrapolated = prob_mean

        results.append({
            "aldehyde_smiles": ald_s,
            "amine_smiles": amine_s,
            "aldehyde_name": p["ald"]["best_name"],
            "amine_name": p["amine"]["best_name"],
            "topology": p["topology"],
            "topo_mismatch": p.get("topo_mismatch", False),
            "n_aldehyde": p["ald"]["n_aldehyde"],
            "n_amine": p["amine"]["n_amine"],
            "film_prob_mean": prob_mean,
            "film_prob_std": prob_std,
            "ald_has_f": p["ald"]["has_fluorine"],
            "amine_has_f": p["amine"]["has_fluorine"],
            "ald_source": p["ald"]["source"],
            "amine_source": p["amine"]["source"],
            "ald_commercial_id": p["ald"]["commercial_id"],
            "amine_commercial_id": p["amine"]["commercial_id"],
            "nn_dist": nn_dist,
            "nn_dist_pos": nn_dist_pos,
            "nn_dist_neg": nn_dist_neg,
            "ald_max_sim": ald_max_sim,
            "amine_max_sim": amine_max_sim,
            "monomer_min_sim": monomer_min_sim,
            "is_extrapolation": is_extrapolation,
            "prob_extrapolated": prob_extrapolated,
            "emb": emb_np,
        })

        if (pi + 1) % 5000 == 0:
            elapsed = time.time() - t0
            rate = (pi + 1) / elapsed
            eta = (len(screening_pairs) - pi - 1) / rate
            logger.info(f"  {pi+1}/{len(screening_pairs)} "
                        f"({100*(pi+1)/len(screening_pairs):.0f}%) "
                        f"速率 {rate:.0f}对/s ETA {eta:.0f}s")

    if not results:
        logger.error("无有效筛选结果！")
        return

    # ── 软约束 + 排序 ──
    logger.info("=== 7/7 软约束 + 排序 ===")
    # 添加杂环标记和调整分数
    for r in results:
        ald_mol = Chem.MolFromSmiles(r["aldehyde_smiles"])
        am_mol = Chem.MolFromSmiles(r["amine_smiles"])
        r["ald_has_hetero"] = str(has_heterocycle(ald_mol)) if ald_mol else "False"
        r["amine_has_hetero"] = str(has_heterocycle(am_mol)) if am_mol else "False"
        # 软约束调整 (返回 (adjusted, reasons))
        ald_info = {"smiles": r["aldehyde_smiles"], "n_aldehyde": int(r["n_aldehyde"]),
                     "n_amine": 0, "topo_mismatch": r.get("topo_mismatch", False)}
        amine_info = {"smiles": r["amine_smiles"], "n_aldehyde": 0,
                       "n_amine": int(r["n_amine"]),
                       "topo_mismatch": r.get("topo_mismatch", False)}
        adj_prob, pair_reasons = check_pair_soft_constraints(
            ald_info, amine_info, r["prob_extrapolated"])
        # 柔性软降权
        fl_factor, fl_reason, ra, rm, rt = _flexibility_penalty(
            r["aldehyde_smiles"], r["amine_smiles"])
        # 软外推偏置: 在排序分上给外推区域微小加分 (GNN 仍占主导)
        if r.get("is_extrapolation") and args.extrap_mode == "soft" and args.ext_bias > 0:
            adj_prob = min(adj_prob + args.ext_bias, 0.999)
        r["film_prob_adjusted"] = min(round(adj_prob * fl_factor, 6), 1.0)
        r["ald_aromatic_rings"] = ra
        r["amine_aromatic_rings"] = rm
        r["total_aromatic_rings"] = rt
        r["flexibility_reason"] = fl_reason
        # 汇总软约束原因
        all_reasons = pair_reasons + [fl_reason]
        r["penalty_reasons"] = " | ".join(all_reasons)

    scores_adj = np.array([r["film_prob_adjusted"] for r in results])
    scores_raw = np.array([r["film_prob_mean"] for r in results])
    emb_matrix = np.array([r["emb"] for r in results])

    # L2 归一化
    norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    norms = np.where(norms > 1e-10, norms, 1.0)
    emb_matrix = emb_matrix / norms

    # 按调整分数排名
    rank_order = np.argsort(scores_adj)[::-1]
    for i, idx in enumerate(rank_order):
        results[idx]["score_rank"] = i + 1
        results[idx]["score_percentile"] = round(100 * (1 - i / len(scores_adj)), 2)

    # ── Top 40 纯分数排序 + 芳环>=4 + 单体最多3次 ──
    top_k = min(args.top_k, 40)
    eligible = [(i, scores_adj[i]) for i in range(len(results))
                if results[i].get("total_aromatic_rings", 0) >= 4]
    eligible.sort(key=lambda x: -x[1])
    diverse_idx = []
    ald_count: dict[str, int] = {}
    amine_count: dict[str, int] = {}
    for i, _ in eligible:
        if len(diverse_idx) >= top_k:
            break
        ald_smi = results[i]["aldehyde_smiles"]
        am_smi = results[i]["amine_smiles"]
        if ald_count.get(ald_smi, 0) >= 3 or amine_count.get(am_smi, 0) >= 3:
            continue
        diverse_idx.append(i)
        ald_count[ald_smi] = ald_count.get(ald_smi, 0) + 1
        amine_count[am_smi] = amine_count.get(am_smi, 0) + 1
    for rank, idx in enumerate(diverse_idx):
        results[idx]["diverse_rank"] = rank + 1
    used = set(diverse_idx)
    # ── 保存 ──
    save_cols = [k for k in results[0].keys() if k != "emb"]
    if "diverse_rank" not in save_cols and any("diverse_rank" in r for r in results):
        save_cols.append("diverse_rank")
    for r in results:
        r.pop("emb", None)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=save_cols, extrasaction="ignore")
        writer.writeheader()
        for idx in rank_order:
            writer.writerow(results[idx])
    logger.info(f"全量结果: {args.output} ({len(results)} 条)")

    top_results = [results[i] for i in diverse_idx]
    with open(args.top_output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=save_cols, extrasaction="ignore")
        writer.writeheader()
        for r in sorted(top_results, key=lambda r: r.get("diverse_rank", 999)):
            writer.writerow(r)
    logger.info(f"Top {len(top_results)} 候选: {args.top_output}")

    # ── 摘要 ──
    f_cnt = sum(1 for r in results
                if str(r["ald_has_f"]).lower() == "true"
                or str(r["amine_has_f"]).lower() == "true")
    hex_c = sum(1 for r in results if r["topology"] == "hexagonal")
    tet_c = sum(1 for r in results if r["topology"] == "tetragonal")
    nn_mean = np.mean([r["nn_dist"] for r in results if r["nn_dist"] > 0])
    top_hex = sum(1 for r in top_results if r["topology"] == "hexagonal")
    top_c3ald = sum(1 for r in top_results
                    if r["topology"] == "hexagonal" and int(r["n_aldehyde"]) >= 3)
    top_c3am = sum(1 for r in top_results
                   if r["topology"] == "hexagonal" and int(r["n_amine"]) >= 3)

    logger.info("=" * 60)
    logger.info(f"总筛选: {len(screening_pairs)} → 有效: {len(results)}")
    logger.info(f"原始分数: median={np.median(scores_raw):.4f} "
                f"mean±std={np.mean(scores_raw):.4f}±{np.std(scores_raw):.4f} "
                f"min={scores_raw.min():.4f} max={scores_raw.max():.4f}")
    logger.info(f"调整分数: median={np.median(scores_adj):.4f} "
                f"mean±std={np.mean(scores_adj):.4f}±{np.std(scores_adj):.4f}")
    logger.info(f"含氟: {f_cnt} ({100*f_cnt/max(1,len(results)):.1f}%)")
    logger.info(f"六方/四方: {hex_c}/{tet_c}")
    logger.info(f"平均 NN 距离: {nn_mean:.4f}")
    n_extrap = sum(1 for r in results if r.get("is_extrapolation"))
    if n_extrap > 0:
        mode_label = {"soft": "soft(只标不修)", "hard": "hard(改分到先验)", "none": "none"}
        logger.info(f"外推风险对: {n_extrap} ({100*n_extrap/max(1,len(results)):.1f}%) mode={mode_label.get(args.extrap_mode, args.extrap_mode)}")
    logger.info(f"")
    logger.info(f"Top {len(top_results)} — 六方: {top_hex}, C3醛: {top_c3ald}, C3胺: {top_c3am}")
    for r in sorted(top_results, key=lambda r: r.get("diverse_rank", 999)):
        ald_n = r["aldehyde_name"][:30]
        am_n = r["amine_name"][:35]
        fl = r.get("flexibility_reason", "?")
        rt = r.get("total_aromatic_rings", 0)
        ext_flag = " [!EXT]" if r.get("is_extrapolation") else ""
        logger.info(
            f"  #{r['diverse_rank']:2d} μ={r['film_prob_mean']:.4f} "
            f"adj={r['film_prob_adjusted']:.4f} "
            f"σ={r['film_prob_std']:.3f} nn={r['nn_dist']:.3f} "
            f"H={r.get('ald_has_hetero','?')}/{r.get('amine_has_hetero','?')} "
            f"{r['topology'][:4]} C{r['n_aldehyde']}+C{r['n_amine']} "
            f"rings=t{rt}({fl[:14]}) sim={r.get('monomer_min_sim', -1):.3f}"
            f"{ext_flag} "
            f"{ald_n} × {am_n}"
        )

    total_time = time.time() - t0
    logger.info(f"\n总耗时: {total_time:.0f}s ({total_time/60:.1f}min)")


if __name__ == "__main__":
    main()
