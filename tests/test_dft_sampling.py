"""DFT Monte Carlo 取向采样 / 基序模板 / xTB 分级筛选 / 采样缓存口径测试。

不依赖真实 xtb：筛选与管线测试用 monkeypatch 伪造 _run_xtb；
采样器本身（RDKit/UFF）为真实计算，验证确定性与模板几何。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.dft import cache as dft_cache  # noqa: E402
from src.dft import engine  # noqa: E402
from src.dft import jobs as dft_jobs  # noqa: E402

BENZENE = "c1ccccc1"
PHENOL = "Oc1ccccc1"
BROMOBENZENE = "Brc1ccccc1"


# ---------------------------------------------------------------- MC 采样器（真实 RDKit）

class TestMcSampling:
    def test_deterministic_same_seed(self):
        """同种子 → 候选逐字节一致（缓存/复现的前提）。"""
        c1 = engine.sample_complex_orientations(BENZENE, PHENOL, n_samples=8)
        c2 = engine.sample_complex_orientations(BENZENE, PHENOL, n_samples=8)
        assert [c["kind"] for c in c1] == [c["kind"] for c in c2]
        assert all(a["xyz"] == b["xyz"] for a, b in zip(c1, c2))

    def test_different_seed_differs(self):
        """不同种子 → MC 段候选几何不同。"""
        c1 = engine.sample_complex_orientations(PHENOL, BENZENE, n_samples=8,
                                                seed=42)
        c2 = engine.sample_complex_orientations(PHENOL, BENZENE, n_samples=8,
                                                seed=777)
        xyz1 = [c["xyz"] for c in c1 if c["kind"] == "mc"]
        xyz2 = [c["xyz"] for c in c2 if c["kind"] == "mc"]
        assert xyz1 and xyz2 and xyz1 != xyz2

    def test_sample_count_respected(self):
        cands = engine.sample_complex_orientations(BENZENE, PHENOL, n_samples=6)
        assert len(cands) == 6
        # 每个候选都是合法 xyz：原子数 = 苯酚 13 + 苯 12
        for c in cands:
            assert engine._xyz_atom_count(c["xyz"]) == 25

    def test_pi_templates_for_aromatic_pair(self):
        """双方有芳环 → π-π 平行错位（环心距 ~3.8 Å）与 T 型（~4.9 Å）模板。"""
        import numpy as np
        mol_a = engine._embed_one(BENZENE, seed=42)
        mol_b = engine._embed_one(BENZENE, seed=43)
        tmpls = dict()
        for kind, pos_b in engine._motif_templates(mol_a, mol_b):
            tmpls.setdefault(kind, pos_b)
        assert "template_pi_stack" in tmpls
        assert "template_t_shape" in tmpls
        pos_a = mol_a.GetConformer(0).GetPositions()
        d_pd = float(np.linalg.norm(
            tmpls["template_pi_stack"][:6].mean(axis=0)
            - pos_a[:6].mean(axis=0)))
        d_t = float(np.linalg.norm(
            tmpls["template_t_shape"][:6].mean(axis=0)
            - pos_a[:6].mean(axis=0)))
        # PD 堆叠：sqrt(3.4² + 1.6²) ≈ 3.76；T 型：4.9
        assert d_pd == pytest.approx(3.76, abs=0.05)
        assert d_t == pytest.approx(4.90, abs=0.05)

    def test_halogen_template_geometry(self):
        """含 Br 客体 + 含 O 主体 → 卤键模板：Br···O ≈ 3.1 Å 且无过近接触。"""
        import numpy as np
        mol_a = engine._embed_one(PHENOL, seed=42)
        mol_b = engine._embed_one(BROMOBENZENE, seed=43)
        tmpls = [p for k, p in engine._motif_templates(mol_a, mol_b)
                 if k == "template_halogen"]
        assert tmpls, "溴苯 + 苯酚应产生卤键模板"
        pos_a = mol_a.GetConformer(0).GetPositions()
        o_idx = [a.GetIdx() for a in mol_a.GetAtoms()
                 if a.GetAtomicNum() == 8][0]
        br_idx = [a.GetIdx() for a in mol_b.GetAtoms()
                  if a.GetAtomicNum() == 35][0]
        for pos_b in tmpls:
            d = float(np.linalg.norm(pos_b[br_idx] - pos_a[o_idx]))
            diff = pos_b[:, None, :] - pos_a[None, :, :]
            dmin = float(np.sqrt((diff ** 2).sum(-1)).min())
            assert 2.8 <= d <= 3.6, f"Br···O 距离 {d:.2f} 不在卤键区间"
            assert dmin >= 1.5, f"存在过近接触 {dmin:.2f} Å"
            # σ 空穴共线性：C→Br 与 Br→O 同向（即 C–Br···O 键角 180°）
            c_idx = mol_b.GetAtomWithIdx(br_idx).GetNeighbors()[0].GetIdx()
            v_cb = pos_b[br_idx] - pos_b[c_idx]
            v_ob = pos_a[o_idx] - pos_b[br_idx]
            cosang = float(v_cb @ v_ob
                           / (np.linalg.norm(v_cb) * np.linalg.norm(v_ob)))
            assert cosang > 0.9, "C–Br···O 应接近线形（卤键几何）"

    def test_mc_sample_count_env_and_adaptive(self, monkeypatch):
        """环境变量覆盖默认值；大体系自适应缩减。"""
        assert engine.mc_sample_count() == engine.DEFAULT_MC_SAMPLES
        monkeypatch.setenv("COF_DFT_MC_SAMPLES", "8")
        assert engine.mc_sample_count() == 8
        # 89 原子（TAPT-DMTA·BDE 规模）：min(8, 360//89=4) = 4
        assert engine.mc_sample_count(89) == 4
        monkeypatch.delenv("COF_DFT_MC_SAMPLES")
        assert engine.mc_sample_count() == engine.DEFAULT_MC_SAMPLES

    def test_kj_kcal_conversion_consistent(self):
        """kJ/mol 主口径换算：HARTREE_TO_KJ == HARTREE_TO_KCAL × 4.184。"""
        assert engine.HARTREE_TO_KJ == pytest.approx(
            engine.HARTREE_TO_KCAL * 4.184, abs=0.01)


# ---------------------------------------------------------------- xTB 分级筛选（假 xtb）

OUT_SP = "| TOTAL ENERGY  {e:.10f} Eh   |\n normal termination of xtb\n"
FAKE_XYZ = "3\ncomplex\nC 0.0 0.0 0.0\nN 1.4 0.0 0.0\nO 2.5 0.0 0.0\n"


@pytest.fixture()
def fake_xtb_screen(monkeypatch, tmp_path):
    """伪造 _run_xtb：记录 (args, cwd.name, opt)；能量按候选序号递减可控。"""
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    calls: list[tuple] = []

    def _fake(xyz_block, args, cwd, timeout, opt=True):
        calls.append((args, cwd.name, opt))
        # 候选目录 cand_XX_kind/gfnff|gfn2sp；能量随候选序号递减（后者更优）
        if cwd.parent.name.startswith("cand_"):
            idx = int(cwd.parent.name.split("_")[1])
            base = -50.0 - idx * 0.01  # cand_00 最差，序号大者优
            if cwd.name == "gfn2sp":
                base -= 0.001
            return OUT_SP.format(e=base), (FAKE_XYZ if cwd.name == "gfnff"
                                           else None)
        # 主管线目录
        stdout = {"a": OUT_SP.format(e=-10.0), "b": OUT_SP.format(e=-8.0),
                  "dimer": OUT_SP.format(e=-10.0), "x": OUT_SP.format(e=-8.0),
                  "complex": OUT_SP.format(e=-18.005)}[cwd.name]
        return stdout, FAKE_XYZ if cwd.name in ("complex",) else None

    monkeypatch.setattr(engine, "_run_xtb", _fake)
    return calls


class TestScreening:
    def test_screen_picks_lowest_and_reports(self, fake_xtb_screen, tmp_path):
        info = engine.screen_complex_xtb(BENZENE, PHENOL, tmp_path / "scr",
                                         n_samples=5)
        assert info["n_samples"] == 5
        assert len(info["trials"]) == 5
        # 能量随序号递减 → 最后一个候选胜出
        assert info["best_xyz"]
        assert info["screen_level"] == "gfn2sp"  # 25 原子 ≤ 60
        # 小体系：每候选 gfnff 优化 + gfn2 单点各一次
        kinds = [(c[1], c[2]) for c in fake_xtb_screen]
        assert ("gfnff", True) in kinds and ("gfn2sp", False) in kinds

    def test_large_system_skips_gfn2_sp(self, fake_xtb_screen, tmp_path,
                                        monkeypatch):
        monkeypatch.setattr(engine, "_MC_SP_MAX_ATOMS", 1)  # 一切按大体系
        info = engine.screen_complex_xtb(BENZENE, PHENOL, tmp_path / "scr",
                                         n_samples=4)
        assert info["screen_level"] == "gfnff"
        # 不允许出现单点（opt=False）调用
        assert all(opt for _, _, opt in fake_xtb_screen)

    def test_screen_all_fail_chinese_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
        monkeypatch.setattr(engine, "_run_xtb",
                            lambda *a, **k: ("no energy here", None))
        with pytest.raises(engine.DftError, match="取向筛选全部失败"):
            engine.screen_complex_xtb(BENZENE, PHENOL, tmp_path / "scr",
                                      n_samples=3)

    def test_pair_binding_with_sampling(self, fake_xtb_screen, tmp_path):
        """gfn2 档默认走 MC 采样：结果带 sampling 字段，调用次数 > 3。"""
        r = engine.compute_pair_binding(BENZENE, PHENOL, method="gfn2",
                                        jobs_root=tmp_path, n_samples=4)
        assert r["sampling"] is not None
        assert r["sampling"]["n_samples"] == 4
        assert r["sampling"]["screen_level"] == "gfn2sp"
        # 4 候选 × (gfnff + gfn2sp) + a + b + complex = 11 次
        assert len(fake_xtb_screen) == 11
        # 结合能口径不变：complex - a - b
        assert r["e_bind_hartree"] == pytest.approx(-0.005, abs=1e-9)

    def test_pair_binding_n_samples_1_legacy(self, fake_xtb_screen, tmp_path):
        """n_samples=1 → 旧单取向口径：无筛选调用、无 sampling 字段。"""
        r = engine.compute_pair_binding(BENZENE, PHENOL, method="gfn2",
                                        jobs_root=tmp_path, n_samples=1)
        assert r["sampling"] is None
        assert len(fake_xtb_screen) == 3  # a + b + complex

    def test_gfnff_never_screens(self, fake_xtb_screen, tmp_path):
        r = engine.compute_pair_binding(BENZENE, PHENOL, method="gfnff",
                                        jobs_root=tmp_path)
        assert r["sampling"] is None
        assert all(args == ["--gfnff"] for args, _, _ in
                   [(c[0], None, None) for c in fake_xtb_screen])


# ---------------------------------------------------------------- 缓存采样口径

class TestSamplerCacheTag:
    def test_tag_rules(self):
        assert dft_jobs._sampler_tag("xtb", "gfnff", None) is None
        assert dft_jobs._sampler_tag("xtb", "gfnff", 12) is None
        assert dft_jobs._sampler_tag("xtb", "gfn2", None) == "mc0"
        assert dft_jobs._sampler_tag("xtb", "gfn2", 8) == "mc8"
        assert dft_jobs._sampler_tag("psi4", "wb97xd3bj_svp", None) == "mc0"
        assert dft_jobs._sampler_tag("psi4", "b3lyp_631gdp", 16) == "mc16"

    def test_cache_key_tag_isolates(self):
        k_old = dft_cache.cache_key("AAA", "self_stack", "gfn2")
        k_mc = dft_cache.cache_key("AAA", "self_stack", "gfn2",
                                   sampler_tag="mc0")
        k_mc8 = dft_cache.cache_key("AAA", "self_stack", "gfn2",
                                    sampler_tag="mc8")
        assert k_old != k_mc != k_mc8 and k_old != k_mc8
        # 旧格式保持不变（存量缓存兼容）
        k_legacy = dft_cache.cache_key("AAA", "self_stack", "gfn2",
                                       mode="dimer", backend="xtb")
        assert k_legacy == k_old
