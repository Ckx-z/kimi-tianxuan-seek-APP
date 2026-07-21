r"""化学硬约束规则 — 命中任一 → adj=0。

推理时调用 check_hard_constraints() 返回 (violations, bonus, bonus_reason)：
  - violations: 命中的规则名列表，非空 → film_prob_adjusted = 0
  - bonus: 固定 1.0（不再奖励）

规则列表:
  单体级:
    1. 无芳香原子 — 分子不含任何芳香原子 (合并原规则 1+2)
    3. 直链分子(无环)
    4. 非平面环(sp3或大环)
    5. 支链>4碳
    6. 含硼酸酯
    7. 官能团有效性
    8. 官能团非中心对称 — 反演对称检查
    9. 对位违规 — C2同环对位 / C3同环间位 / C3不同环与桥对位
  配对级:
    10. 自反应 — 醛含胺基 / 胺含醛基或酮羰
    11. 拓扑不匹配 — 仅保留 C3+C2
    12. 对位苯链过长 >2 (三联苯排除)
    13. 无苯环长支链 >8
    14. 芳环过少 <4
    15. 芳环过多 >8
    16. 双刚
    17. 超尺寸
    18. 大小失配 — 芳环数差 <1
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rdkit import Chem

# ── SMARTS ──────────────────────────────────────────────────
_ALD_SMARTS = Chem.MolFromSmarts("[CX3H1](=O)[#6]")
_AM_SMARTS = Chem.MolFromSmarts("[NH2][c]")
_AMINE_N_SMARTS = Chem.MolFromSmarts("[NH2]")
_BENZENE_SMARTS = Chem.MolFromSmarts("c1ccccc1")
_AROMATIC_SMARTS = Chem.MolFromSmarts("[a]")
_BORONATE_SMARTS = Chem.MolFromSmarts("[B]([O])([O])")
_HYDRAZIDE_SMARTS = Chem.MolFromSmarts("[NH2][NH]C(=O)")
_CARBOHYDRAZIDE_SMARTS = Chem.MolFromSmarts("[NH2][NH]C(=O)[NH][NH2]")
_AMIDE_NH2_SMARTS = Chem.MolFromSmarts("[NH2]C(=O)")
_SULFONAMIDE_SMARTS = Chem.MolFromSmarts("[NH2]S(=O)(=O)")
_ESTER_ALDEHYDE_SMARTS = Chem.MolFromSmarts("[CX3](=O)[O][#6]")
_KETONE_SMARTS = Chem.MolFromSmarts("[CX3](=O)[#6]")
_CF3_SMARTS = Chem.MolFromSmarts("[CX4]([F])([F])([F])")
_F_AROM_SMARTS = Chem.MolFromSmarts("[F][c]")


# ── F/CF3 描述符检测 ────────────────────────────────────────

def _get_aldehyde_arom_carbons(mol: Chem.Mol) -> list[int]:
    """获取醛基连接的芳香碳索引 (醛基 -CHO 的 C 所连的芳碳)。"""
    matches = mol.GetSubstructMatches(_ALD_SMARTS)
    result = []
    for m in matches:
        c_idx = m[2]
        if mol.GetAtomWithIdx(c_idx).GetIsAromatic():
            result.append(c_idx)
    return result


def _get_amine_arom_carbons(mol: Chem.Mol) -> list[int]:
    """获取伯胺 -NH2 连接的芳香碳索引。"""
    matches = mol.GetSubstructMatches(_AM_SMARTS)
    result = []
    for m in matches:
        c_idx = m[1]
        if mol.GetAtomWithIdx(c_idx).GetIsAromatic():
            result.append(c_idx)
    return result


def _is_same_ring(mol: Chem.Mol, idx1: int, idx2: int) -> bool:
    """两个原子是否在同一个环内。"""
    for ring in mol.GetRingInfo().AtomRings():
        if idx1 in ring and idx2 in ring:
            return True
    return False


def _is_ortho_on_ring(mol: Chem.Mol, idx1: int, idx2: int) -> bool:
    """idx1 和 idx2 在同一个芳环上是否互为邻位 (路径长度=2)。"""
    path = Chem.GetShortestPath(mol, idx1, idx2)
    return path is not None and len(path) == 2 and _is_same_ring(mol, idx1, idx2)


def _detect_F_CF3_features(ald_mol: Chem.Mol, am_mol: Chem.Mol) -> list[float]:
    """检测 F/CF3 位置特征 + 电子平衡 + 位阻描述符。

    返回值 (11 维):
      [F_on_ald, F_ortho_ald, F_on_amine,
       CF3_on_ald, CF3_on_amine, CF3_ortho_ald,
       polyfluoro_ald,
       ald_e_withdrawing, am_e_withdrawing, e_favorable,
       ortho_steric_ald]
    """
    f_ald = sum(1 for a in ald_mol.GetAtoms() if a.GetAtomicNum() == 9)
    f_am = sum(1 for a in am_mol.GetAtoms() if a.GetAtomicNum() == 9)
    cf3_ald = len(ald_mol.GetSubstructMatches(_CF3_SMARTS))
    cf3_am = len(am_mol.GetSubstructMatches(_CF3_SMARTS))

    ald_arom_c = _get_aldehyde_arom_carbons(ald_mol)

    # F_on_ald, F_on_amine, polyfluoro_ald
    F_on_ald = 1.0 if f_ald > 0 else 0.0
    F_on_amine = 1.0 if f_am > 0 else 0.0
    polyfluoro_ald = 1.0 if f_ald >= 2 else 0.0

    # F_ortho_ald: F 邻位醛基
    F_ortho_ald = 0.0
    if f_ald > 0 and ald_arom_c:
        for a in ald_mol.GetAtoms():
            if a.GetAtomicNum() != 9:
                continue
            for nb in a.GetNeighbors():
                if nb.GetAtomicNum() == 6 and nb.GetIsAromatic():
                    for ac in ald_arom_c:
                        if _is_ortho_on_ring(ald_mol, nb.GetIdx(), ac):
                            F_ortho_ald = 1.0
                            break

    # CF3 检测
    CF3_on_ald = 1.0 if cf3_ald > 0 else 0.0
    CF3_on_amine = 1.0 if cf3_am > 0 else 0.0

    # CF3_ortho_ald: CF3 邻位醛基
    CF3_ortho_ald = 0.0
    ortho_steric_ald = 0.0
    if cf3_ald > 0 and ald_arom_c:
        for match in ald_mol.GetSubstructMatches(_CF3_SMARTS):
            cf3_c = match[0]
            for nb in ald_mol.GetAtomWithIdx(cf3_c).GetNeighbors():
                if nb.GetAtomicNum() == 6 and nb.GetIsAromatic():
                    for ac in ald_arom_c:
                        if _is_ortho_on_ring(ald_mol, nb.GetIdx(), ac):
                            CF3_ortho_ald = 1.0
                            ortho_steric_ald = 1.0
                            break

    # F 邻位也产生位阻 (虽小于 CF3, 但 ortho-F 仍有 Charton ν=0.27)
    if F_ortho_ald > 0:
        ortho_steric_ald = 1.0

    # 电子平衡
    ald_EW = 1.0 if (f_ald > 0 or cf3_ald > 0) else 0.0
    am_EW = 1.0 if (f_am > 0 or cf3_am > 0) else 0.0
    e_favorable = 1.0 if (ald_EW > 0 and am_EW == 0) else 0.0

    return [
        F_on_ald, F_ortho_ald, F_on_amine,
        CF3_on_ald, CF3_on_amine, CF3_ortho_ald,
        polyfluoro_ald,
        ald_EW, am_EW, e_favorable,
        ortho_steric_ald,
    ]


# ── 辅助 ────────────────────────────────────────────────────

def _topology(n_ald: int, n_am: int) -> str:
    if n_ald >= 3 or n_am >= 3:
        return "C3"
    if n_ald >= 2 or n_am >= 2:
        return "C2"
    return "C1"


def _count_aromatic_rings(mol: Chem.Mol) -> int:
    if mol is None:
        return 0
    ri = mol.GetRingInfo()
    return sum(1 for ring in ri.AtomRings()
               if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring))


def _count_valid_aldehydes(mol: Chem.Mol) -> int:
    if mol.HasSubstructMatch(_ESTER_ALDEHYDE_SMARTS):
        return 0
    matches = mol.GetSubstructMatches(_ALD_SMARTS)
    valid = 0
    for m in matches:
        if not mol.GetAtomWithIdx(m[0]).IsInRing():
            valid += 1
    return valid


def _count_valid_amines(mol: Chem.Mol) -> int:
    if mol.HasSubstructMatch(_CARBOHYDRAZIDE_SMARTS):
        return 0
    if mol.HasSubstructMatch(_HYDRAZIDE_SMARTS):
        return 0
    if mol.HasSubstructMatch(_AMIDE_NH2_SMARTS):
        return 0
    if mol.HasSubstructMatch(_SULFONAMIDE_SMARTS):
        return 0
    matches = mol.GetSubstructMatches(_AMINE_N_SMARTS)
    valid = 0
    for m in matches:
        n_idx = m[0]
        n_atom = mol.GetAtomWithIdx(n_idx)
        heavy = [nb for nb in n_atom.GetNeighbors() if nb.GetAtomicNum() != 1]
        if len(heavy) != 1:
            continue
        nb = heavy[0]
        if nb.GetAtomicNum() == 7:
            continue
        if nb.GetAtomicNum() == 6:
            has_carbonyl = any(
                nb2.GetAtomicNum() == 8
                and mol.GetBondBetweenAtoms(nb.GetIdx(), nb2.GetIdx()) is not None
                and mol.GetBondBetweenAtoms(nb.GetIdx(), nb2.GetIdx()).GetBondType() == Chem.BondType.DOUBLE
                for nb2 in nb.GetNeighbors()
            )
            if has_carbonyl:
                continue
        if nb.GetAtomicNum() == 16:
            continue
        valid += 1
    return valid


def _count_linear_para_chain(mol: Chem.Mol) -> int:
    rings = mol.GetSubstructMatches(_BENZENE_SMARTS)
    if len(rings) <= 1:
        return len(rings)
    n = len(rings)
    adj = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
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


def _max_side_chain_without_benzene(mol: Chem.Mol) -> int:
    """计算环上挂的非环碳链最长连续长度，只统计不含苯环的支链。"""
    ring_atoms = set()
    for r in mol.GetRingInfo().AtomRings():
        ring_atoms.update(r)

    non_ring = [i for i in range(mol.GetNumAtoms())
                if i not in ring_atoms]
    if len(non_ring) < 4:
        return 0

    nr_set = set(non_ring)
    adj = {i: [] for i in non_ring}
    for i in non_ring:
        a = mol.GetAtomWithIdx(i)
        for nb in a.GetNeighbors():
            j = nb.GetIdx()
            if j in nr_set:
                b = mol.GetBondBetweenAtoms(i, j)
                if b and b.GetBondType() == Chem.BondType.SINGLE:
                    adj[i].append(j)

    visited = set()
    max_diameter = 0
    for start in non_ring:
        if start in visited:
            continue
        q = [start]
        comp = {start}
        visited.add(start)
        for node in q:
            for nb in adj[node]:
                if nb not in visited:
                    visited.add(nb)
                    q.append(nb)
                    comp.add(nb)
        has_aromatic = any(
            mol.GetAtomWithIdx(a).GetIsAromatic() for a in comp
        )
        if has_aromatic:
            continue
        if len(comp) < 4:
            continue

        comp_adj = {a: [nb for nb in adj[a] if nb in comp] for a in comp}
        leaves = [a for a in comp if len(comp_adj[a]) <= 1]
        if not leaves:
            continue

        def _bfs_farthest(start_node):
            qq = [start_node]
            dist = {start_node: 0}
            farthest_node = start_node
            for node in qq:
                for nb in comp_adj[node]:
                    if nb not in dist:
                        dist[nb] = dist[node] + 1
                        qq.append(nb)
                        if dist[nb] > dist[farthest_node]:
                            farthest_node = nb
            return farthest_node, dist[farthest_node]

        a, _ = _bfs_farthest(leaves[0])
        _, diameter = _bfs_farthest(a)
        max_diameter = max(max_diameter, diameter + 1)

    return max_diameter


def _get_reactive_carbons(mol: Chem.Mol, is_aldehyde: bool) -> List[int]:
    """获取反应位点连接的芳香碳索引。"""
    if is_aldehyde:
        matches = mol.GetSubstructMatches(_ALD_SMARTS)
        return [m[2] for m in matches]
    else:
        matches = mol.GetSubstructMatches(_AM_SMARTS)
        return [m[1] for m in matches]


def _get_para_position(c_idx: int, ring: Tuple[int, ...]) -> Optional[int]:
    """获取苯环上 c_idx 的对位原子索引。"""
    ring_set = set(ring)
    if c_idx not in ring_set:
        return None
    ring_list = list(ring)
    idx_in_ring = ring_list.index(c_idx)
    return ring_list[(idx_in_ring + 3) % 6]


# ── 单体级规则 ──────────────────────────────────────────────

def _check_monomer_hard_rules(mol: Chem.Mol, smi: str, n_claimed_ald: int,
                               n_claimed_am: int) -> List[str]:
    violations = []

    # 1. 无芳香原子 (合并原规则 1+2)
    if not mol.HasSubstructMatch(_AROMATIC_SMARTS):
        violations.append("无芳香原子")

    # 3. 直链分子 (无环)
    rings = mol.GetRingInfo().AtomRings()
    if not rings:
        violations.append("直链分子(无环)")

    # 4. 非芳香环且(sp3碳 或 >6元环)
    for ring in rings:
        is_aromatic = all(mol.GetAtomWithIdx(a).GetIsAromatic() for a in ring)
        if is_aromatic:
            continue
        has_sp3 = any(
            mol.GetAtomWithIdx(a).GetAtomicNum() == 6
            and mol.GetAtomWithIdx(a).GetHybridization() == Chem.HybridizationType.SP3
            for a in ring
        )
        is_large = len(ring) > 6
        if has_sp3 or is_large:
            violations.append("非平面环(sp3或大环)")
            break

    # 5. 支链>4碳
    ring_atoms = set()
    for r in rings:
        ring_atoms.update(r)
    found_long_branch = False
    for a_idx in ring_atoms:
        atom = mol.GetAtomWithIdx(a_idx)
        if atom.GetAtomicNum() != 6:
            continue
        for nb in atom.GetNeighbors():
            if nb.GetIdx() in ring_atoms or nb.GetAtomicNum() != 6:
                continue
            visited = {a_idx, nb.GetIdx()}
            q = [(nb.GetIdx(), 1)]
            max_len = 1
            while q:
                cur, d = q.pop(0)
                max_len = max(max_len, d)
                for nn in mol.GetAtomWithIdx(cur).GetNeighbors():
                    if nn.GetIdx() in visited:
                        continue
                    if nn.GetAtomicNum() == 6 and nn.GetIdx() not in ring_atoms:
                        visited.add(nn.GetIdx())
                        q.append((nn.GetIdx(), d + 1))
            if max_len > 4:
                found_long_branch = True
                break
        if found_long_branch:
            break
    if found_long_branch:
        violations.append("支链>4碳")

    # 6. 硼酸酯
    if mol.HasSubstructMatch(_BORONATE_SMARTS):
        violations.append("含硼酸酯")

    # 7. 官能团有效性
    if n_claimed_ald > 0:
        valid_ald = _count_valid_aldehydes(mol)
        if valid_ald == 0:
            violations.append("无有效醛基")
        elif valid_ald != n_claimed_ald:
            violations.append(f"醛基数不匹配(称{n_claimed_ald}实{valid_ald})")
    if n_claimed_am > 0:
        valid_am = _count_valid_amines(mol)
        if valid_am == 0:
            violations.append("无有效伯胺")
        elif valid_am != n_claimed_am:
            violations.append(f"胺基数不匹配(称{n_claimed_am}实{valid_am})")

    # 8. 官能团中心对称 (C2/C3 都必须)
    n_ald = _count_valid_aldehydes(mol)
    n_am = _count_valid_amines(mol)
    topo = _topology(n_ald, n_am)
    if topo in ("C2", "C3"):
        if not _check_centrosymmetric(mol, n_ald, n_am, topo):
            violations.append("官能团非中心对称")

    # 9. 对位检查
    para_violations = _check_para_positions(mol, n_ald, n_am, topo)
    violations.extend(para_violations)

    return violations


def _check_centrosymmetric(mol: Chem.Mol, n_ald: int, n_am: int,
                            topo: str) -> bool:
    """检查官能团对称性。

    规则 8.1 — C2 反演对称 (中心对称):
      分子中存在唯一对称中心 i，任意原子沿 i 反演后能找到等价原子。
      同环 C2: 两个反应位点必须对位 (规则 9 已检查，此处放行)。
      不同环 C2: 两个反应位点所在苯环必须通过线性对位桥连接
                 (反应位点-苯环-桥-苯环-反应位点呈直线)。

    规则 8.2 — C3 三重旋转对称 (C₃ 轴):
      分子存在 C₃ 轴，绕轴旋转 120°/240° 后与原分子完全重合。
      三个反应位点必须在不同苯环上，且三个苯环通过中心桥等价连接。
    """
    is_aldehyde = n_ald >= 2
    reactive_c = _get_reactive_carbons(mol, is_aldehyde)

    if len(reactive_c) < 2:
        return False

    rings = mol.GetSubstructMatches(_BENZENE_SMARTS)

    if topo == "C2":
        c_rings = []
        for c in reactive_c:
            for ring in rings:
                if c in set(ring):
                    c_rings.append((c, ring))
                    break
        if len(c_rings) < 2:
            return False

        c1, ring1 = c_rings[0]
        c2, ring2 = c_rings[1]

        # 同环 C2: 必须对位 (规则 9 已检查，此处放行)
        if ring1 == ring2:
            return True

        # 不同环 C2: 检查线性对位桥连接 (反演对称)
        return _check_linear_para_bridge(mol, c1, ring1, c2, ring2, rings)

    else:  # C3 — 三个反应位点在不同苯环上，且各自对位连接桥
        if len(reactive_c) < 3:
            return False

        c_rings = []
        for c in reactive_c:
            for ring in rings:
                if c in set(ring):
                    c_rings.append((c, ring))
                    break
        if len(c_rings) < 3:
            return False

        # 三个位点必须在不同苯环上
        unique_rings = {tuple(r) for _, r in c_rings}
        if len(unique_rings) < 3:
            return False

        # 仅检查每个反应位点在所在苯环的对位有桥接出 (官能团对称)
        for c, ring in c_rings:
            para = _get_para_position(c, ring)
            if para is None:
                return False
            para_atom = mol.GetAtomWithIdx(para)
            has_bridge = any(
                nb.GetIdx() not in set(ring)
                for nb in para_atom.GetNeighbors()
            )
            if not has_bridge:
                return False
        return True


def _check_c3_rotational_symmetry(mol: Chem.Mol,
                                    c_rings: List[Tuple[int, Tuple[int, ...]]],
                                    all_rings: Tuple[Tuple[int, ...]]) -> bool:
    """检查 C3 三重旋转对称: 三个苯环通过共同中心桥等价连接。

    三个苯环的对位原子应连接到同一个中心桥（原子或环），
    形成 120° 旋转对称结构。
    """
    ring_sets = [set(r) for _, r in c_rings]

    # 找每个苯环的对位连接点
    bridge_connections = []
    for (c, ring), ring_set in zip(c_rings, ring_sets):
        para = _get_para_position(c, ring)
        if para is None:
            return False
        # 找对位原子连接的非环邻居
        para_atom = mol.GetAtomWithIdx(para)
        bridge_found = False
        for nb in para_atom.GetNeighbors():
            nb_idx = nb.GetIdx()
            if nb_idx in ring_set:
                continue
            # 检查这个邻居是否通向中心桥
            bridge_connections.append((para, nb_idx))
            bridge_found = True
            break
        if not bridge_found:
            return False

    if len(bridge_connections) < 3:
        return False

    # 三个桥连接应汇聚到同一个中心结构
    # BFS 从三个桥出发，检查它们是否在中心相遇
    center_candidates = None
    for _, start in bridge_connections:
        visited = {start}
        q = [start]
        reachable = {start}
        for node in q:
            for nn in mol.GetAtomWithIdx(node).GetNeighbors():
                nn_idx = nn.GetIdx()
                if nn_idx not in visited:
                    # 不回走到苯环
                    in_any_ring = any(nn_idx in rs for rs in ring_sets)
                    if not in_any_ring or nn_idx == start:
                        visited.add(nn_idx)
                        q.append(nn_idx)
                        reachable.add(nn_idx)
        if center_candidates is None:
            center_candidates = reachable
        else:
            center_candidates = center_candidates & reachable

    return len(center_candidates) > 0


def _check_linear_para_bridge(mol: Chem.Mol, c1: int, ring1: Tuple[int, ...],
                               c2: int, ring2: Tuple[int, ...],
                               rings: Tuple[Tuple[int, ...]]) -> bool:
    """检查两个不同苯环之间是否通过线性对位桥连接。

    即: c1 在 ring1 上对位的原子连接桥 → 桥 → ring2 上对位于 c2 的原子。
    """
    ring1_set = set(ring1)
    ring2_set = set(ring2)

    para1 = _get_para_position(c1, ring1)
    para2 = _get_para_position(c2, ring2)
    if para1 is None or para2 is None:
        return False
    if para1 == para2:
        return False

    path = Chem.GetShortestPath(mol, para1, para2)
    if path is None:
        return False

    for a in path:
        if a == para1 or a == para2:
            continue
        if a in ring1_set or a in ring2_set:
            return False

    return True


def _reactive_is_para_to_bridge(mol: Chem.Mol, c: int, ring: Tuple[int, ...],
                                 all_rings: Tuple[Tuple[int, ...]]) -> bool:
    """检查 C3 反应位点 c 在 ring 上的对位原子是否连接桥（连接其他苯环）。"""
    ring_set = set(ring)
    para = _get_para_position(c, ring)
    if para is None:
        return False

    para_atom = mol.GetAtomWithIdx(para)
    for nb in para_atom.GetNeighbors():
        nb_idx = nb.GetIdx()
        if nb_idx in ring_set:
            continue
        visited = {para, nb_idx}
        q = [nb_idx]
        for node in q:
            for other_ring in all_rings:
                if other_ring == ring:
                    continue
                if node in set(other_ring):
                    return True
            for nn in mol.GetAtomWithIdx(node).GetNeighbors():
                if nn.GetIdx() not in visited:
                    visited.add(nn.GetIdx())
                    q.append(nn.GetIdx())
    return False


def _check_para_positions(mol: Chem.Mol, n_ald: int, n_am: int,
                           topo: str) -> List[str]:
    """检查反应位点的邻/间/对位关系。

    C2 同环: 必须对位 (间位/邻位 → 排除)
    C3 同环: 必须间位 (邻位/对位 → 排除)
    C3 不同环: 反应位点与桥必须对位 (在 _check_centrosymmetric 中检查)
    """
    violations = []
    if topo not in ("C2", "C3"):
        return violations

    if n_ald >= 2:
        matches = mol.GetSubstructMatches(_ALD_SMARTS)
        reactive_atoms = [m[2] for m in matches]
    elif n_am >= 2:
        matches = mol.GetSubstructMatches(_AM_SMARTS)
        reactive_atoms = [m[1] for m in matches]
    else:
        return violations

    if len(reactive_atoms) < 2:
        return violations

    rings = mol.GetSubstructMatches(_BENZENE_SMARTS)
    for ring in rings:
        ring_set = set(ring)
        on_ring = [a for a in reactive_atoms if a in ring_set]
        if len(on_ring) < 2:
            continue
        for i in range(len(on_ring)):
            for j in range(i + 1, len(on_ring)):
                path = Chem.GetShortestPath(mol, on_ring[i], on_ring[j])
                if path is None:
                    continue
                ring_bonds = sum(
                    1 for k in range(len(path) - 1)
                    if path[k] in ring_set and path[k + 1] in ring_set
                )
                if topo == "C2":
                    if ring_bonds == 3:
                        continue  # 对位 → 通过
                    elif ring_bonds == 2:
                        violations.append("非对位(间位)")
                    elif ring_bonds == 1:
                        violations.append("非对位(邻位)")
                    return violations
                else:  # C3
                    if ring_bonds == 1:
                        violations.append("C3邻位(禁)")
                    elif ring_bonds == 3:
                        violations.append("C3对位(非间位)")
    return violations


# ── 配对级规则 ──────────────────────────────────────────────

def _check_pair_hard_rules(ald_mol: Chem.Mol, am_mol: Chem.Mol,
                            ald_smi: str, am_smi: str,
                            n_ald: int, n_am: int) -> List[str]:
    violations = []

    # 10. 自反应 — 已移除，GNN 应自行学习此类模式

    # 11. 拓扑不匹配 — 只保留 C3+C2
    if not ((n_ald == 3 and n_am == 2) or (n_ald == 2 and n_am == 3)):
        violations.append(f"拓扑不匹配(仅C3+C2, n_ald={n_ald}, n_am={n_am})")

    # 12. 对位苯链过长 >2 — 已移除，三联苯不应强制排除

    # 13. 无苯环长支链 >8
    sc_ald = _max_side_chain_without_benzene(ald_mol)
    sc_am = _max_side_chain_without_benzene(am_mol)
    max_sc = max(sc_ald, sc_am)
    if max_sc > 8:
        violations.append(f"无苯环长支链({max_sc}>8)")

    # 14-17. 芳环数约束
    ra = _count_aromatic_rings(ald_mol)
    rm = _count_aromatic_rings(am_mol)
    total = ra + rm

    if total < 4:
        violations.append(f"芳环过少({total}<4)")
    if total > 8:
        violations.append(f"芳环过多({total}>8)")
    if ra >= 4 and rm >= 4:
        violations.append(f"双刚(ra={ra},rm={rm})")
    # 17. 超尺寸 — 已移除，与规则15(芳环过多)重叠

    return violations


# ── 大小配对惩罚 ──────────────────────────────────────────

def _check_big_small_mismatch(ald_smi: str, am_smi: str) -> Optional[str]:
    """大小不匹配（芳环数差 <1）→ 惩罚，防止两个大分子或两个小分子配对。"""
    ald_mol = Chem.MolFromSmiles(ald_smi)
    am_mol = Chem.MolFromSmiles(am_smi)
    if ald_mol is None or am_mol is None:
        return None
    ra = _count_aromatic_rings(ald_mol)
    rm = _count_aromatic_rings(am_mol)
    if abs(ra - rm) < 1:
        return f"大小失配(ra={ra},rm={rm},diff<1)"
    return None


# ── 主接口 ──────────────────────────────────────────────────

def check_hard_constraints(ald_smi: str, am_smi: str,
                            n_ald_claimed: int = 0,
                            n_am_claimed: int = 0) -> Tuple[List[str], float, str]:
    ald_mol = Chem.MolFromSmiles(ald_smi)
    am_mol = Chem.MolFromSmiles(am_smi)
    if ald_mol is None or am_mol is None:
        return ["SMILES解析失败"], 1.0, ""

    all_violations = []

    all_violations.extend(
        _check_monomer_hard_rules(ald_mol, ald_smi, n_ald_claimed, 0))
    all_violations.extend(
        _check_monomer_hard_rules(am_mol, am_smi, 0, n_am_claimed))

    n_ald = _count_valid_aldehydes(ald_mol)
    n_am = _count_valid_amines(am_mol)
    all_violations.extend(
        _check_pair_hard_rules(ald_mol, am_mol, ald_smi, am_smi, n_ald, n_am))

    size_mismatch = _check_big_small_mismatch(ald_smi, am_smi)
    if size_mismatch:
        all_violations.append(size_mismatch)

    seen = set()
    unique = []
    for v in all_violations:
        if v not in seen:
            seen.add(v)
            unique.append(v)

    return unique, 1.0, ""


# ── 规则向量 (方案 B) ────────────────────────────────────────

# 18 条规则的标准化名称，顺序固定
RULE_NAMES = [
    # 违反规则 (23 条, 1=bad)
    "无芳香原子",
    "直链分子(无环)",
    "非平面环(sp3或大环)",
    "支链>4碳",
    "含硼酸酯",
    "无有效醛基",
    "醛基数不匹配",
    "无有效伯胺",
    "胺基数不匹配",
    "官能团非中心对称",
    "非对位(间位)",
    "非对位(邻位)",
    "C3邻位(禁)",
    "C3对位(非间位)",
    "自反应",
    "拓扑不匹配",
    "对位苯链过长",
    "无苯环长支链",
    "芳环过少",
    "芳环过多",
    "双刚",
    "超尺寸",
    "大小失配",
    # F/CF3 描述符 (11 条, 1=存在该取代效应)
    "F_on_ald",
    "F_ortho_ald",
    "F_on_amine",
    "CF3_on_ald",
    "CF3_on_amine",
    "CF3_ortho_ald",
    "polyfluoro_ald",
    "ald_e_withdrawing",
    "am_e_withdrawing",
    "e_favorable",
    "ortho_steric_ald",
]

RULE_DIM = len(RULE_NAMES)  # 34 维 (23 违反 + 11 F/CF3 描述符)


def get_rule_vector(ald_smi: str, am_smi: str) -> list[float]:
    """返回 34 维规则向量 (23 违反 + 11 F/CF3 描述符)。

    前 23 维: 规则命中=1.0 (violation), 否则=0.0。
    后 11 维: F/CF3 取代基位置 + 电子平衡 + 位阻描述符。
    """
    ald_mol = Chem.MolFromSmiles(ald_smi)
    am_mol = Chem.MolFromSmiles(am_smi)
    if ald_mol is None or am_mol is None:
        return [0.0] * RULE_DIM

    violations, _, _ = check_hard_constraints(ald_smi, am_smi)
    vec = [0.0] * RULE_DIM
    for v in violations:
        v_base = v.split("(")[0] if "(" in v else v
        for i, name in enumerate(RULE_NAMES[:23]):  # 仅匹配前 23 条违反规则
            if name in v or v_base in name:
                vec[i] = 1.0
                break

    # 追加 F/CF3 描述符 (11 维)
    f_cf3_features = _detect_F_CF3_features(ald_mol, am_mol)
    for j, val in enumerate(f_cf3_features):
        vec[23 + j] = val

    return vec


# ── 训练兼容接口 ─────────────────────────────────────────────

def compute_pair_violations(ald_smi: str, am_smi: str) -> Dict[str, float]:
    violations, _, _ = check_hard_constraints(ald_smi, am_smi)
    result = {"phenyl": 0.0, "symmetry": 0.0, "para": 0.0}
    for v in violations:
        if "无芳香原子" in v:
            result["phenyl"] = 1.0
        if "非中心对称" in v:
            result["symmetry"] = 1.0
        if "非对位" in v or "C3邻位" in v or "C3对位" in v:
            result["para"] = 1.0
    return result


DEFAULT_WEIGHTS = {"phenyl": 0.5, "symmetry": 0.5, "para": 0.4}


def chem_penalty_loss(preds, ald_smiles, am_smiles, weights=None,
                       threshold=0.5):
    import torch
    w = weights or DEFAULT_WEIGHTS
    violations = []
    for ald, am in zip(ald_smiles, am_smiles):
        v = compute_pair_violations(ald, am)
        total_v = sum(w.get(k, 0.0) * v.get(k, 0.0) for k in w)
        violations.append(total_v)
    v_tensor = torch.tensor(violations, dtype=torch.float32, device=preds.device)
    mask = (preds > threshold).float()
    return (mask * v_tensor).mean()


class ViolationCache:
    def __init__(self, ald_smiles_list, am_smiles_list, weights=None):
        import numpy as np
        self.weights = weights or DEFAULT_WEIGHTS
        self.scores = []
        self.details = []
        for ald, am in zip(ald_smiles_list, am_smiles_list):
            v = compute_pair_violations(ald, am)
            total = sum(self.weights.get(k, 0.0) * v.get(k, 0.0) for k in self.weights)
            self.scores.append(total)
            self.details.append(v)

    def to_tensor(self, indices, device="cpu"):
        import torch
        return torch.tensor([self.scores[i] for i in indices],
                           dtype=torch.float32, device=device)

    def mean_violation(self):
        import numpy as np
        return float(np.mean(self.scores))

    def violation_summary(self):
        import numpy as np
        arr = np.array(self.scores)
        return {
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "p90": float(np.percentile(arr, 90)),
            "nonzero_frac": float((arr > 0).mean()),
        }

    def rule_summary(self):
        summary = {}
        for rule in DEFAULT_WEIGHTS:
            vals = [d.get(rule, 0.0) for d in self.details]
            arr = np.array(vals)
            summary[rule] = {
                "mean": float(np.mean(arr)),
                "nonzero_frac": float((arr > 0).mean()),
            }
        return summary
