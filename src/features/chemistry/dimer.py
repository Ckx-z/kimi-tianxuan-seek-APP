"""二聚体 3D 描述符 — 醛·胺非共价复合物构象 → 配对级 3D 特征。

COF 成膜的关键是配对后单体间的几何匹配：
  - 两个单体能否以有利取向相互接近
  - 反应位点的可及性和空间关系
  - 复合物的整体平面性

采用非共价复合物 (ald.amine dot-disconnected SMILES)：
  - 避免 SMARTS 反应处理多官能团时的脆弱性
  - ETKDG 会基于非键作用将两单体放在合理的相对位置
  - 科学上解释为"预反应复合物"的几何性质
"""
from __future__ import annotations

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolDescriptors
from rdkit.Chem.rdMolDescriptors import CalcPMI1, CalcPMI2, CalcPMI3


def _find_aldehyde_carbons(mol: Chem.Mol) -> list[int]:
    """找到所有醛基碳 [CX3H1](=O)。"""
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return []
    pattern = Chem.MolFromSmarts("[CX3H1](=O)")
    if pattern is None:
        return []
    try:
        return [m[0] for m in mol.GetSubstructMatches(pattern)]
    except Exception:
        return []


def _find_amine_nitrogens(mol: Chem.Mol) -> list[int]:
    """找到所有伯胺氮 [NX3H2]（排除硝基、酰胺）。"""
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return []
    pattern = Chem.MolFromSmarts("[NX3H2]")
    if pattern is None:
        return []
    result = []
    try:
        matches = mol.GetSubstructMatches(pattern)
    except Exception:
        return []
    for m in matches:
        n_idx = m[0]
        atom = mol.GetAtomWithIdx(n_idx)
        o_count = sum(1 for nb in atom.GetNeighbors() if nb.GetAtomicNum() == 8)
        if o_count == 0:
            result.append(n_idx)
    return result


def _generate_complex_conformers(ald_mol: Chem.Mol, amine_mol: Chem.Mol,
                                 n_confs: int = 5, seed: int = 42,
                                 ) -> list[Chem.Mol]:
    """醛胺非共价复合物 ETKDG 构象生成。"""
    # Combine two molecules into a complex
    complex_mol = Chem.CombineMols(ald_mol, amine_mol)
    try:
        Chem.SanitizeMol(complex_mol)
    except Exception:
        return []
    mol_h = Chem.AddHs(complex_mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.numThreads = 0
    params.pruneRmsThresh = 0.5
    try:
        cids = AllChem.EmbedMultipleConfs(mol_h, numConfs=n_confs, params=params)
    except Exception:
        return []
    if not cids:
        params.randomSeed = -1
        try:
            cids = AllChem.EmbedMultipleConfs(mol_h, numConfs=n_confs, params=params)
        except Exception:
            return []
    if not cids:
        return []
    results = []
    for cid in cids:
        try:
            AllChem.MMFFOptimizeMolecule(mol_h, confId=cid)
            results.append((mol_h, cid))
        except Exception:
            results.append((mol_h, cid))
    return results if results else [(mol_h, cids[0])]


def _complex_pmi_ratios(mol: Chem.Mol, conf_id: int) -> tuple[float, float]:
    """复合物 PMI 比值 — 整体扁平度。"""
    try:
        i1 = CalcPMI1(mol, confId=conf_id)
        i2 = CalcPMI2(mol, confId=conf_id)
        i3 = CalcPMI3(mol, confId=conf_id)
        if i3 < 1e-10:
            return 0.0, 0.0
        return i1 / i3, i2 / i3
    except Exception:
        return 0.0, 0.0


def _complex_planarity_rmsd(mol: Chem.Mol, conf_id: int) -> float:
    """复合物芳香碳偏离最小二乘平面的 RMSD。"""
    try:
        conf = mol.GetConformer(conf_id)
        aromatic_c = [a for a in mol.GetAtoms()
                      if a.GetIsAromatic() and a.GetAtomicNum() == 6]
        if len(aromatic_c) < 3:
            return 0.0
        pos = np.array([conf.GetAtomPosition(a.GetIdx()) for a in aromatic_c])
        center = pos.mean(axis=0)
        centered = pos - center
        _, _, vh = np.linalg.svd(centered)
        normal = vh[-1]
        distances = np.abs(centered @ normal)
        return float(np.sqrt(np.mean(distances ** 2)))
    except Exception:
        return 0.0


def _reaction_site_distances(mol: Chem.Mol, ald_mol: Chem.Mol,
                             amine_mol: Chem.Mol, conf_id: int) -> tuple[float, float]:
    """反应位点间几何关系。

    Returns:
        (min_distance, n_close_pairs): 最近的醛C-胺N距离, Å内接近对数
    """
    try:
        conf = mol.GetConformer(conf_id)
        ald_c_indices = [i for i in _find_aldehyde_carbons(ald_mol)]
        amine_n_indices = [ald_mol.GetNumAtoms() + i
                           for i in _find_amine_nitrogens(amine_mol)]

        if not ald_c_indices or not amine_n_indices:
            return 0.0, 0.0

        min_dist = float("inf")
        close_count = 0
        for ci in ald_c_indices:
            pos_c = np.array(conf.GetAtomPosition(ci))
            for ni in amine_n_indices:
                pos_n = np.array(conf.GetAtomPosition(ni))
                dist = np.linalg.norm(pos_c - pos_n)
                min_dist = min(min_dist, dist)
                if dist < 6.0:
                    close_count += 1

        return float(min_dist) if min_dist != float("inf") else 0.0, float(close_count)
    except Exception:
        return 0.0, 0.0


def _inter_monomer_angle(mol: Chem.Mol, ald_mol: Chem.Mol,
                         amine_mol: Chem.Mol, conf_id: int) -> float:
    """两单体芳香环最佳拟合平面间的夹角 (度)。"""
    try:
        conf = mol.GetConformer(conf_id)
        n_ald = ald_mol.GetNumAtoms()

        def _get_aromatic_positions(mol_obj, offset):
            indices = [a.GetIdx() + offset for a in mol_obj.GetAtoms()
                       if a.GetIsAromatic()]
            if len(indices) < 3:
                return None
            return np.array([conf.GetAtomPosition(i) for i in indices])

        ald_pos = _get_aromatic_positions(ald_mol, 0)
        amine_pos = _get_aromatic_positions(amine_mol, n_ald)

        if ald_pos is None or amine_pos is None:
            return 0.0

        def _plane_normal(positions):
            center = positions.mean(axis=0)
            centered = positions - center
            _, _, vh = np.linalg.svd(centered)
            return vh[-1]

        n1 = _plane_normal(ald_pos)
        n2 = _plane_normal(amine_pos)
        cos_angle = np.abs(np.dot(n1, n2))
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        return float(np.degrees(np.arccos(cos_angle)))
    except Exception:
        return 0.0


def _complex_gyration_radius(mol: Chem.Mol, conf_id: int) -> float:
    """复合物回转半径。"""
    try:
        conf = mol.GetConformer(conf_id)
        pos = conf.GetPositions()
        center = pos.mean(axis=0)
        rg = np.sqrt(np.mean(np.sum((pos - center) ** 2, axis=1)))
        return float(rg)
    except Exception:
        return 0.0


def _complex_mol_volume(mol: Chem.Mol, conf_id: int) -> float:
    """复合物分子体积。"""
    try:
        vol = AllChem.ComputeMolVolume(mol, confId=conf_id)
        return vol if not np.isnan(vol) else 0.0
    except Exception:
        return 0.0


def _has_metal(mol: Chem.Mol) -> bool:
    """检测分子是否含金属原子，UFF 力场处理金属极慢。"""
    metals = {3, 4, 11, 12, 13, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
              31, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 55, 56,
              57, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83}
    try:
        return any(a.GetAtomicNum() in metals for a in mol.GetAtoms())
    except Exception:
        return True


def _complex_sp3_ratio(mol: Chem.Mol) -> float:
    """复合物 sp3 碳占比。"""
    carbons = [a for a in mol.GetAtoms() if a.GetAtomicNum() == 6]
    if not carbons:
        return 0.0
    sp3 = sum(1 for c in carbons if str(c.GetHybridization()) == "SP3")
    return sp3 / len(carbons)


def _complex_dipole_moment(mol: Chem.Mol, conf_id: int) -> float:
    """复合物偶极矩。"""
    try:
        props = AllChem.MMFFGetMoleculeProperties(mol, confId=conf_id)
        if props is None:
            return 0.0
        dipole = props.GetDipoleMoment()
        return float(dipole) if dipole is not None else 0.0
    except Exception:
        return 0.0


def compute_dimer_3d(ald_smiles: str, amine_smiles: str,
                     n_confs: int = 5, seed: int = 42) -> list[float] | None:
    """醛胺配对 → 10 维二聚体 3D 描述符向量。

    1. pmi_i1_i3    — 复合物整体扁平度
    2. pmi_i2_i3    — 复合物对称性
    3. mol_volume   — 复合物体积
    4. radius_gyration — 复合物回转半径
    5. sp3_ratio    — 复合物 sp3 碳占比
    6. planar_rmsd  — 复合物芳香平面度
    7. dipole_moment — 复合物偶极矩
    8. min_site_dist — 最近醛C-胺N距离
    9. n_close_sites — 反应位点接近对数 (<6Å)
    10. inter_plane_angle — 单体芳香平面夹角

    Returns:
        10 维浮点列表，失败返回 None
    """
    ald_mol = Chem.MolFromSmiles(ald_smiles)
    if ald_mol is None:
        ald_mol = Chem.MolFromSmiles(ald_smiles, sanitize=False)
    amine_mol = Chem.MolFromSmiles(amine_smiles)
    if amine_mol is None:
        amine_mol = Chem.MolFromSmiles(amine_smiles, sanitize=False)
    if ald_mol is None or amine_mol is None:
        return None

    # 检查反应位点是否存在
    if not _find_aldehyde_carbons(ald_mol) or not _find_amine_nitrogens(amine_mol):
        return None

    # 含金属单体 — UFF 力场处理极慢，跳过 3D 计算
    if _has_metal(ald_mol) or _has_metal(amine_mol):
        return None

    confs = _generate_complex_conformers(ald_mol, amine_mol, n_confs, seed)
    if not confs:
        return None

    all_descs = []
    for mol_h, cid in confs:
        pmi = _complex_pmi_ratios(mol_h, cid)
        vol = _complex_mol_volume(mol_h, cid)
        rg = _complex_gyration_radius(mol_h, cid)
        sp3 = _complex_sp3_ratio(mol_h)
        planar = _complex_planarity_rmsd(mol_h, cid)
        dipole = _complex_dipole_moment(mol_h, cid)
        min_dist, n_close = _reaction_site_distances(
            mol_h, ald_mol, amine_mol, cid)
        angle = _inter_monomer_angle(mol_h, ald_mol, amine_mol, cid)

        all_descs.append([
            pmi[0], pmi[1], vol, rg, sp3, planar, dipole,
            min_dist, n_close, angle,
        ])

    arr = np.array(all_descs)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return arr.mean(axis=0).tolist()


DIMER_DESCRIPTOR_NAMES = [
    "dimer_pmi_i1_i3", "dimer_pmi_i2_i3", "dimer_mol_volume",
    "dimer_radius_gyration", "dimer_sp3_ratio", "dimer_planar_rmsd",
    "dimer_dipole_moment", "dimer_min_site_dist", "dimer_n_close_sites",
    "dimer_inter_plane_angle",
]
