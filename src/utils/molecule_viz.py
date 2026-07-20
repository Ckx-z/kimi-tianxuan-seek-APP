"""分子结构渲染工具：SMILES → 2D 结构图（PNG / PIL Image）。

供 App 前端和 Word 报告复用。所有函数对非法 SMILES 优雅降级：
解析失败返回 None，不抛异常，不影响预测/报告主流程。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from rdkit import Chem
from rdkit.Chem import AllChem, Draw

# 醛 + 伯胺 → 亚胺（C=N）缩合反应；O 与 H2O 不计入产物模板
_IMINE_RXN_SMARTS = "[C:1]=O.[NH2:2]>>[C:1]=[N:2]"


def mol_from_smiles(smiles: str) -> Optional[Chem.Mol]:
    """解析 SMILES，失败返回 None。"""
    if not smiles or not smiles.strip():
        return None
    try:
        return Chem.MolFromSmiles(smiles.strip())
    except Exception:
        return None


def smiles_to_image(smiles: str, size: tuple[int, int] = (450, 300)):
    """SMILES 渲染为 PIL Image；解析失败返回 None。"""
    mol = mol_from_smiles(smiles)
    if mol is None:
        return None
    try:
        return Draw.MolToImage(mol, size=size)
    except Exception:
        return None


def smiles_to_png_file(smiles: str, out_path: str | Path,
                       size: tuple[int, int] = (900, 600)) -> Optional[Path]:
    """SMILES 渲染并保存为 PNG 文件；失败返回 None。"""
    img = smiles_to_image(smiles, size=size)
    if img is None:
        return None
    try:
        out_path = Path(out_path)
        img.save(out_path, format="PNG")
        return out_path
    except Exception:
        return None


def render_imine_product(ald_smiles: str, amine_smiles: str,
                         size: tuple[int, int] = (600, 300)):
    """渲染醛胺缩合产物骨架图（取第一个亚胺化产物作为示意）。

    多醛/多胺单体只示意第一个缩合位点；反应失败时返回 None。
    """
    ma = mol_from_smiles(ald_smiles)
    mm = mol_from_smiles(amine_smiles)
    if ma is None or mm is None:
        return None
    try:
        rxn = AllChem.ReactionFromSmarts(_IMINE_RXN_SMARTS)
        products = rxn.RunReactants((ma, mm))
        if not products:
            return None
        prod = products[0][0]
        Chem.SanitizeMol(prod)
        return Draw.MolToImage(prod, size=size)
    except Exception:
        return None


def png_temp_file(smiles: str, prefix: str = "mol",
                  size: tuple[int, int] = (900, 600)) -> Optional[Path]:
    """渲染到系统临时目录的 PNG 文件（调用方负责用后清理）。"""
    fd, tmp = tempfile.mkstemp(prefix=prefix + "_", suffix=".png")
    try:
        import os
        os.close(fd)
    except OSError:
        pass
    return smiles_to_png_file(smiles, tmp, size=size)
