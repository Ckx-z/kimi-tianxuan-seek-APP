"""实验方案卡生成（P2 后端，页①/③支撑）。

按侯老师法（v3.9 方案）模板生成可核对方案卡：
- conditions：默认条件参数（标注"按 v3.9 方案默认值，可按组调整"）
- steps：加料顺序与操作要点
- checklist：防错清单（来自实验 ABCDEF 的真实失败教训）
- monomer_hints：单体特异提示（RDKit 检测 F/CF3、酰肼、大芳香体系）

纯模板 + 结构检测，不调用预测模型；RDKit 不可用时 hints 退化为空。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_PATH = PROJECT_ROOT / "data" / "builtin_monomers.json"

TEMPLATE_NAME = "侯老师法（v3.9 方案）"
DEFAULTS_NOTE = "按 v3.9 方案默认值，可按组调整"

# ---------------------------------------------------------------- 模板

_CONDITIONS = {
    "solvent": "甲苯（或氯仿）",
    "modulator": "苯胺（调制剂，用量按方案）",
    "catalyst": "6M 乙酸",
    "temperature_c": 120,
    "time_days": "2-5",
    "vessel": "35 mL Pyrex 管",
}

_STEPS = [
    "按化学计量比称取醛单体与胺单体，备用",
    "醛单体 + 苯胺调制剂加入 35 mL Pyrex 管",
    "加入溶剂（甲苯或氯仿），超声/摇晃至溶解完全",
    "加入胺单体（或胺单体溶液）",
    "最后加入 6M 乙酸催化剂",
    "密封（注意预留排气），置于 120 °C 烘箱反应 2–5 天",
    "冷却至室温后开管，收集产物，洗涤、干燥，检查成膜情况",
]

# 防错清单：全部来自真实失败教训（实验 ABCDEF）
_CHECKLIST = [
    {
        "item": "乙酸浓度核对",
        "detail": "必须用 6M 乙酸，6M≠18M；误用冰醋酸（≈18M）已导致过实验失败",
    },
    {
        "item": "溶解完全性检查",
        "detail": "升温前确认单体完全溶解；浑浊时补溶剂或继续超声，切勿带渣升温",
    },
    {
        "item": "苯胺量按方案",
        "detail": "调制剂苯胺用量严格按方案量取，不凭感觉加减",
    },
    {
        "item": "密封与排气",
        "detail": "Pyrex 管密封可靠并预留排气空间，防止漏气或压力爆管",
    },
    {
        "item": "加料顺序",
        "detail": "先醛+苯胺，后胺，最后乙酸；顺序错乱会导致局部爆聚",
    },
]


# ---------------------------------------------------------------- 单体检测

def _mol(smiles: str):
    """SMILES → RDKit Mol；解析失败/RDKit 不可用返回 None。"""
    if not smiles or not isinstance(smiles, str):
        return None
    try:
        from rdkit import Chem

        return Chem.MolFromSmiles(smiles.strip())
    except Exception:
        return None


def _aromatic_ring_count(mol) -> int:
    ri = mol.GetRingInfo()
    return sum(
        1
        for ring in ri.AtomRings()
        if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring)
    )


def _monomer_hints(ald_smiles: str, amine_smiles: str) -> list[str]:
    """单体特异提示：F/CF3 溶解性、酰肼（腙键体系）、大芳香体系。"""
    hints: list[str] = []
    mols = [m for m in (_mol(ald_smiles), _mol(amine_smiles)) if m is not None]
    if not mols:
        return hints

    try:
        from rdkit import Chem

        # 1. 含氟（F/CF3）→ 溶解性提示
        has_f = any(
            any(a.GetSymbol() == "F" for a in m.GetAtoms()) for m in mols
        )
        if has_f:
            hints.append(
                "含氟单体（F/CF3）：在甲苯/氯仿中溶解性通常偏差，"
                "建议先小量预溶确认，必要时超声助溶或换 BTF/二氧六环；"
                "含氟体系成膜窗口可能更窄"
            )

        # 2. 酰肼 → 腙键体系提示 + 模型不适用提醒
        hydrazide = Chem.MolFromSmarts("C(=O)NN")
        if any(m.HasSubstructMatch(hydrazide) for m in mols):
            hints.append(
                "检测到酰肼结构：该组合属于腙键体系，模型不适用（OOD 标记）；"
                "本方案卡仅按侯老师法模板给出，条件请以文献为准"
            )

        # 3. 大芳香体系（≥3 个芳环）→ 可能需要更长反应时间
        if any(_aromatic_ring_count(m) >= 3 for m in mols):
            hints.append(
                "大芳香刚性体系：π–π 堆积强、溶解与扩散慢，"
                "可能需要更长反应时间（建议取上限 4–5 天），并注意预聚体提前析出"
            )
    except Exception as exc:
        logger.warning("单体结构检测异常: %s", exc)
    return hints


def _monomer_obj(smiles: str, name: str = "") -> dict:
    """单体对象 {smiles, cas, name}，CAS/name 从内置库反查（尽力而为）。"""
    smiles = (smiles or "").strip()
    cas, lib_name = "", ""
    mol = _mol(smiles)
    if mol is not None:
        try:
            from rdkit import Chem

            canon = Chem.MolToSmiles(mol)
            for m in json.loads(BUILTIN_PATH.read_text(encoding="utf-8")):
                mm = _mol(m.get("smiles", ""))
                if mm is not None and Chem.MolToSmiles(mm) == canon:
                    cas, lib_name = m.get("cas", ""), m.get("name", "")
                    break
        except Exception as exc:
            logger.warning("内置库反查失败: %s", exc)
    return {"smiles": smiles, "cas": cas, "name": (name or "").strip() or lib_name}


# ---------------------------------------------------------------- 主入口

def generate_plan_card(
    aldehyde_smiles: str,
    amine_smiles: str,
    ald_name: str = "",
    amine_name: str = "",
) -> dict:
    """生成侯老师法实验方案卡。

    返回 {template, aldehyde, amine, conditions, defaults_note, steps,
    checklist, monomer_hints, generated_at}；输入无法解析时仍可返回
    模板卡（monomer_hints 为空），不抛异常。
    """
    return {
        "template": TEMPLATE_NAME,
        "aldehyde": _monomer_obj(aldehyde_smiles, ald_name),
        "amine": _monomer_obj(amine_smiles, amine_name),
        "conditions": dict(_CONDITIONS),
        "defaults_note": DEFAULTS_NOTE,
        "steps": list(_STEPS),
        "checklist": [dict(c) for c in _CHECKLIST],
        "monomer_hints": _monomer_hints(aldehyde_smiles, amine_smiles),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
