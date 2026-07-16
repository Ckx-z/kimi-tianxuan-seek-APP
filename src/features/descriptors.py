"""统一描述符接口。

整合旧项目 src/features/chemistry/ 中的描述符计算，输出标准化、归一化的特征向量，
避免"小单体相似度排序"退化问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors

# 确保能引用 chemistry 子包（旧项目代码副本）
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from chemistry import cache_3d, hard_rules, interaction, linker_analyzer


def _get_reactive_sites(smiles: str, role: str = "aldehyde") -> int:
    """粗略计算单体反应位点数（醛基或伯胺数）。"""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 1
    try:
        if role == "aldehyde":
            # 醛基 C=O
            patt = Chem.MolFromSmarts("[CX3](=O)")
            return len(mol.GetSubstructMatches(patt)) if patt else 1
        else:
            # 伯胺 -NH2
            patt = Chem.MolFromSmarts("[NX3;H2]")
            return len(mol.GetSubstructMatches(patt)) if patt else 1
    except Exception:
        return 1


def _safe_ratio(numerator: float, denominator: float) -> float:
    """安全除法，返回比例。"""
    if denominator == 0 or pd.isna(denominator):
        return 0.0
    return numerator / denominator


def compute_single_monomer_features(smiles: str, role: str = "aldehyde") -> dict:
    """计算单个单体的特征字典。

    Args:
        smiles: 单体 SMILES
        role: "aldehyde" 或 "amine"

    Returns:
        包含 RDKit 基本描述符 + linker_analyzer 描述符 + 归一化特征的字典
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {}

    # 1. RDKit 基本描述符（绝对量）
    mw = Descriptors.MolWt(mol)
    n_atoms = mol.GetNumAtoms()
    n_heavy = mol.GetNumHeavyAtoms()
    n_rotatable = Descriptors.NumRotatableBonds(mol)
    n_aromatic_rings = rdMolDescriptors.CalcNumAromaticRings(mol)
    n_rings = rdMolDescriptors.CalcNumRings(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    logp = Descriptors.MolLogP(mol)

    # 2. linker_analyzer 描述符
    try:
        linker_desc = linker_analyzer.compute_monomer_descriptors(mol)
    except Exception:
        linker_desc = {}

    # 3. 反应位点数
    n_sites = _get_reactive_sites(smiles, role)

    features = {
        "mw": mw,
        "n_atoms": n_atoms,
        "n_heavy": n_heavy,
        "n_rotatable": n_rotatable,
        "n_aromatic_rings": n_aromatic_rings,
        "n_rings": n_rings,
        "tpsa": tpsa,
        "logp": logp,
        "n_reactive_sites": n_sites,
    }
    features.update(linker_desc)

    # 4. 归一化特征（防止相似度排序退化）
    features["mw_per_site"] = _safe_ratio(mw, n_sites)
    features["n_atoms_per_site"] = _safe_ratio(n_atoms, n_sites)
    features["n_heavy_per_site"] = _safe_ratio(n_heavy, n_sites)
    features["n_aromatic_rings_per_site"] = _safe_ratio(n_aromatic_rings, n_sites)
    features["n_rings_per_site"] = _safe_ratio(n_rings, n_sites)
    features["tpsa_per_site"] = _safe_ratio(tpsa, n_sites)

    return features


def compute_pair_features(ald_smiles: str, amine_smiles: str,
                            use_rules: bool = True,
                            reduced_rules: bool = False,
                            use_interaction: bool = True,
                            use_3d: bool = False,
                            use_dimer: bool = False,
                            n_confs: int = 5,
                            seed: int = 42,
                            monomer_cache_path: Optional[Path] = None,
                            dimer_cache_path: Optional[Path] = None) -> dict:
    """计算一对醛+胺的组合特征。

    Args:
        ald_smiles: 醛 SMILES
        amine_smiles: 胺 SMILES
        use_rules: 是否拼接规则特征
        reduced_rules: 是否使用精简规则向量（仅核心违反 + F/CF3）
        use_interaction: 是否拼接醛-胺 Hadamard 交互特征
        use_3d: 是否拼接单体 3D 描述符（醛 + 胺各 10 维）
        use_dimer: 是否拼接二聚体 3D 描述符（10 维）
        n_confs: 3D 构象生成数量
        seed: 3D 构象随机种子
        monomer_cache_path: 单体 3D 缓存路径，默认由 cache_3d 决定
        dimer_cache_path: 二聚体 3D 缓存路径，默认由 cache_3d 决定

    重点：使用比例型和每反应位点归一化特征，避免退化为相似度排序。
    """
    ald = compute_single_monomer_features(ald_smiles, role="aldehyde")
    amine = compute_single_monomer_features(amine_smiles, role="amine")

    if not ald or not amine:
        return {}

    # 1. 规则向量 —— 来自旧项目 hard_rules.py
    rule_vec = {}
    if use_rules:
        try:
            rule_vec = hard_rules.get_rule_vector(ald_smiles, amine_smiles, reduced=reduced_rules)
        except Exception:
            rule_vec = {}

    # 2. 组合描述符
    pair_features = {
        # 拓扑匹配（反应位点比）
        "site_ratio": _safe_ratio(ald.get("n_reactive_sites", 1), amine.get("n_reactive_sites", 1)),
        # 大小匹配
        "mw_ratio": _safe_ratio(ald.get("mw", 1), amine.get("mw", 1)),
        "ring_ratio": _safe_ratio(ald.get("n_rings", 1), amine.get("n_rings", 1)),
        "aromatic_ring_ratio": _safe_ratio(ald.get("n_aromatic_rings", 1), amine.get("n_aromatic_rings", 1)),
        # 对称性/刚性差异
        "rotatable_diff": abs(ald.get("n_rotatable", 0) - amine.get("n_rotatable", 0)),
        "logp_diff": abs(ald.get("logp", 0) - amine.get("logp", 0)),
        "tpsa_diff": abs(ald.get("tpsa", 0) - amine.get("tpsa", 0)),
    }

    # 3. 醛-胺 Hadamard 交互特征
    interact_features = {}
    if use_interaction:
        try:
            interact_features = interaction.compute_interaction_features(ald, amine)
        except Exception:
            interact_features = {}

    # 4. 拼接所有特征
    features = {}
    for k, v in ald.items():
        features[f"ald_{k}"] = v
    for k, v in amine.items():
        features[f"amine_{k}"] = v
    for k, v in pair_features.items():
        features[f"pair_{k}"] = v

    # 规则向量可能是 list 或 dict
    if isinstance(rule_vec, dict):
        for k, v in rule_vec.items():
            features[f"rule_{k}"] = v
    elif isinstance(rule_vec, (list, tuple)):
        for i, v in enumerate(rule_vec):
            features[f"rule_{i}"] = v

    for k, v in interact_features.items():
        features[f"int_{k}"] = v

    # 5. 单体 3D 描述符
    if use_3d:
        try:
            ald_3d = cache_3d.get_monomer_3d(
                ald_smiles, n_confs=n_confs, seed=seed, cache_path=monomer_cache_path
            )
            amine_3d = cache_3d.get_monomer_3d(
                amine_smiles, n_confs=n_confs, seed=seed, cache_path=monomer_cache_path
            )
            if ald_3d is not None:
                for name, val in zip(cache_3d.DESCRIPTOR_NAMES, ald_3d):
                    features[f"ald_3d_{name}"] = val
            if amine_3d is not None:
                for name, val in zip(cache_3d.DESCRIPTOR_NAMES, amine_3d):
                    features[f"amine_3d_{name}"] = val
        except Exception:
            pass

    # 6. 二聚体 3D 描述符
    if use_dimer:
        try:
            dimer_3d = cache_3d.get_dimer_3d(
                ald_smiles, amine_smiles, n_confs=n_confs, seed=seed, cache_path=dimer_cache_path
            )
            if dimer_3d is not None:
                for name, val in zip(cache_3d.DIMER_DESCRIPTOR_NAMES, dimer_3d):
                    # DIMER_DESCRIPTOR_NAMES 已带 dimer_ 前缀，避免生成 dimer_3d_dimer_*
                    base_name = name[6:] if name.startswith("dimer_") else name
                    features[f"dimer_3d_{base_name}"] = val
        except Exception:
            pass

    return features


def featurize_pair(ald_smiles: str, amine_smiles: str,
                    use_rules: bool = True,
                    reduced_rules: bool = False,
                    use_interaction: bool = True,
                    use_3d: bool = False,
                    use_dimer: bool = False,
                    n_confs: int = 5,
                    seed: int = 42,
                    monomer_cache_path: Optional[Path] = None,
                    dimer_cache_path: Optional[Path] = None) -> pd.Series:
    """把一对单体转换为特征 Series。"""
    features = compute_pair_features(
        ald_smiles, amine_smiles,
        use_rules=use_rules,
        reduced_rules=reduced_rules,
        use_interaction=use_interaction,
        use_3d=use_3d,
        use_dimer=use_dimer,
        n_confs=n_confs,
        seed=seed,
        monomer_cache_path=monomer_cache_path,
        dimer_cache_path=dimer_cache_path,
    )
    return pd.Series(features)


def featurize_dataframe(df: pd.DataFrame,
                        smiles_cols: tuple[str, str] = ("aldehyde_smiles", "amine_smiles"),
                        use_rules: bool = True,
                        reduced_rules: bool = False,
                        use_interaction: bool = True,
                        use_3d: bool = False,
                        use_dimer: bool = False,
                        n_confs: int = 5,
                        seed: int = 42,
                        monomer_cache_path: Optional[Path] = None,
                        dimer_cache_path: Optional[Path] = None) -> pd.DataFrame:
    """批量把 DataFrame 中的单体对转换为特征矩阵。

    自动跳过醛或胺 SMILES 为 NaN 的行，并打印警告。
    保留原始 DataFrame 的索引，方便后续拼接额外列（如反应条件）。

    Args:
        df: 输入 DataFrame
        smiles_cols: SMILES 列名
        use_rules: 是否拼接规则特征
        reduced_rules: 是否使用精简规则向量
        use_interaction: 是否拼接交互特征
        use_3d: 是否拼接单体 3D 描述符
        use_dimer: 是否拼接二聚体 3D 描述符
        n_confs: 3D 构象生成数量
        seed: 3D 构象随机种子
        monomer_cache_path: 单体 3D 缓存路径
        dimer_cache_path: 二聚体 3D 缓存路径
    """
    records = []
    kept_indices = []
    n_skipped = 0
    for idx, row in df.iterrows():
        ald = row[smiles_cols[0]]
        amine = row[smiles_cols[1]]
        if pd.isna(ald) or pd.isna(amine):
            n_skipped += 1
            continue
        feats = compute_pair_features(
            ald, amine,
            use_rules=use_rules,
            reduced_rules=reduced_rules,
            use_interaction=use_interaction,
            use_3d=use_3d,
            use_dimer=use_dimer,
            n_confs=n_confs,
            seed=seed,
            monomer_cache_path=monomer_cache_path,
            dimer_cache_path=dimer_cache_path,
        )
        feats["aldehyde_smiles"] = ald
        feats["amine_smiles"] = amine
        records.append(feats)
        kept_indices.append(idx)
    if n_skipped > 0:
        print(f"警告：跳过 {n_skipped} 对 SMILES 缺失的样本")
    return pd.DataFrame(records, index=kept_indices)


if __name__ == "__main__":
    # 简单测试
    ald = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"  # Tp
    amine = "Nc1ccc(N)cc1"  # Pa
    feats = compute_pair_features(ald, amine)
    print(f"特征数: {len(feats)}")
    print(pd.Series(feats).head(20))
