"""DFT 引擎单元测试（2.0）：二聚体+X 管线 / 输出解析 / E_bind / 失败分类 / 3D 构象。

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

ALD = "O=Cc1ccccc1"          # 苯甲醛
AMINE = "Nc1ccccc1"          # 苯胺
DIMER = "C(=Nc1ccccc1)c1ccccc1"  # N-苄叉苯胺（canonical）

# 仿真实 xtb 输出片段
OUT_D = """\
 some banner
| TOTAL ENERGY           -15.878693712486 Eh   |
 * HOMO-LUMO GAP            5.017 eV
 molecular dipole:
                 x           y           z            tot (Debye)
   full:        0.010      -0.020       0.030       0.115
 normal termination of xtb
"""
OUT_X = """\
| TOTAL ENERGY           -10.121306287514 Eh   |
 * HOMO-LUMO GAP            7.500 eV
 molecular dipole:
                 x           y           z            tot (Debye)
   full:        0.000       0.000      -0.043       0.050
 normal termination of xtb
"""
OUT_C = """\
| TOTAL ENERGY           -31.700000000000 Eh   |
 * HOMO-LUMO GAP            4.200 eV
 molecular dipole:
                 x           y           z            tot (Debye)
   full:        0.100       0.200       0.300       1.234
 normal termination of xtb
"""
FAKE_XYZ = "3\ncomplex\nC 0.0 0.0 0.0\nN 1.4 0.0 0.0\nO 2.5 0.0 0.0\n"


@pytest.fixture()
def fake_xtb_by_dir(monkeypatch, tmp_path):
    """按子目录名决定输出：dimer → OUT_D，x → OUT_X，complex → OUT_C。

    自身堆积时只有 dimer + complex 两次调用，比按调用顺序伪造更可靠。
    """
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    calls = []

    def _fake(xyz_block, args, cwd, timeout):
        calls.append((args, cwd, timeout))
        stdout = {"dimer": OUT_D, "x": OUT_X, "complex": OUT_C}[cwd.name]
        opt = FAKE_XYZ if cwd.name == "complex" else None
        return stdout, opt

    monkeypatch.setattr(engine, "_run_xtb", _fake)
    return calls


class TestParse:
    def test_parse_energy(self):
        assert engine.parse_energy(OUT_D) == pytest.approx(-15.878693712486)

    def test_parse_gap(self):
        assert engine.parse_gap_ev(OUT_D) == pytest.approx(5.017)

    def test_parse_dipole(self):
        assert engine.parse_dipole_debye(OUT_C) == pytest.approx(1.234)

    def test_parse_missing_returns_none(self):
        assert engine.parse_energy("no numbers here") is None
        assert engine.parse_gap_ev("no gap") is None
        assert engine.parse_dipole_debye("nothing") is None

    def test_gfnff_gap_absent(self):
        out = OUT_D.replace(" * HOMO-LUMO GAP            5.017 eV\n", "")
        assert engine.parse_gap_ev(out) is None


class TestCanonicalize:
    def test_canonical_same_for_equivalent(self):
        a = engine.canonicalize_smiles("c1ccccc1")
        b = engine.canonicalize_smiles("C1=CC=CC=C1")
        assert a is not None and a == b

    def test_invalid_returns_none(self):
        assert engine.canonicalize_smiles("not_a_smiles!!") is None
        assert engine.canonicalize_smiles("") is None


class TestResolveX:
    def test_self_stack(self):
        smiles, desc, part = engine.resolve_x("self_stack", DIMER)
        assert smiles == DIMER
        assert "自身堆积" in desc
        assert part == "self_stack"

    def test_solvent(self):
        smiles, desc, part = engine.resolve_x(
            "solvent", DIMER, solvent_id="toluene")
        assert smiles == engine.canonicalize_smiles("Cc1ccccc1")
        assert "甲苯" in desc
        assert part == "solvent:toluene"

    def test_solvent_missing_id(self):
        with pytest.raises(engine.DftError, match="solvent_id"):
            engine.resolve_x("solvent", DIMER)

    def test_solvent_unknown_id(self):
        with pytest.raises(engine.DftError, match="未知溶剂"):
            engine.resolve_x("solvent", DIMER, solvent_id="benzene")

    def test_other_dimer(self):
        smiles, desc, part = engine.resolve_x(
            "other_dimer", DIMER,
            ald2_smiles="O=CC=O", amine2_smiles="Nc1ccc(N)cc1")
        assert "C=N" not in smiles or "=" in smiles  # 是合法 SMILES
        from rdkit import Chem
        assert Chem.MolFromSmiles(smiles) is not None
        assert "另一组单体" in desc
        assert part.startswith("other_dimer:")

    def test_other_dimer_missing_params(self):
        with pytest.raises(engine.DftError, match="ald2_smiles"):
            engine.resolve_x("other_dimer", DIMER, ald2_smiles="O=CC=O")

    def test_other_dimer_non_ald_amine(self):
        with pytest.raises(engine.DftError, match="另一组单体无法形成二聚体"):
            engine.resolve_x("other_dimer", DIMER,
                             ald2_smiles="c1ccccc1", amine2_smiles="Nc1ccccc1")

    def test_custom(self):
        smiles, desc, part = engine.resolve_x(
            "custom", DIMER, custom_smiles="CCO")
        assert smiles == engine.canonicalize_smiles("CCO")
        assert "自定义" in desc
        assert part == f"custom:{smiles}"

    def test_custom_missing(self):
        with pytest.raises(engine.DftError, match="custom_smiles"):
            engine.resolve_x("custom", DIMER)

    def test_custom_invalid_smiles(self):
        with pytest.raises(engine.DftError, match="无法解析"):
            engine.resolve_x("custom", DIMER, custom_smiles="xx!!")

    def test_unknown_x_type(self):
        with pytest.raises(engine.DftError, match="未知的 X 类型"):
            engine.resolve_x("dft_please", DIMER)


class TestComputeBinding:
    def test_self_stack_reuses_dimer_run(self, fake_xtb_by_dir, tmp_path):
        """自身堆积：X=D，只跑两次 xtb（二聚体 + 复合物）。"""
        hints: list[str] = []
        r = engine.compute_binding(ALD, AMINE, method="gfn2",
                                   on_stage=hints.append, jobs_root=tmp_path)
        # E_bind = -31.7 - (-15.878693712486) - (-15.878693712486)
        e_d = -15.878693712486
        assert r["e_bind_hartree"] == pytest.approx(-31.7 - 2 * e_d, abs=1e-9)
        assert len(fake_xtb_by_dir) == 2  # dimer + complex（X 复用）
        assert r["dimer_smiles"] == DIMER
        assert r["x_type"] == "self_stack"
        assert r["x_smiles"] == DIMER
        assert r["x_cache_part"] == "self_stack"
        assert "自身堆积" in r["x_description"]
        assert r["energies_hartree"]["dimer"] == pytest.approx(e_d)
        assert r["energies_hartree"]["x"] == pytest.approx(e_d)
        assert r["energies_hartree"]["complex"] == pytest.approx(-31.7)
        assert r["gap_ev"]["complex"] == pytest.approx(4.2)
        assert r["dipole_debye"]["x"] == pytest.approx(0.115)  # 复用二聚体
        assert r["complex_xyz"] == FAKE_XYZ
        assert r["smiles_a"] == engine.canonicalize_smiles(ALD)
        assert r["smiles_b"] == engine.canonicalize_smiles(AMINE)
        assert all(args == ["--gfn", "2"] for args, _, _ in fake_xtb_by_dir)

    def test_solvent_x(self, fake_xtb_by_dir, tmp_path):
        r = engine.compute_binding(ALD, AMINE, method="gfnff",
                                   x_type="solvent", solvent_id="dioxane",
                                   jobs_root=tmp_path)
        assert len(fake_xtb_by_dir) == 3
        assert r["x_smiles"] == engine.canonicalize_smiles("C1COCCO1")
        assert "二氧六环" in r["x_description"]
        assert r["x_cache_part"] == "solvent:dioxane"
        # E_bind = -31.7 - (-15.878693712486) - (-10.121306287514)
        assert r["e_bind_hartree"] == pytest.approx(
            -31.7 + 15.878693712486 + 10.121306287514, abs=1e-9)
        assert r["e_bind_kcal"] == pytest.approx(
            r["e_bind_hartree"] * 627.509, abs=1e-6)
        assert all(args == ["--gfnff"] for args, _, _ in fake_xtb_by_dir)

    def test_other_dimer_x(self, fake_xtb_by_dir, tmp_path):
        r = engine.compute_binding(
            ALD, AMINE, method="gfn2", x_type="other_dimer",
            ald2_smiles="O=CC=O", amine2_smiles="Nc1ccc(N)cc1",
            jobs_root=tmp_path)
        assert len(fake_xtb_by_dir) == 3
        assert "另一组单体" in r["x_description"]
        assert r["x_cache_part"].startswith("other_dimer:")
        assert r["x_request"]["ald2_smiles"] == "O=CC=O"
        assert r["x_request"]["amine2_smiles"] == "Nc1ccc(N)cc1"

    def test_custom_x(self, fake_xtb_by_dir, tmp_path):
        r = engine.compute_binding(ALD, AMINE, method="gfn2",
                                   x_type="custom", custom_smiles="CCO",
                                   jobs_root=tmp_path)
        assert len(fake_xtb_by_dir) == 3
        assert r["x_smiles"] == engine.canonicalize_smiles("CCO")
        assert "自定义" in r["x_description"]
        assert r["x_request"]["custom_smiles"] == "CCO"

    def test_multi_site_dimer_note(self, fake_xtb_by_dir, tmp_path):
        r = engine.compute_binding("O=Cc1ccc(C=O)cc1", "Nc1ccc(N)cc1",
                                   method="gfn2", jobs_root=tmp_path)
        assert r["dimer_multi_site"] is True
        assert "示意单点缩合" in (r["dimer_note"] or "")

    def test_large_system_hint(self, fake_xtb_by_dir, monkeypatch, tmp_path):
        monkeypatch.setattr(engine, "LARGE_SYSTEM_ATOMS", 1)
        hints: list[str] = []
        engine.compute_binding(ALD, AMINE, method="gfn2",
                               on_stage=hints.append, jobs_root=tmp_path)
        assert any("体系较大" in h and "耗时较长" in h for h in hints)

    def test_non_ald_amine_chinese_error(self, fake_xtb_by_dir, tmp_path):
        with pytest.raises(engine.DftError, match="二聚体生成失败"):
            engine.compute_binding("c1ccccc1", "Nc1ccccc1", jobs_root=tmp_path)

    def test_invalid_smiles_chinese_error(self, fake_xtb_by_dir, tmp_path):
        with pytest.raises(engine.DftError, match="无法解析"):
            engine.compute_binding("xx!!", AMINE, jobs_root=tmp_path)

    def test_unknown_method(self, tmp_path):
        with pytest.raises(engine.DftError, match="未知方法档位"):
            engine.compute_binding(ALD, AMINE, method="b3lyp",
                                   jobs_root=tmp_path)

    def test_unknown_x_type_chinese_error(self, fake_xtb_by_dir, tmp_path):
        with pytest.raises(engine.DftError, match="未知的 X 类型"):
            engine.compute_binding(ALD, AMINE, x_type="magic",
                                   jobs_root=tmp_path)

    def test_missing_engine_chinese_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(engine, "xtb_binary", lambda: None)
        with pytest.raises(engine.DftError, match="未安装计算引擎"):
            engine.compute_binding(ALD, AMINE, jobs_root=tmp_path)

    def test_timeout_chinese_error(self, fake_xtb_by_dir, monkeypatch, tmp_path):
        def _timeout(*_a, **_k):
            raise engine.DftError("计算超时（超过 300 秒仍未完成）")
        monkeypatch.setattr(engine, "_run_xtb", _timeout)
        with pytest.raises(engine.DftError, match="超时"):
            engine.compute_binding(ALD, AMINE, jobs_root=tmp_path)

    def test_abnormal_termination_chinese_error(self, fake_xtb_by_dir,
                                                monkeypatch, tmp_path):
        monkeypatch.setattr(engine, "_run_xtb",
                            lambda *a, **k: ("crash, no termination", None))
        with pytest.raises(engine.DftError, match="未找到总能量"):
            engine.compute_binding(ALD, AMINE, jobs_root=tmp_path)


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

    def test_complex_xyz_dimer_self_stack(self):
        """二聚体·二聚体复合物初猜原子数翻倍。"""
        xyz_d = engine.embed_monomer_xyz(DIMER)
        n_d = int(xyz_d.strip().splitlines()[0])
        xyz_c = engine.embed_complex_xyz(DIMER, DIMER)
        assert int(xyz_c.strip().splitlines()[0]) == 2 * n_d


class TestXyzAtomCount:
    def test_count(self):
        assert engine._xyz_atom_count(FAKE_XYZ) == 3

    def test_bad_input(self):
        assert engine._xyz_atom_count("not xyz") == 0
