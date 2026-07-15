"""3D 构象描述符 — SMILES → ETKDG 构象 → 10 维物理描述符。

COF 成膜相关的 3D 特征:
  1. PMI I1/I3 — 分子扁平度
  2. PMI I2/I3 — 分子对称性
  3. 分子体积 — 空间占位
  4. 最大径/最小径比 — 延展性
  5. sp3 碳占比 — 3D 扭曲程度
  6. 回转半径 — 紧凑度
  7. 可旋转键数 — 柔性
  8. 芳香平面度 RMSD — 芳香核共面程度
  9. 反应位点 H 阻挡数 — 位阻
  10. 偶极矩 — 电子分布不对称性
"""
from __future__ import annotations

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors
from rdkit.Chem.rdMolDescriptors import CalcPMI1, CalcPMI2, CalcPMI3


def _generate_conformers(mol: Chem.Mol, n_confs: int = 5,
                         seed: int = 42) -> list[Chem.Mol]:
    """ETKDG 生成多个构象，返回带构象的 mol 列表。"""
    # 确保 sanitize 成功
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return []
    mol_h = Chem.AddHs(mol)
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
    # MMFF 优化
    results = []
    for cid in cids:
        try:
            ff = AllChem.MMFFOptimizeMolecule(mol_h, confId=cid)
            if ff == 0:
                results.append((mol_h, cid))
        except Exception:
            results.append((mol_h, cid))
    return results if results else [(mol_h, cids[0])]


def _pmi_ratios(mol: Chem.Mol, conf_id: int) -> tuple[float, float]:
    """PMI 比值 I1/I3, I2/I3 — 衡量分子扁平度。"""
    try:
        i1 = CalcPMI1(mol, confId=conf_id)
        i2 = CalcPMI2(mol, confId=conf_id)
        i3 = CalcPMI3(mol, confId=conf_id)
        if i3 < 1e-10:
            return 0.0, 0.0
        return i1 / i3, i2 / i3
    except Exception:
        return 0.0, 0.0


def _molecular_volume(mol: Chem.Mol, conf_id: int) -> float:
    """分子体积 (近似)。"""
    try:
        vol = AllChem.ComputeMolVolume(mol, confId=conf_id)
        return vol if not np.isnan(vol) else 0.0
    except Exception:
        return 0.0


def _radius_ratio(mol: Chem.Mol, conf_id: int) -> float:
    """最大径/最小径比 — 分子延展性。"""
    try:
        conf = mol.GetConformer(conf_id)
        pos = conf.GetPositions()
        if len(pos) < 2:
            return 1.0
        from scipy.spatial.distance import pdist
        dists = pdist(pos)
        max_d = dists.max()
        # 最小径: 沿最大径方向的垂直截面内原子间距
        i_max = np.argmax(dists)
        n = len(pos)
        # 找最远原子对
        idx = 0
        for i in range(n):
            for j in range(i + 1, n):
                if idx == i_max:
                    a, b = i, j
                idx += 1
        axis = pos[b] - pos[a]
        axis_len = np.linalg.norm(axis)
        if axis_len < 1e-10:
            return 1.0
        axis_unit = axis / axis_len
        # 投影到垂直平面
        projections = pos - np.outer(pos @ axis_unit, axis_unit)
        if len(projections) < 2:
            return max_d / 1.0
        perp_dists = pdist(projections)
        min_d = perp_dists.max() if len(perp_dists) > 0 else 1.0
        min_d = max(min_d, 1.0)
        return max_d / min_d
    except Exception:
        return 1.0


def _sp3_ratio(mol: Chem.Mol) -> float:
    """sp3 碳占比。"""
    carbons = [a for a in mol.GetAtoms() if a.GetAtomicNum() == 6]
    if not carbons:
        return 0.0
    sp3 = sum(1 for c in carbons if str(c.GetHybridization()) == "SP3")
    return sp3 / len(carbons)


def _radius_of_gyration(mol: Chem.Mol, conf_id: int) -> float:
    """回转半径。"""
    try:
        conf = mol.GetConformer(conf_id)
        pos = conf.GetPositions()
        center = pos.mean(axis=0)
        rg = np.sqrt(np.mean(np.sum((pos - center) ** 2, axis=1)))
        return float(rg)
    except Exception:
        return 0.0


def _num_rotatable_bonds(mol: Chem.Mol) -> float:
    """可旋转键数。"""
    try:
        return float(Descriptors.NumRotatableBonds(mol))
    except Exception:
        return 0.0


def _aromatic_planarity_rmsd(mol: Chem.Mol, conf_id: int) -> float:
    """芳香平面度 RMSD — 所有芳香碳偏离最小二乘平面的 RMSD。"""
    try:
        conf = mol.GetConformer(conf_id)
        aromatic_atoms = [
            a for a in mol.GetAtoms()
            if a.GetIsAromatic() and a.GetAtomicNum() == 6
        ]
        if len(aromatic_atoms) < 3:
            return 0.0
        pos = np.array([conf.GetAtomPosition(a.GetIdx()) for a in aromatic_atoms])
        # 最小二乘平面拟合
        center = pos.mean(axis=0)
        centered = pos - center
        _, _, vh = np.linalg.svd(centered)
        normal = vh[-1]
        distances = np.abs(centered @ normal)
        rmsd = np.sqrt(np.mean(distances ** 2))
        return float(rmsd)
    except Exception:
        return 0.0


def _reaction_site_h_shielding(mol: Chem.Mol, conf_id: int) -> float:
    """反应位点 H 阻挡数 — 醛基 C / 胺基 N 周围 H 原子的空间密度。

    对醛: 统计醛基 C 周围 3Å 内的 H 原子数
    对胺: 统计伯胺 N 周围 3Å 内的 H 原子数
    如果既无醛基也无胺基，统计所有重原子周围 H 密度。
    """
    try:
        conf = mol.GetConformer(conf_id)
        mol_h = Chem.AddHs(mol)
        conf_h = mol_h.GetConformer(conf_id) if mol_h.GetNumConformers() > 0 else None
        if conf_h is None:
            return 0.0

        # 找反应位点
        site_atoms = []
        for a in mol.GetAtoms():
            if a.GetAtomicNum() == 6:
                for nb in a.GetNeighbors():
                    if nb.GetAtomicNum() == 8:
                        bond = mol.GetBondBetweenAtoms(a.GetIdx(), nb.GetIdx())
                        if bond and bond.GetBondType() == Chem.BondType.DOUBLE and nb.GetDegree() == 1:
                            site_atoms.append(a.GetIdx())
            elif a.GetAtomicNum() == 7:
                heavy = [n for n in a.GetNeighbors() if n.GetAtomicNum() != 1]
                if len(heavy) == 1:
                    site_atoms.append(a.GetIdx())

        if not site_atoms:
            return 0.0

        # H 原子坐标
        h_positions = []
        for a in mol_h.GetAtoms():
            if a.GetAtomicNum() == 1:
                pos = conf_h.GetAtomPosition(a.GetIdx())
                h_positions.append(np.array([pos.x, pos.y, pos.z]))

        if not h_positions:
            return 0.0

        h_pos = np.array(h_positions)
        total_shielding = 0.0
        for site_id in site_atoms:
            site_pos = np.array(conf.GetAtomPosition(site_id))
            dists = np.linalg.norm(h_pos - site_pos, axis=1)
            total_shielding += np.sum(dists < 3.0)

        return total_shielding / len(site_atoms)
    except Exception:
        return 0.0


def _dipole_moment(mol: Chem.Mol, conf_id: int) -> float:
    """偶极矩 (MMFF 优化后)。"""
    try:
        props = AllChem.MMFFGetMoleculeProperties(mol, confId=conf_id)
        if props is None:
            return 0.0
        dipole = props.GetDipoleMoment()
        return float(dipole) if dipole is not None else 0.0
    except Exception:
        return 0.0


def compute_3d_descriptors(smiles: str, n_confs: int = 5,
                           seed: int = 42) -> list[float] | None:
    """SMILES → 10 维 3D 描述符向量。多构象取均值。

    Returns:
        10 维浮点列表，失败返回 None
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None:
        return None

    confs = _generate_conformers(mol, n_confs, seed)
    if not confs:
        return None

    all_descs = []
    for mol_h, cid in confs:
        i1_i3, i2_i3 = _pmi_ratios(mol_h, cid)
        vol = _molecular_volume(mol_h, cid)
        rr = _radius_ratio(mol_h, cid)
        sp3 = _sp3_ratio(mol)
        rg = _radius_of_gyration(mol_h, cid)
        nrot = _num_rotatable_bonds(mol)
        planar_rmsd = _aromatic_planarity_rmsd(mol, cid)
        h_shield = _reaction_site_h_shielding(mol, cid)
        dipole = _dipole_moment(mol_h, cid)

        all_descs.append([
            i1_i3, i2_i3, vol, rr, sp3, rg, nrot,
            planar_rmsd, h_shield, dipole,
        ])

    arr = np.array(all_descs)
    # 多构象取均值，NaN 替换为 0
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return arr.mean(axis=0).tolist()


# 描述符名称 (用于 CSV 列名和归一化)
DESCRIPTOR_NAMES = [
    "pmi_i1_i3", "pmi_i2_i3", "mol_volume", "radius_ratio",
    "sp3_ratio", "radius_gyration", "num_rotatable_bonds",
    "aromatic_planarity_rmsd", "h_shielding", "dipole_moment",
]
