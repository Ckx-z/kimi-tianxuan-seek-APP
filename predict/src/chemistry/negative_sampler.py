"""合成负样本生成器 — 通过单体替换将化学先验注入训练分布。

每条正样本生成 1–2 个合成负变体:
  策略 A: 对称→不对称替换 (规则 #2)
  策略 B: 常环→极端多环(>8)替换 (规则 #1)
  策略 C: 低取代→过取代替换 (规则 #6)

不修改模型架构或损失函数——化学知识通过数据分布表达。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
import random

import numpy as np
from rdkit import Chem
from rdkit.Chem import Descriptors, CanonicalRankAtoms, AllChem

_ALD_SMARTS = Chem.MolFromSmarts("[CX3H1](=O)[#6]")
_AM_SMARTS = Chem.MolFromSmarts("[NH2][c]")
_BENZENE_SMARTS = Chem.MolFromSmarts("c1ccccc1")
_HALOGENS = {9, 17, 35, 53}

random.seed(42)
np.random.seed(42)


# ── 单体属性计算 ──

@dataclass
class MonomerInfo:
    """单体化学属性快照，用于替换池索引和相似度匹配。"""
    smiles: str
    canonical_smiles: str
    monomer_type: str            # aldehyde / amine
    topology: str                 # C2 / C3 / C4
    n_rings: int
    is_symmetric: bool
    n_ald: int
    n_am: int
    has_fluorine: bool
    max_sub_per_ring: int         # 所有苯环中最大取代基数
    max_nonhalo_extra: int        # 所有苯环中最大非卤素多余取代基数
    is_para: bool                 # C2 单体是否对位 (非C2=True)
    mw: float
    source: str = "pool"


def _topology(n_ald: int, n_am: int) -> str:
    if n_ald >= 3 or n_am >= 3:
        return "C3"
    if n_ald >= 2 or n_am >= 2:
        return "C2"
    if n_ald >= 4 or n_am >= 4:
        return "C4"
    return "C1"


def _count_aromatic_rings(mol: Chem.Mol) -> int:
    return Descriptors.NumAromaticRings(mol)


def _check_symmetry(mol: Chem.Mol, n_ald: int, n_am: int) -> bool:
    """CanonicalRankAtoms 全局对称感知。"""
    if n_ald >= 2:
        matches = mol.GetSubstructMatches(_ALD_SMARTS)
        reactive = [m[0] for m in matches]
    elif n_am >= 2:
        matches = mol.GetSubstructMatches(_AM_SMARTS)
        reactive = [m[0] for m in matches]
    else:
        return False
    if len(reactive) < 2:
        return False
    ranks = CanonicalRankAtoms(mol, breakTies=False)
    reactive_ranks = [ranks[a] for a in reactive]
    return all(r == reactive_ranks[0] for r in reactive_ranks[1:])


def _check_para(mol: Chem.Mol, n_ald: int, n_am: int, topo: str) -> bool:
    """C2 单体两反应基团是否在同一苯环对位。"""
    if topo != "C2":
        return True
    if n_ald >= 2:
        matches = mol.GetSubstructMatches(_ALD_SMARTS)
        reactive_atoms = [m[2] for m in matches]
    elif n_am >= 2:
        matches = mol.GetSubstructMatches(_AM_SMARTS)
        reactive_atoms = [m[1] for m in matches]
    else:
        return False
    if len(reactive_atoms) < 2:
        return False

    rings = mol.GetSubstructMatches(_BENZENE_SMARTS)
    for ring in rings:
        ring_set = set(ring)
        on_ring = [a for a in reactive_atoms if a in ring_set]
        if len(on_ring) < 2:
            continue
        for i in range(len(on_ring)):
            for j in range(i + 1, len(on_ring)):
                path = Chem.GetShortestPath(mol, on_ring[i], on_ring[j])
                ring_bonds = sum(
                    1 for k in range(len(path) - 1)
                    if path[k] in ring_set and path[k + 1] in ring_set
                )
                if ring_bonds == 3:
                    return True
                elif ring_bonds in (1, 2):
                    return False
    return True


def _substituent_stats(mol: Chem.Mol) -> Tuple[int, int]:
    """返回 (max_sub_per_ring, max_nonhalo_extra) 各苯环取最大。"""
    rings = mol.GetSubstructMatches(_BENZENE_SMARTS)
    max_sub, max_nonhalo = 0, 0
    for ring in rings:
        ring_set = set(ring)
        n_sub, n_nonhalo = 0, 0
        for aidx in ring:
            atom = mol.GetAtomWithIdx(aidx)
            for nbr in atom.GetNeighbors():
                if nbr.GetIdx() not in ring_set:
                    n_sub += 1
                    an = nbr.GetAtomicNum()
                    if an not in _HALOGENS and an != 1:
                        n_nonhalo += 1
        max_sub = max(max_sub, n_sub)
        max_nonhalo = max(max_nonhalo, n_nonhalo)
    return max_sub, max_nonhalo


def compute_monomer_info(smi: str, monomer_type: str = "unknown",
                         source: str = "pool") -> Optional[MonomerInfo]:
    """从 SMILES 计算单体的全部筛选属性。"""
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return None
    can = Chem.MolToSmiles(mol, canonical=True)
    n_ald = len(mol.GetSubstructMatches(_ALD_SMARTS, uniquify=True))
    n_am = len(mol.GetSubstructMatches(_AM_SMARTS, uniquify=True))
    topo = _topology(n_ald, n_am)
    n_rings = _count_aromatic_rings(mol)
    symmetric = _check_symmetry(mol, n_ald, n_am)
    is_para = _check_para(mol, n_ald, n_am, topo)
    max_sub, max_nonhalo = _substituent_stats(mol)
    has_f = bool(mol.HasSubstructMatch(Chem.MolFromSmarts("[F]")))
    mw = Descriptors.MolWt(mol)
    return MonomerInfo(
        smiles=smi, canonical_smiles=can, monomer_type=monomer_type,
        topology=topo, n_rings=n_rings, is_symmetric=symmetric,
        n_ald=n_ald, n_am=n_am, has_fluorine=has_f,
        max_sub_per_ring=max_sub, max_nonhalo_extra=max_nonhalo,
        is_para=is_para, mw=mw, source=source,
    )


# ── 替换池 ──

@dataclass
class ReplacementPool:
    """按拓扑和环数分桶的单体索引，支持快速查找违规变体。"""
    # topology → ring_bucket → List[MonomerInfo]
    by_topo_rings: Dict[str, Dict[str, List[MonomerInfo]]] = field(default_factory=dict)
    # topology → symmetry → List[MonomerInfo]
    by_topo_sym: Dict[str, Dict[bool, List[MonomerInfo]]] = field(default_factory=dict)
    all_monomers: List[MonomerInfo] = field(default_factory=list)
    # canonical_smiles → MonomerInfo
    lookup: Dict[str, MonomerInfo] = field(default_factory=dict)

    def add(self, info: MonomerInfo) -> None:
        self.all_monomers.append(info)
        self.lookup[info.canonical_smiles] = info

        topo = info.topology
        self.by_topo_rings.setdefault(topo, {})

        if info.n_rings <= 4:
            bucket = "low"
        elif info.n_rings <= 8:
            bucket = "mid"
        else:
            bucket = "high"
        self.by_topo_rings[topo].setdefault(bucket, []).append(info)

        self.by_topo_sym.setdefault(topo, {})
        self.by_topo_sym[topo].setdefault(info.is_symmetric, []).append(info)

    def find_asymmetric_variant(self, info: MonomerInfo) -> Optional[MonomerInfo]:
        """找同topo、同环数桶的不对称变体。"""
        if not info.is_symmetric:
            return None
        topo = info.topology
        asym_pool = self.by_topo_sym.get(topo, {}).get(False, [])
        if not asym_pool:
            return None
        bucket = _ring_bucket(info.n_rings)
        same_bucket = [m for m in asym_pool
                       if _ring_bucket(m.n_rings) == bucket
                       and m.canonical_smiles != info.canonical_smiles]
        if same_bucket:
            return _pick_closest_mw(same_bucket, info.mw)
        return _pick_closest_mw(asym_pool, info.mw)

    def find_multiring_variant(self, info: MonomerInfo,
                                max_rings: int = 12) -> Optional[MonomerInfo]:
        """找同topo、环数「刚好超标」(9–max_rings) 的变体。

        仅在极端多环 (>8) 时标记为不利成膜，匹配 COF 化学实际：
        苯桥/炔键/亚胺键连接 3–5 环为常规结构。
        """
        topo = info.topology
        if info.n_rings > 8:
            return None
        candidates = [m for m in self.all_monomers
                      if m.topology == topo
                      and 9 <= m.n_rings <= max_rings
                      and m.monomer_type == info.monomer_type
                      and m.canonical_smiles != info.canonical_smiles]
        if not candidates:
            return None
        return _pick_closest_mw(candidates, info.mw)

    def find_oversubstituted_variant(self, info: MonomerInfo,
                                       max_excess: int = 2) -> Optional[MonomerInfo]:
        """找同topo、非卤素多余取代基数「刚好超标」(1–max_excess) 的变体。

        仅找轻微过度取代的变体，避免替换为满取代单体 (4+ OMe/OH)。
        """
        candidates = [m for m in self.all_monomers
                      if m.topology == info.topology
                      and 1 <= m.max_nonhalo_extra <= max_excess
                      and m.canonical_smiles != info.canonical_smiles
                      and m.monomer_type == info.monomer_type]
        if not candidates:
            return None
        return _pick_closest_mw(candidates, info.mw)

    def find_nonpara_c2_variant(self, info: MonomerInfo) -> Optional[MonomerInfo]:
        """找同topo=C2、非对位的变体。"""
        if info.topology != "C2" or not info.is_para:
            return None
        candidates = [m for m in self.all_monomers
                      if m.topology == "C2"
                      and not m.is_para
                      and m.monomer_type == info.monomer_type
                      and m.canonical_smiles != info.canonical_smiles]
        if not candidates:
            return None
        return _pick_closest_mw(candidates, info.mw)


def _ring_bucket(n: int) -> str:
    if n <= 4:
        return "low"
    if n <= 8:
        return "mid"
    return "high"


def _pick_closest_mw(candidates: List[MonomerInfo], target_mw: float) -> MonomerInfo:
    """选分子量最接近的变体，以最小化除目标属性外的结构差异。"""
    return min(candidates, key=lambda m: abs(m.mw - target_mw))


# ── 合成负样本生成 ──

@dataclass
class SyntheticPair:
    ald_smiles: str
    am_smiles: str
    ald_info: MonomerInfo
    am_info: MonomerInfo
    strategy: str               # asymmetry / multiring / oversub / nonpara
    replaced: str               # aldehyde / amine
    label: int = 0


def build_replacement_pool(monomer_pool_path: str,
                           extra_smiles: Optional[List[str]] = None) -> ReplacementPool:
    """从单体池构建替换索引。"""
    import pandas as pd
    pool = ReplacementPool()
    df = pd.read_csv(monomer_pool_path)
    seen = set()
    for _, row in df.iterrows():
        smi = str(row["smiles"])
        mtype = str(row.get("monomer_type", "unknown"))
        if mtype not in ("aldehyde", "amine"):
            continue
        info = compute_monomer_info(smi, monomer_type=mtype, source="pool")
        if info is None:
            continue
        if info.canonical_smiles in seen:
            continue
        seen.add(info.canonical_smiles)
        # 排除自身无法进入 2D 筛选的单体 (无苯环)
        if info.n_rings == 0:
            continue
        pool.add(info)

    if extra_smiles:
        for smi in extra_smiles:
            smi = str(smi)
            info = compute_monomer_info(smi, monomer_type="unknown", source="extra")
            if info is None or info.canonical_smiles in seen:
                continue
            seen.add(info.canonical_smiles)
            if info.n_rings == 0:
                continue
            pool.add(info)

    return pool


def generate_synthetic_pairs(
    positive_pairs: List[Tuple[str, str, str, str]],
    # (ald_smiles, am_smiles, ald_type, am_type)  type = aldehyde/amine
    pool: ReplacementPool,
    strategies: Tuple[str, ...] = ("asymmetry", "multiring", "oversub"),
    max_per_pair: int = 1,
    multiring_max_rings: int = 12,
    oversub_max_excess: int = 2,
) -> List[SyntheticPair]:
    """对每条正样本生成 1 个合成负变体 (默认), 仅选「刚好过线」的变体。

    Args:
        positive_pairs: 训练集中的正样本配对。
        pool: 单体替换池。
        strategies: 启用的合成策略。
        max_per_pair: 每条正样本最多生成几个负变体 (默认 1)。
        multiring_max_rings: 多环替换上限, 仅选 9–max_rings 环 (默认 12)。
        oversub_max_excess: 过取代替换上界 (默认 2)。
    """
    results: List[SyntheticPair] = []
    strategy_weights = {"asymmetry": 3, "multiring": 2, "oversub": 2, "nonpara": 1}

    for ald_smi, am_smi, ald_type, am_type in positive_pairs:
        ald_info = pool.lookup.get(ald_smi) or compute_monomer_info(
            ald_smi, monomer_type=ald_type, source="train")
        am_info = pool.lookup.get(am_smi) or compute_monomer_info(
            am_smi, monomer_type=am_type, source="train")
        if ald_info is None or am_info is None:
            continue

        candidates: List[Tuple[float, SyntheticPair]] = []

        # 策略 A: 不对称替换
        if "asymmetry" in strategies:
            if ald_info.is_symmetric and ald_info.topology in ("C2", "C3"):
                variant = pool.find_asymmetric_variant(ald_info)
                if variant and variant.canonical_smiles != am_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=variant.canonical_smiles,
                        am_smiles=am_info.canonical_smiles,
                        ald_info=variant, am_info=am_info,
                        strategy="asymmetry", replaced="aldehyde",
                    )
                    candidates.append((strategy_weights["asymmetry"], pair))

            if am_info.is_symmetric and am_info.topology in ("C2", "C3"):
                variant = pool.find_asymmetric_variant(am_info)
                if variant and variant.canonical_smiles != ald_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=ald_info.canonical_smiles,
                        am_smiles=variant.canonical_smiles,
                        ald_info=ald_info, am_info=variant,
                        strategy="asymmetry", replaced="amine",
                    )
                    candidates.append((strategy_weights["asymmetry"], pair))

        # 策略 B: 多环替换
        if "multiring" in strategies:
            if ald_info.n_rings <= 8 and ald_info.topology != "C1":
                variant = pool.find_multiring_variant(ald_info, max_rings=multiring_max_rings)
                if variant and variant.canonical_smiles != am_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=variant.canonical_smiles,
                        am_smiles=am_info.canonical_smiles,
                        ald_info=variant, am_info=am_info,
                        strategy="multiring", replaced="aldehyde",
                    )
                    candidates.append((strategy_weights["multiring"], pair))

            if am_info.n_rings <= 8 and am_info.topology != "C1":
                variant = pool.find_multiring_variant(am_info, max_rings=multiring_max_rings)
                if variant and variant.canonical_smiles != ald_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=ald_info.canonical_smiles,
                        am_smiles=variant.canonical_smiles,
                        ald_info=ald_info, am_info=variant,
                        strategy="multiring", replaced="amine",
                    )
                    candidates.append((strategy_weights["multiring"], pair))

        # 策略 C: 过取代替换 (规则 #6 — 非卤素取代 >4)
        if "oversub" in strategies:
            if ald_info.max_nonhalo_extra == 0:
                variant = pool.find_oversubstituted_variant(ald_info, max_excess=oversub_max_excess)
                if variant and variant.canonical_smiles != am_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=variant.canonical_smiles,
                        am_smiles=am_info.canonical_smiles,
                        ald_info=variant, am_info=am_info,
                        strategy="oversub", replaced="aldehyde",
                    )
                    candidates.append((strategy_weights["oversub"], pair))

            if am_info.max_nonhalo_extra == 0:
                variant = pool.find_oversubstituted_variant(am_info, max_excess=oversub_max_excess)
                if variant and variant.canonical_smiles != ald_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=ald_info.canonical_smiles,
                        am_smiles=variant.canonical_smiles,
                        ald_info=ald_info, am_info=variant,
                        strategy="oversub", replaced="amine",
                    )
                    candidates.append((strategy_weights["oversub"], pair))

        # 策略 D: C2 非对位替换
        if "nonpara" in strategies:
            if ald_info.is_para and ald_info.topology == "C2":
                variant = pool.find_nonpara_c2_variant(ald_info)
                if variant and variant.canonical_smiles != am_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=variant.canonical_smiles,
                        am_smiles=am_info.canonical_smiles,
                        ald_info=variant, am_info=am_info,
                        strategy="nonpara", replaced="aldehyde",
                    )
                    candidates.append((strategy_weights["nonpara"], pair))

            if am_info.is_para and am_info.topology == "C2":
                variant = pool.find_nonpara_c2_variant(am_info)
                if variant and variant.canonical_smiles != ald_info.canonical_smiles:
                    pair = SyntheticPair(
                        ald_smiles=ald_info.canonical_smiles,
                        am_smiles=variant.canonical_smiles,
                        ald_info=ald_info, am_info=variant,
                        strategy="nonpara", replaced="amine",
                    )
                    candidates.append((strategy_weights["nonpara"], pair))

        if not candidates:
            continue

        # 加权随机选 1-2 个变体
        strategies_chosen = _weighted_sample(candidates, k=min(max_per_pair, len(candidates)))
        results.extend(pair for _, pair in strategies_chosen)

    return results


def _weighted_sample(candidates: List[Tuple[float, SyntheticPair]],
                     k: int) -> List[Tuple[float, SyntheticPair]]:
    """加权不放回采样。"""
    if len(candidates) <= k:
        return candidates
    chosen = []
    remaining = list(candidates)
    for _ in range(k):
        total_w = sum(w for w, _ in remaining)
        r = random.random() * total_w
        acc = 0.0
        for i, (w, pair) in enumerate(remaining):
            acc += w
            if acc >= r:
                chosen.append((w, pair))
                remaining.pop(i)
                break
    return chosen
