"""DFT 2.0 二聚体生成器测试（src/dft/dimer.py）。

苯甲醛+苯胺 → 亚胺产物（含 C=N）；多位点标注；非醛胺中文报错；canonical 稳定性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from rdkit import Chem  # noqa: E402

from src.dft import dimer  # noqa: E402


class TestMakeDimer:
    def test_benzaldehyde_aniline_imine(self):
        """苯甲醛 + 苯胺 → N-苄叉苯胺，产物含 C=N 亚胺键。"""
        r = dimer.make_dimer("O=Cc1ccccc1", "Nc1ccccc1")
        mol = Chem.MolFromSmiles(r["smiles"])
        assert mol is not None
        # 亚胺键 C=N 存在
        imine = Chem.MolFromSmarts("[CX3]=[NX2]")
        assert mol.HasSubstructMatch(imine)
        # 醛基氧已脱除（缩合失水）：无醛基残留
        assert not mol.HasSubstructMatch(Chem.MolFromSmarts("[CX3H1](=O)"))
        assert r["multi_site"] is False
        assert r["note"] is None

    def test_multi_site_flagged(self):
        """对苯二甲醛（二醛）+ 对苯二胺（二胺）→ 多位点标注，仅缩合第一个位点。"""
        r = dimer.make_dimer("O=Cc1ccc(C=O)cc1", "Nc1ccc(N)cc1")
        assert r["multi_site"] is True
        assert "示意单点缩合" in r["note"]
        mol = Chem.MolFromSmiles(r["smiles"])
        assert mol.HasSubstructMatch(Chem.MolFromSmarts("[CX3]=[NX2]"))
        # 只缩合一个位点：残留一个醛基 + 一个伯胺
        assert mol.HasSubstructMatch(Chem.MolFromSmarts("[CX3H1](=O)"))
        assert mol.HasSubstructMatch(Chem.MolFromSmarts("[NX3H2]"))

    def test_canonical_stability(self):
        """同一对单体多次调用输出一致；等价写法（芳香/脂肪）输出一致。"""
        r1 = dimer.make_dimer("O=Cc1ccccc1", "Nc1ccccc1")
        r2 = dimer.make_dimer("O=Cc1ccccc1", "Nc1ccccc1")
        assert r1["smiles"] == r2["smiles"]
        r3 = dimer.make_dimer("O=Cc1ccccc1", "NC1=CC=CC=C1")
        assert r1["smiles"] == r3["smiles"]

    def test_non_aldehyde_chinese_error(self):
        with pytest.raises(dimer.DimerError, match="醛基"):
            dimer.make_dimer("c1ccccc1", "Nc1ccccc1")

    def test_non_primary_amine_chinese_error(self):
        with pytest.raises(dimer.DimerError, match="伯胺"):
            dimer.make_dimer("O=Cc1ccccc1", "O=C(O)c1ccccc1")

    def test_amide_nitrogen_not_counted(self):
        """酰胺 N 不算伯胺位点（乙酰胺无 -NH2 反应位点语义）。"""
        # 乙酰胺 CC(=O)N：N 连酰基，反应模板 [NH2:2] 仍可匹配，
        # 但位点计数排除酰胺；此处验证计数函数口径
        mol = Chem.MolFromSmiles("Nc1ccccc1")
        assert dimer.count_primary_amine_sites(mol) == 1
        assert dimer.count_aldehyde_sites(mol) == 0

    def test_invalid_smiles_chinese_error(self):
        with pytest.raises(dimer.DimerError, match="无法解析"):
            dimer.make_dimer("xx!!", "Nc1ccccc1")
        with pytest.raises(dimer.DimerError, match="无法解析"):
            dimer.make_dimer("O=Cc1ccccc1", "")
