"""DFT 引擎单元测试：输出解析 / E_bind 计算 / 失败分类 / 3D 构象。

不依赖真实 xtb：_run_xtb 用 monkeypatch 伪造含 TOTAL ENERGY 的 stdout。
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

# 仿真实 xtb 输出片段（苯甲醛量级数值）
OUT_A = """\
 some banner
| TOTAL ENERGY           -15.878693712486 Eh   |
 * HOMO-LUMO GAP            5.017 eV
 molecular dipole:
                 x           y           z            tot (Debye)
   full:        0.010      -0.020       0.030       0.115
 normal termination of xtb
"""
OUT_B = """\
| TOTAL ENERGY           -10.121306287514 Eh   |
 * HOMO-LUMO GAP            7.500 eV
 molecular dipole:
                 x           y           z            tot (Debye)
   full:        0.000       0.000      -0.043       0.050
 normal termination of xtb
"""
OUT_C = """\
| TOTAL ENERGY           -26.012000000000 Eh   |
 * HOMO-LUMO GAP            4.200 eV
 molecular dipole:
                 x           y           z            tot (Debye)
   full:        0.100       0.200       0.300       1.234
 normal termination of xtb
"""
FAKE_XYZ = "3\ncomplex\nC 0.0 0.0 0.0\nN 1.4 0.0 0.0\nO 2.5 0.0 0.0\n"


@pytest.fixture()
def fake_xtb(monkeypatch, tmp_path):
    """伪造 _run_xtb：按调用顺序返回单体A / 单体B / 复合物的输出。"""
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    calls = []

    def _fake(xyz_block, args, cwd, timeout):
        calls.append((args, cwd, timeout))
        stdout = [OUT_A, OUT_B, OUT_C][len(calls) - 1]
        opt = FAKE_XYZ if len(calls) == 3 else None
        return stdout, opt

    monkeypatch.setattr(engine, "_run_xtb", _fake)
    monkeypatch.setattr(engine, "DEFAULT_TIMEOUT", {"gfnff": 60, "gfn2": 300})
    return calls


class TestParse:
    def test_parse_energy(self):
        assert engine.parse_energy(OUT_A) == pytest.approx(-15.878693712486)

    def test_parse_gap(self):
        assert engine.parse_gap_ev(OUT_A) == pytest.approx(5.017)

    def test_parse_dipole(self):
        assert engine.parse_dipole_debye(OUT_C) == pytest.approx(1.234)

    def test_parse_missing_returns_none(self):
        assert engine.parse_energy("no numbers here") is None
        assert engine.parse_gap_ev("no gap") is None
        assert engine.parse_dipole_debye("nothing") is None

    def test_gfnff_gap_absent(self):
        out = OUT_A.replace(" * HOMO-LUMO GAP            5.017 eV\n", "")
        assert engine.parse_gap_ev(out) is None


class TestCanonicalize:
    def test_canonical_same_for_equivalent(self):
        a = engine.canonicalize_smiles("c1ccccc1")
        b = engine.canonicalize_smiles("C1=CC=CC=C1")
        assert a is not None and a == b

    def test_invalid_returns_none(self):
        assert engine.canonicalize_smiles("not_a_smiles!!") is None
        assert engine.canonicalize_smiles("") is None


class TestComputeBinding:
    def test_ebind_and_descriptors(self, fake_xtb, tmp_path):
        r = engine.compute_binding("c1ccccc1", "C=O", method="gfn2",
                                   jobs_root=tmp_path)
        # E_bind = -26.012 - (-15.878693712486) - (-10.121306287514) = -0.012
        assert r["e_bind_hartree"] == pytest.approx(-0.012, abs=1e-9)
        assert r["e_bind_kcal"] == pytest.approx(-0.012 * 627.509, abs=1e-6)
        assert r["e_bind_kj"] == pytest.approx(-0.012 * 2625.5, abs=1e-5)
        assert r["energies_hartree"]["a"] == pytest.approx(-15.878693712486)
        assert r["energies_hartree"]["complex"] == pytest.approx(-26.012)
        assert r["gap_ev"]["a"] == pytest.approx(5.017)
        assert r["gap_ev"]["complex"] == pytest.approx(4.2)
        assert r["dipole_debye"]["b"] == pytest.approx(0.05)
        # 复合物几何来自伪造的 xtbopt.xyz
        assert r["complex_xyz"] == FAKE_XYZ
        # 三次 xtb 调用均带 --gfn 2 参数
        assert len(fake_xtb) == 3
        assert all(args == ["--gfn", "2"] for args, _, _ in fake_xtb)

    def test_gfnff_args(self, fake_xtb, tmp_path):
        engine.compute_binding("c1ccccc1", "C=O", method="gfnff",
                               jobs_root=tmp_path)
        assert all(args == ["--gfnff"] for args, _, _ in fake_xtb)

    def test_smiles_canonicalized_in_result(self, fake_xtb, tmp_path):
        r = engine.compute_binding("C1=CC=CC=C1", "C=O", method="gfn2",
                                   jobs_root=tmp_path)
        assert r["smiles_a"] == engine.canonicalize_smiles("c1ccccc1")

    def test_invalid_smiles_chinese_error(self, fake_xtb, tmp_path):
        with pytest.raises(engine.DftError, match="无法解析"):
            engine.compute_binding("xx!!", "C=O", jobs_root=tmp_path)

    def test_unknown_method(self, tmp_path):
        with pytest.raises(engine.DftError, match="未知方法档位"):
            engine.compute_binding("c1ccccc1", "C=O", method="b3lyp",
                                   jobs_root=tmp_path)

    def test_missing_engine_chinese_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(engine, "xtb_binary", lambda: None)
        with pytest.raises(engine.DftError, match="未安装计算引擎"):
            engine.compute_binding("c1ccccc1", "C=O", jobs_root=tmp_path)

    def test_timeout_chinese_error(self, fake_xtb, monkeypatch, tmp_path):
        def _timeout(*_a, **_k):
            raise engine.DftError("计算超时（超过 300 秒仍未完成）")
        monkeypatch.setattr(engine, "_run_xtb", _timeout)
        with pytest.raises(engine.DftError, match="超时"):
            engine.compute_binding("c1ccccc1", "C=O", jobs_root=tmp_path)

    def test_abnormal_termination_chinese_error(self, fake_xtb, monkeypatch,
                                                tmp_path):
        monkeypatch.setattr(engine, "_run_xtb",
                            lambda *a, **k: ("crash, no termination", None))
        with pytest.raises(engine.DftError, match="未找到总能量"):
            engine.compute_binding("c1ccccc1", "C=O", jobs_root=tmp_path)


class TestClassifyFailure:
    def test_unparametrized_element(self):
        msg = engine._classify_failure("error: no parameters for element Xx",
                                       "", 1)
        assert "元素" in msg

    def test_generic_nonconvergence(self):
        msg = engine._classify_failure("optimization stopped", "", 1)
        assert "未收敛" in msg or "失败" in msg


class TestEmbed:
    def test_monomer_xyz_real_rdkit(self):
        xyz = engine.embed_monomer_xyz("c1ccccc1")
        lines = xyz.strip().splitlines()
        assert int(lines[0]) == 12  # 苯加氢 12 原子
        assert len(lines) == 14

    def test_complex_xyz_has_both_molecules(self):
        xyz = engine.embed_complex_xyz("c1ccccc1", "C=O")
        n = int(xyz.strip().splitlines()[0])
        assert n == 12 + 4  # 苯(12H加氢) + 甲醛(4)
