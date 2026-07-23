"""条件推荐：案例匹配器。

从实验参考库中匹配最相似的历史案例。
由于 Word 文档解析复杂，MVP 阶段使用简化案例库（从 main_template.docx 提取的方案信息）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
REFS_DIR = PROJECT_ROOT / "data" / "experimental_refs"


# 内置简化案例库（从 main_template.docx 提取的关键实验组）
# 未来可以从 Word 文档自动提取
CASE_DATABASE = [
    {
        "case_id": "G1",
        "description": "TAPT + CF3 二醛",
        "ald_smarts": "[C,c;F]",
        "amine_smarts": "c",
        "method": "固-液/气界面法（壁面预涂覆）",
        "solvent": "甲苯 / 氯仿",
        "solvent_ratio": "6:4 ~ 2:8",
        "temperature": "120 °C",
        "time": "2-3 天",
        "additive": "苯胺 3-5 eq",
        "catalyst": "乙酸 6.0 M, 0.20 mL",
        "notes": "CF3 二醛与 TAPT 形成疏水 COF 膜，需苯胺调制。",
    },
    {
        "case_id": "G2",
        "description": "TAPT + F 二醛",
        "ald_smarts": "[F]",
        "amine_smarts": "c",
        "method": "固-液/气界面法",
        "solvent": "甲苯 / 氯仿",
        "solvent_ratio": "6:4 ~ 3:7",
        "temperature": "120 °C",
        "time": "2-3 天",
        "additive": "苯胺 3-5 eq",
        "catalyst": "乙酸 6.0 M, 0.20 mL",
        "notes": "F 二醛极性略强于 CF3，甲苯/氯仿比例可调整。",
    },
    {
        "case_id": "G3",
        "description": "TFPT + CF3 二胺",
        "ald_smarts": "c",
        "amine_smarts": "[C,c;F]",
        "method": "固-液/气界面法",
        "solvent": "甲苯 / 氯仿",
        "solvent_ratio": "6:4",
        "temperature": "120 °C",
        "time": "2 天",
        "additive": "苯胺 3 eq",
        "catalyst": "乙酸 6.0 M, 0.20 mL",
        "notes": "CF3 在胺侧，需控制胺溶解度。",
    },
    {
        "case_id": "G4",
        "description": "TFPT + F 二胺",
        "ald_smarts": "c",
        "amine_smarts": "[F]",
        "method": "固-液/气界面法",
        "solvent": "甲苯 / 氯仿",
        "solvent_ratio": "6:4 ~ 4:6",
        "temperature": "120 °C",
        "time": "2-3 天",
        "additive": "苯胺 3-5 eq",
        "catalyst": "乙酸 6.0 M, 0.20-0.25 mL",
        "notes": "F 在胺侧，苯胺当量可稍高。",
    },
    {
        "case_id": "G5",
        "description": "TFPT + 全氟酰肼",
        "ald_smarts": "C(=O)NN",
        "amine_smarts": "c",
        "method": "溶剂热法",
        "solvent": "BTF / 二氧六环",
        "solvent_ratio": "9:1 ~ 6:4",
        "temperature": "120 °C",
        "time": "3-5 天",
        "additive": "苯胺 3-5 eq",
        "catalyst": "乙酸 6.0 M, 0.20-0.25 mL",
        "notes": "酰肼体系形成腙键，需 BTF/二氧六环。",
    },
    {
        "case_id": "HOU_BASE",
        "description": "侯老师基准：扩散/调制剂双介导固-液/气界面法",
        "ald_smarts": "c",
        "amine_smarts": "c",
        "method": "固-液/气界面法",
        "solvent": "二氧六环 / 水 (1:1)",
        "solvent_ratio": "1:1",
        "temperature": "90-120 °C",
        "time": "2-3 天",
        "additive": "苯胺 5-10 eq",
        "catalyst": "乙酸 6.0 M, 0.2 mL",
        "notes": "经典方法，适用多数亚胺 COF 膜。",
    },
]


def _morgan_fingerprint(smiles: str, radius: int = 2, n_bits: int = 1024) -> tuple:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)


def _substructure_match_score(smiles: str, smarts: str | None) -> float:
    if smarts is None or smarts == "c":
        return 0.0
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0.0
    patt = Chem.MolFromSmarts(smarts)
    if patt is None:
        return 0.0
    if mol.HasSubstructMatch(patt):
        return 1.0
    return 0.0


def match_case(ald_smiles: str, amine_smiles: str) -> dict:
    """匹配最相似的历史案例。"""
    scores = []
    for case in CASE_DATABASE:
        score = 0.0
        score += _substructure_match_score(ald_smiles, case.get("ald_smarts")) * 0.5
        score += _substructure_match_score(amine_smiles, case.get("amine_smarts")) * 0.5
        scores.append((case, score))

    # 按分数排序，返回最高分的案例
    scores.sort(key=lambda x: x[1], reverse=True)
    best_case = scores[0][0]
    best_score = scores[0][1]

    return {
        "matched_case": best_case["case_id"],
        "similarity_score": best_score,
        "description": best_case["description"],
        "method": best_case["method"],
        "solvent": best_case["solvent"],
        "solvent_ratio": best_case["solvent_ratio"],
        "temperature": best_case["temperature"],
        "time": best_case["time"],
        "additive": best_case["additive"],
        "catalyst": best_case["catalyst"],
        "notes": best_case["notes"],
    }


if __name__ == "__main__":
    ald = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
    amine = "Nc1ccc(N)cc1"
    print(match_case(ald, amine))
