"""条件推荐：专家规则引擎。

基于单体化学特征推断合理的实验条件。由于反应条件数据缺失 86-90%，
条件推荐主要依靠领域规则而非 ML。
"""

from __future__ import annotations

from rdkit import Chem


def _contains_substructure(smiles: str, smarts: str) -> bool:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return False
    patt = Chem.MolFromSmarts(smarts)
    if patt is None:
        return False
    return mol.HasSubstructMatch(patt)


def _count_substructures(smiles: str, smarts: str) -> int:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0
    patt = Chem.MolFromSmarts(smarts)
    if patt is None:
        return 0
    return len(mol.GetSubstructMatches(patt))


def classify_monomer(ald_smiles: str, amine_smiles: str) -> dict:
    """根据单体结构分类，用于推荐条件。"""
    # 检测 F/CF3 取代
    has_f_ald = _contains_substructure(ald_smiles, "[F]")
    has_cf3_ald = _contains_substructure(ald_smiles, "C(F)(F)F")
    has_f_amine = _contains_substructure(amine_smiles, "[F]")
    has_cf3_amine = _contains_substructure(amine_smiles, "C(F)(F)F")

    # 检测酰肼
    has_hydrazide_ald = _contains_substructure(ald_smiles, "C(=O)NN")
    has_hydrazide_amine = _contains_substructure(amine_smiles, "C(=O)NN")

    # 检测反应位点数
    n_ald_sites = _count_substructures(ald_smiles, "[CX3](=O)")
    n_amine_sites = _count_substructures(amine_smiles, "[NX3;H2]")

    # 醛基/胺基当量比
    if n_ald_sites > 0 and n_amine_sites > 0:
        ratio = n_ald_sites / n_amine_sites
    else:
        ratio = 1.0

    return {
        "has_f": has_f_ald or has_f_amine,
        "has_cf3": has_cf3_ald or has_cf3_amine,
        "has_hydrazide": has_hydrazide_ald or has_hydrazide_amine,
        "fluorinated": has_f_ald or has_cf3_ald or has_f_amine or has_cf3_amine,
        "n_ald_sites": n_ald_sites,
        "n_amine_sites": n_amine_sites,
        "site_ratio": ratio,
        "topology": _topology_name(n_ald_sites, n_amine_sites),
    }


def _topology_name(n_ald: int, n_amine: int) -> str:
    """根据反应位点数命名拓扑。"""
    if n_ald == 3 and n_amine == 2:
        return "C3+C2 (hcb network)"
    elif n_ald == 2 and n_amine == 3:
        return "C2+C3 (hcb network)"
    elif n_ald == 3 and n_amine == 3:
        return "C3+C3 (honeycomb)"
    elif n_ald == 2 and n_amine == 2:
        return "C2+C2 (linear)"
    else:
        return f"C{n_ald}+C{n_amine}"


def recommend_conditions(ald_smiles: str, amine_smiles: str) -> dict:
    """基于规则推荐实验条件。"""
    cls = classify_monomer(ald_smiles, amine_smiles)

    # 默认条件
    conditions = {
        "method": "液-液界面法",
        "solvent_system": "水 / 二氯甲烷 (1:1)",
        "temperature": "室温 (25°C)",
        "time": "24-48 h",
        "catalyst": "乙酸水溶液 (6.0 M)",
        "catalyst_volume": "0.2 mL",
        "stoichiometry": "1:1 (醛:胺)",
        "additive": "无",
        "notes": "常规亚胺 COF 界面合成条件",
    }

    if cls["fluorinated"]:
        conditions.update({
            "method": "溶剂热法（固-液/气界面）",
            "solvent_system": "甲苯 / 氯仿 (6:4)",
            "temperature": "120 °C",
            "time": "2-3 天",
            "catalyst": "苯胺 (3-5 eq) + 乙酸 (6.0 M)",
            "catalyst_volume": "0.2 mL",
            "stoichiometry": "1:1 (醛:胺)",
            "additive": "苯胺调制剂",
            "notes": "含 F/CF3 单体疏水性强，需甲苯/氯仿体系和苯胺调制促进成膜。",
        })
    elif cls["has_hydrazide"]:
        conditions.update({
            "method": "溶剂热法",
            "solvent_system": "BTF / 二氧六环 (9:1 ~ 6:4)",
            "temperature": "120 °C",
            "time": "3-5 天",
            "catalyst": "乙酸 (6.0 M)",
            "catalyst_volume": "0.2-0.25 mL",
            "stoichiometry": "1:1 (醛:胺)",
            "additive": "无",
            "notes": "酰肼 COF 合成需非质子极性溶剂体系。",
        })
    else:
        # 常规亚胺体系
        if cls["n_ald_sites"] >= 3 and cls["n_amine_sites"] >= 2:
            conditions.update({
                "method": "溶剂热法",
                "solvent_system": "均三甲苯 / 二氧六环 (1:1)",
                "temperature": "120 °C",
                "time": "3 天",
                "catalyst": "乙酸 (6.0 M)",
                "catalyst_volume": "0.2 mL",
                "stoichiometry": "1:1 (醛:胺)",
                "notes": "C3+C2 拓扑适合形成 2D COF 网络，溶剂热条件利于结晶。",
            })

    conditions["classification"] = cls
    return conditions


if __name__ == "__main__":
    ald = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
    amine = "Nc1ccc(N)cc1"
    print(recommend_conditions(ald, amine))
