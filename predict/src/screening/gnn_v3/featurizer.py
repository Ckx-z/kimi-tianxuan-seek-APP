"""v3 分子图特征提取器 — 原子 37 维 + 边 5 维。

sanitize=False 兼容: aromatic/hybridization 从键类型反推，反应位点用原子环境兜底。
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import torch
from rdkit import Chem
from torch_geometric.data import Data

_ELEMENT_MAP = {6: 0, 7: 1, 8: 2, 9: 3, 15: 4, 16: 5, 17: 6, 35: 7, 53: 8}
_ELEMENT_DIM = 10


def _is_atom_aromatic(atom: Chem.Atom) -> bool:
    for bond in atom.GetBonds():
        if bond.GetIsAromatic():
            return True
    return False


def _infer_hybridization(atom: Chem.Atom) -> int:
    """0=SP, 1=SP2, 2=SP3, 3=other, 4=unspec"""
    deg = atom.GetDegree()
    if deg == 0:
        return 4
    has_double = any(b.GetBondType() == Chem.BondType.DOUBLE for b in atom.GetBonds())
    has_triple = any(b.GetBondType() == Chem.BondType.TRIPLE for b in atom.GetBonds())
    if has_triple:
        return 0
    if has_double or _is_atom_aromatic(atom):
        return 1
    return 2


def _is_aldehyde_carbon(atom: Chem.Atom) -> bool:
    """C 连接 O(双键) 且该 O 度为 1"""
    if atom.GetAtomicNum() != 6:
        return False
    for nb in atom.GetNeighbors():
        if nb.GetAtomicNum() == 8:
            bond = atom.GetOwningMol().GetBondBetweenAtoms(atom.GetIdx(), nb.GetIdx())
            if bond and bond.GetBondType() == Chem.BondType.DOUBLE and nb.GetDegree() == 1:
                return True
    return False


def _is_amine_nitrogen(atom: Chem.Atom) -> bool:
    """伯胺 N: 度=1(只连一个重原子), 排除硝基"""
    if atom.GetAtomicNum() != 7:
        return False
    heavy = [n for n in atom.GetNeighbors() if n.GetAtomicNum() != 1]
    if len(heavy) != 1:
        return False
    o_count = sum(1 for n in atom.GetNeighbors() if n.GetAtomicNum() == 8)
    if o_count >= 2:
        return False
    return True


def _position_encoding(mol: Chem.Mol) -> tuple[list[float], list[float]]:
    """BFS 距离到醛基/胺基，归一化"""
    n = mol.GetNumAtoms()
    ald_ids = [a.GetIdx() for a in mol.GetAtoms() if _is_aldehyde_carbon(a)]
    amine_ids = [a.GetIdx() for a in mol.GetAtoms() if _is_amine_nitrogen(a)]

    adj = {i: [nb.GetIdx() for nb in mol.GetAtomWithIdx(i).GetNeighbors()] for i in range(n)}

    def bfs(starts: list[int]) -> list[float]:
        d = [float("inf")] * n
        q = deque()
        for s in starts:
            d[s] = 0.0
            q.append(s)
        while q:
            u = q.popleft()
            for v in adj[u]:
                if d[v] == float("inf"):
                    d[v] = d[u] + 1.0
                    q.append(v)
        return d

    d_ald = bfs(ald_ids)
    d_amine = bfs(amine_ids)
    mx = max(max((x for x in d_ald if x != float("inf")), default=1.0),
             max((x for x in d_amine if x != float("inf")), default=1.0), 1.0)
    return [min(x / mx, 1.0) for x in d_ald], [min(x / mx, 1.0) for x in d_amine]


def _atom_features(atom: Chem.Atom, role: int, d_ald: float, d_amine: float) -> torch.Tensor:
    f = []

    # 元素 (10)
    e = [0.0] * _ELEMENT_DIM
    e[_ELEMENT_MAP.get(atom.GetAtomicNum(), 9)] = 1.0
    f.extend(e)

    # 杂化 (5)
    h = [0.0] * 5
    h[_infer_hybridization(atom)] = 1.0
    f.extend(h)

    # 电荷 (5)
    c = [0.0] * 5
    c[max(-2, min(2, atom.GetFormalCharge())) + 2] = 1.0
    f.extend(c)

    # 度 (6)
    d = [0.0] * 6
    d[min(atom.GetDegree(), 5)] = 1.0
    f.extend(d)

    # H 数 (4) — sanitize=False 时用显式 H 计数
    h_count = sum(1 for n in atom.GetNeighbors() if n.GetAtomicNum() == 1)
    hc = [0.0] * 4
    hc[min(h_count, 3)] = 1.0
    f.extend(hc)

    # 芳香 (1) — 从键推断
    f.append(1.0 if _is_atom_aromatic(atom) else 0.0)

    # 环内 (1)
    f.append(1.0 if atom.IsInRing() else 0.0)

    # 反应位点 (2)
    f.append(1.0 if _is_aldehyde_carbon(atom) else 0.0)
    f.append(1.0 if _is_amine_nitrogen(atom) else 0.0)

    # 角色 (1)
    f.append(float(role))

    # 位置编码 (2)
    f.append(d_ald)
    f.append(d_amine)

    return torch.tensor(f, dtype=torch.float)


def _bond_features(bond: Chem.Bond) -> torch.Tensor:
    bt = bond.GetBondType()
    return torch.tensor([
        1.0 if bt == Chem.BondType.SINGLE else 0.0,
        1.0 if bt == Chem.BondType.DOUBLE else 0.0,
        1.0 if bt == Chem.BondType.TRIPLE else 0.0,
        1.0 if bt == Chem.BondType.AROMATIC else 0.0,
        1.0 if bond.GetIsConjugated() else 0.0,
    ], dtype=torch.float)


def mol_to_graph(mol: Chem.Mol, role: int = 0) -> Data:
    d_ald, d_amine = _position_encoding(mol)
    x = torch.stack([_atom_features(mol.GetAtomWithIdx(i), role, d_ald[i], d_amine[i])
                     for i in range(mol.GetNumAtoms())])

    ei, ea = [], []
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        ei.extend([[i, j], [j, i]])
        bf = _bond_features(bond)
        ea.extend([bf, bf])

    if ei:
        ei_t = torch.tensor(ei, dtype=torch.long).t().contiguous()
        ea_t = torch.stack(ea)
    else:
        ei_t = torch.zeros((2, 0), dtype=torch.long)
        ea_t = torch.zeros((0, 5), dtype=torch.float)

    return Data(x=x, edge_index=ei_t, edge_attr=ea_t)


def smiles_to_graph(smiles: str, role: int = 0) -> Optional[Data]:
    if not smiles or not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    return mol_to_graph(mol, role)
