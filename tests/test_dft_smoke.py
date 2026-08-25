"""DFT 真实冒烟测试（2.0）：vendor/xtb 存在时端到端跑一遍。

苯甲醛 + 苯胺 → N-苄叉苯胺二聚体，自身堆积（二聚体·二聚体），GFN-FF 快速档。
跳过条件：vendor/xtb/bin/xtb.exe 不存在（如 CI 无该资产）。
二聚体(26 原子)·二聚体(52 原子) GFN-FF 通常 1 分钟内完成；验证管线端到端：
亚胺缩合 → 构象生成 → 两次 xtb --opt（X 复用二聚体）→ 能量解析 → E_bind。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.dft import engine  # noqa: E402

XTB = engine.xtb_binary()

ALD = "O=Cc1ccccc1"    # 苯甲醛
AMINE = "Nc1ccccc1"    # 苯胺


@pytest.mark.skipif(XTB is None, reason="vendor/xtb 二进制不存在，跳过真实冒烟")
def test_smoke_benzaldehyde_aniline_self_stack_gfnff(tmp_path):
    hints: list[str] = []
    r = engine.compute_binding(
        ALD, AMINE, method="gfnff", x_type="self_stack",
        on_stage=hints.append, jobs_root=tmp_path)

    # 二聚体与 X 字段齐备
    assert r["dimer_smiles"]
    assert r["x_type"] == "self_stack"
    assert r["x_smiles"] == r["dimer_smiles"]
    assert "自身堆积" in r["x_description"]

    # 二聚体·二聚体堆积应表现为吸引（负结合能），且量级合理（< 50 kcal/mol）
    assert r["e_bind_kcal"] < 0
    assert abs(r["e_bind_kcal"]) < 50

    # 能量均为负；GFN-FF 无轨道 → gap 为 None 属预期（不强制）
    for key in ("dimer", "x", "complex"):
        assert r["energies_hartree"][key] < 0
    # 自身堆积：E(X) 复用 E(二聚体)
    assert r["energies_hartree"]["x"] == r["energies_hartree"]["dimer"]

    # 复合物优化后几何可用：原子数 = 二聚体加氢原子数 ×2
    n_atoms = int(r["complex_xyz"].strip().splitlines()[0])
    xyz_d = engine.embed_monomer_xyz(r["dimer_smiles"])
    n_d = int(xyz_d.strip().splitlines()[0])
    assert n_atoms == r["complex_atom_count"] == 2 * n_d

    # 阶段进度回调覆盖主要阶段
    assert any("二聚体" in h for h in hints)
    assert any("复合物" in h for h in hints)


@pytest.mark.skipif(XTB is None, reason="vendor/xtb 二进制不存在，跳过真实冒烟")
def test_smoke_solvent_x_gfnff(tmp_path):
    """溶剂 X：苯甲醛+苯胺二聚体 · 甲苯，GFN-FF 端到端。"""
    r = engine.compute_binding(
        ALD, AMINE, method="gfnff", x_type="solvent", solvent_id="toluene",
        jobs_root=tmp_path)
    assert r["x_description"].startswith("溶剂分子")
    assert r["x_smiles"] == engine.canonicalize_smiles("Cc1ccccc1")
    assert abs(r["e_bind_kcal"]) < 50
