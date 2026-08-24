"""DFT 真实冒烟测试：vendor/xtb 存在时端到端跑一遍（苯 + 甲醛，GFN2-xTB）。

跳过条件：vendor/xtb/bin/xtb.exe 不存在（如 CI 无该资产）。
小分子 × GFN2 通常 10~60 秒内完成；本测试验证管线端到端：
构象生成 → 三次 xtb --opt → 能量/gap/偶极解析 → E_bind。
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


@pytest.mark.skipif(XTB is None, reason="vendor/xtb 二进制不存在，跳过真实冒烟")
def test_smoke_benzene_formaldehyde_gfn2(tmp_path):
    hints: list[str] = []
    r = engine.compute_binding(
        "c1ccccc1", "C=O", method="gfn2",
        on_stage=hints.append, jobs_root=tmp_path)

    # 苯·甲醛色散复合物应表现为吸引（负结合能），且量级合理（< 50 kcal/mol）
    assert r["e_bind_kcal"] < 0
    assert abs(r["e_bind_kcal"]) < 50
    # GFN2 有轨道信息：三者都应给出 gap 与偶极矩
    for key in ("a", "b", "complex"):
        assert r["gap_ev"][key] is not None and r["gap_ev"][key] > 0
        assert r["dipole_debye"][key] is not None
        assert r["energies_hartree"][key] < 0
    # 复合物优化后几何可用
    n_atoms = int(r["complex_xyz"].strip().splitlines()[0])
    assert n_atoms == 16  # 苯(12) + 甲醛(4)
    # 阶段进度回调覆盖主要阶段
    assert any("单体 A" in h for h in hints)
    assert any("复合物" in h for h in hints)


@pytest.mark.skipif(XTB is None, reason="vendor/xtb 二进制不存在，跳过真实冒烟")
def test_smoke_gfnff_fast_tier(tmp_path):
    r = engine.compute_binding("c1ccccc1", "C=O", method="gfnff",
                               jobs_root=tmp_path)
    assert r["e_bind_kcal"] < 0
    assert abs(r["e_bind_kcal"]) < 50
    # GFN-FF 无力场轨道 → gap 为 None 属预期（不强制）
