"""缩合二聚体生成器：醛 + 伯胺 → 亚胺（C=N）二聚体 SMILES。

DFT 2.0 的科学对象是「两单体缩合形成的二聚体」与第三物质 X 的结合能；
本模块负责从单体 SMILES 稳定地产出二聚体 SMILES：

- 反应模板复用 molecule_viz.render_imine_product 的亚胺缩合 SMARTS，
  取第一个缩合位点（与产物骨架图口径一致）；
- 多位点单体（二醛/二胺等）只缩合第一个位点，并以 multi_site/note
  标注「示意单点缩合」；
- 非醛胺体系 / 无法缩合 → DimerError（中文原因）；
- 输出经 RDKit canonical SMILES 规范化，同一对单体输出稳定。
"""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import AllChem

# 醛 + 伯胺 → 亚胺（C=N）缩合；与 molecule_viz._IMINE_RXN_SMARTS 同一模板
_IMINE_RXN_SMARTS = "[C:1]=O.[NH2:2]>>[C:1]=[N:2]"
# 醛基（脂肪/芳香醛 [CX3H1]=O）与伯胺（[NX3H2]）位点识别
_ALDEHYDE_SMARTS = "[CX3H1](=O)"
_PRIMARY_AMINE_SMARTS = "[NX3H2;!$(NC=O)]"  # 排除酰胺 N

MULTI_SITE_NOTE = "示意单点缩合：多位点单体仅缩合第一个位点"


class DimerError(Exception):
    """二聚体生成失败的统一异常，message 为面向用户的中文原因。"""


def _parse(smiles: str, role: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles((smiles or "").strip()) if smiles else None
    if mol is None:
        raise DimerError(f"{role}的 SMILES 无法解析：{(smiles or '')[:80]}")
    # 先 canonical 化再反应：输入写法不同会改变原子序，
    # 进而改变「第一个缩合位点」的选择；统一从 canonical 原子序出发，
    # 保证同一对单体（任意等价写法）输出稳定。
    return Chem.MolFromSmiles(Chem.MolToSmiles(mol))


def count_aldehyde_sites(mol: Chem.Mol) -> int:
    """醛基（-CHO）位点数。"""
    return len(mol.GetSubstructMatches(Chem.MolFromSmarts(_ALDEHYDE_SMARTS)))


def count_primary_amine_sites(mol: Chem.Mol) -> int:
    """伯胺（-NH2，非酰胺）位点数。"""
    return len(mol.GetSubstructMatches(Chem.MolFromSmarts(_PRIMARY_AMINE_SMARTS)))


def make_dimer(ald_smiles: str, amine_smiles: str) -> dict:
    """醛/胺单体 SMILES → 亚胺缩合二聚体。

    Returns:
        {"smiles": canonical 二聚体 SMILES,
         "multi_site": 是否多位点单体（仅示意第一个位点缩合）,
         "note": 多位点时的中文标注，否则 None}

    Raises:
        DimerError: SMILES 无法解析 / 非醛胺体系 / 反应失败（中文原因）
    """
    ald = _parse(ald_smiles, "醛单体")
    amine = _parse(amine_smiles, "胺单体")

    n_ald = count_aldehyde_sites(ald)
    n_amine = count_primary_amine_sites(amine)
    if n_ald == 0:
        raise DimerError(
            "醛单体中未找到醛基（-CHO）：当前仅支持醛 + 伯胺的亚胺缩合体系")
    if n_amine == 0:
        raise DimerError(
            "胺单体中未找到伯胺基（-NH2）：当前仅支持醛 + 伯胺的亚胺缩合体系")

    try:
        rxn = AllChem.ReactionFromSmarts(_IMINE_RXN_SMARTS)
        products = rxn.RunReactants((ald, amine))
    except Exception as exc:
        raise DimerError(f"缩合反应模板执行失败：{type(exc).__name__}: {exc}")
    if not products:
        raise DimerError("该醛-胺组合无法发生亚胺缩合（未匹配到反应位点）")

    prod = products[0][0]
    try:
        Chem.SanitizeMol(prod)
    except Exception:
        raise DimerError("缩合产物化学校验失败（价位/结构不合理），请检查单体结构")

    canon = Chem.MolToSmiles(prod)
    if not canon or Chem.MolFromSmiles(canon) is None:
        raise DimerError("缩合产物规范化失败，请检查单体结构")

    multi_site = n_ald > 1 or n_amine > 1
    return {
        "smiles": canon,
        "multi_site": multi_site,
        "note": MULTI_SITE_NOTE if multi_site else None,
    }
