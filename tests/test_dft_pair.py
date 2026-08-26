"""DFT 任意双分子模式（选项2，mode="pair"）测试。

覆盖：engine pair 管线（跳过二聚体）/ fragment_ranges 正确性 /
API 建任务与结果字段 / 缓存隔离（pair vs dimer 互不命中）/ 历史记录 /
geometry 端点片段区间响应头 / 真实 xTB 冒烟（苯 + 苯酚，gfnff）。

不依赖真实 xtb 的用例：_run_xtb / compute_pair_binding 用 monkeypatch 伪造。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.dft import cache as dft_cache  # noqa: E402
from src.dft import engine  # noqa: E402
from src.dft import log as dft_log  # noqa: E402
from favorites import store as fav_store  # noqa: E402

BENZENE = "c1ccccc1"     # 苯（12 原子，加氢）
PHENOL = "Oc1ccccc1"     # 苯酚（13 原子，加氢）
FORMALDEHYDE = "C=O"     # 甲醛（4 原子）

# 仿真实 xtb 输出片段
OUT_A = """\
| TOTAL ENERGY           -10.000000000000 Eh   |
 * HOMO-LUMO GAP            6.000 eV
 normal termination of xtb
"""
OUT_B = """\
| TOTAL ENERGY           -8.000000000000 Eh   |
 * HOMO-LUMO GAP            5.500 eV
 normal termination of xtb
"""
OUT_C = """\
| TOTAL ENERGY           -18.005000000000 Eh   |
 * HOMO-LUMO GAP            5.800 eV
 normal termination of xtb
"""
FAKE_XYZ = "3\ncomplex\nC 0.0 0.0 0.0\nN 1.4 0.0 0.0\nO 2.5 0.0 0.0\n"


@pytest.fixture()
def fake_xtb_pair(monkeypatch, tmp_path):
    """按子目录名决定输出：a → OUT_A，b → OUT_B，complex → OUT_C。"""
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    calls = []

    def _fake(xyz_block, args, cwd, timeout):
        calls.append((args, cwd, timeout))
        stdout = {"a": OUT_A, "b": OUT_B, "complex": OUT_C}[cwd.name]
        opt = FAKE_XYZ if cwd.name == "complex" else None
        return stdout, opt

    monkeypatch.setattr(engine, "_run_xtb", _fake)
    return calls


# ---------------------------------------------------------------- engine


class TestPairEngine:
    def test_pair_basic(self, fake_xtb_pair, tmp_path):
        hints: list[str] = []
        r = engine.compute_pair_binding(
            BENZENE, PHENOL, method="gfn2",
            on_stage=hints.append, jobs_root=tmp_path)
        assert r["mode"] == "pair"
        assert r["dimer_smiles"] is None
        assert r["x_type"] is None
        assert r["x_description"] == "A···B 直接结合"
        assert r["smiles_a"] == engine.canonicalize_smiles(BENZENE)
        assert r["smiles_b"] == engine.canonicalize_smiles(PHENOL)
        assert r["x_smiles"] == r["smiles_b"]
        assert r["x_cache_part"].startswith("pair:")
        # E_bind = -18.005 - (-10.0) - (-8.0) = -0.005 Eh
        assert r["e_bind_hartree"] == pytest.approx(-0.005, abs=1e-9)
        assert r["e_bind_kcal"] == pytest.approx(-0.005 * 627.509, abs=1e-6)
        assert r["energies_hartree"]["dimer"] == pytest.approx(-10.0)  # 分子 A
        assert r["energies_hartree"]["x"] == pytest.approx(-8.0)       # 分子 B
        assert r["energies_hartree"]["complex"] == pytest.approx(-18.005)
        assert len(fake_xtb_pair) == 3  # a + b + complex
        assert any("分子 A" in h for h in hints)
        assert any("复合物" in h for h in hints)

    def test_pair_same_molecule_reuses_run(self, fake_xtb_pair, tmp_path):
        """A 与 B 同分子：只跑两次 xtb（a + complex）。"""
        r = engine.compute_pair_binding(
            BENZENE, BENZENE, method="gfnff", jobs_root=tmp_path)
        assert len(fake_xtb_pair) == 2
        assert r["energies_hartree"]["x"] == r["energies_hartree"]["dimer"]
        # E_bind = -18.005 - 2*(-10.0)
        assert r["e_bind_hartree"] == pytest.approx(1.995, abs=1e-9)

    def test_pair_fragment_ranges(self, fake_xtb_pair, tmp_path):
        """片段区间 = A 加氢原子数 / 复合物总原子数（0 基左闭右开）。"""
        r = engine.compute_pair_binding(
            BENZENE, FORMALDEHYDE, method="gfn2", jobs_root=tmp_path)
        frag = r["fragment_ranges"]
        assert frag["a"] == [0, 12]           # 苯 12 原子
        assert frag["b"] == [12, 12 + 4]      # 甲醛 4 原子
        assert frag["b"][1] == r["complex_atom_count"]

    def test_pair_fragment_ranges_match_complex_xyz(self, fake_xtb_pair,
                                                    tmp_path, monkeypatch):
        """无 xtbopt.xyz 时退回初猜 xyz，片段区间与初猜原子序仍一致。"""
        def _no_opt(xyz_block, args, cwd, timeout):
            stdout = {"a": OUT_A, "b": OUT_B, "complex": OUT_C}[cwd.name]
            return stdout, None  # 无 xtbopt.xyz → 用初猜 xyz
        monkeypatch.setattr(engine, "_run_xtb", _no_opt)
        r = engine.compute_pair_binding(
            BENZENE, FORMALDEHYDE, method="gfn2", jobs_root=tmp_path)
        frag = r["fragment_ranges"]
        assert frag["a"] == [0, 12]
        assert frag["b"] == [12, 16]
        # 初猜 xyz 的总原子数与区间右端一致
        assert engine._xyz_atom_count(r["complex_xyz"]) == frag["b"][1]

    def test_pair_invalid_smiles_a(self, fake_xtb_pair, tmp_path):
        with pytest.raises(engine.DftError, match="分子 A 的 SMILES 无法解析"):
            engine.compute_pair_binding("xx!!", PHENOL, jobs_root=tmp_path)

    def test_pair_invalid_smiles_b(self, fake_xtb_pair, tmp_path):
        with pytest.raises(engine.DftError, match="分子 B 的 SMILES 无法解析"):
            engine.compute_pair_binding(BENZENE, "xx!!", jobs_root=tmp_path)

    def test_pair_unknown_method(self, tmp_path):
        with pytest.raises(engine.DftError, match="未知方法档位"):
            engine.compute_pair_binding(BENZENE, PHENOL, method="b3lyp",
                                        jobs_root=tmp_path)

    def test_pair_missing_engine(self, monkeypatch, tmp_path):
        monkeypatch.setattr(engine, "xtb_binary", lambda: None)
        with pytest.raises(engine.DftError, match="未安装计算引擎"):
            engine.compute_pair_binding(BENZENE, PHENOL, jobs_root=tmp_path)

    def test_pair_skips_dimer_generation(self, fake_xtb_pair, tmp_path,
                                         monkeypatch):
        """pair 模式不调用 dimer.make_dimer（非醛胺也能算）。"""
        def _boom(*_a, **_k):
            raise AssertionError("pair 模式不应调用 make_dimer")
        monkeypatch.setattr(engine.dimer_mod, "make_dimer", _boom)
        r = engine.compute_pair_binding(BENZENE, PHENOL, method="gfn2",
                                        jobs_root=tmp_path)
        assert r["mode"] == "pair"


class TestCacheKeyMode:
    def test_mode_isolates_cache_key(self):
        k1 = dft_cache.cache_key("AAA", "self_stack", "gfn2", mode="dimer")
        k2 = dft_cache.cache_key("AAA", "pair:BBB", "gfn2", mode="pair")
        k3 = dft_cache.cache_key("AAA", "self_stack", "gfn2")  # 默认 dimer
        assert k1 != k2
        assert k1 == k3  # 缺省 mode 向后兼容


# ---------------------------------------------------------------- API

ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
AMINE = "Nc1ccc(N)cc1"

FAKE_PAIR_RESULT = {
    "mode": "pair",
    "smiles_a": engine.canonicalize_smiles(BENZENE),
    "smiles_b": engine.canonicalize_smiles(PHENOL),
    "dimer_smiles": None,
    "dimer_multi_site": False,
    "dimer_note": None,
    "x_type": None,
    "x_smiles": engine.canonicalize_smiles(PHENOL),
    "x_description": "A···B 直接结合",
    "x_cache_part": f"pair:{engine.canonicalize_smiles(PHENOL)}",
    "x_request": {"solvent_id": None, "ald2_smiles": None,
                  "amine2_smiles": None, "custom_smiles": None},
    "method": "gfn2",
    "method_label": "GFN2-xTB（精确）",
    "e_bind_hartree": -0.005,
    "e_bind_kcal": -3.1375,
    "e_bind_kj": -13.1275,
    "energies_hartree": {"dimer": -10.0, "x": -8.0, "complex": -18.005},
    "gap_ev": {"dimer": 6.0, "x": 5.5, "complex": 5.8},
    "dipole_debye": {"dimer": 0.0, "x": 1.2, "complex": 1.1},
    "complex_atom_count": 25,
    "complex_xyz": FAKE_XYZ,
    "fragment_ranges": {"a": [0, 12], "b": [12, 25]},
    "elapsed_sec": 0.01,
}


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离缓存/历史/收藏目录 + 假引擎（dimer / pair 双路）；返回调用记录。"""
    monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path / "dft_cache")
    monkeypatch.setattr(dft_log, "LOG_PATH", tmp_path / "dft_log.jsonl")
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favorites")
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    calls = {"pair": [], "dimer": []}

    def _fake_pair(smiles_a, smiles_b, method="gfn2", on_stage=None,
                   jobs_root=None):
        calls["pair"].append((smiles_a, smiles_b, method))
        if on_stage:
            on_stage("正在优化分子 A 几何…")
        result = dict(FAKE_PAIR_RESULT)
        result["method"] = method
        return result

    def _fake_dimer(ald_smiles, amine_smiles, method="gfn2", on_stage=None,
                    jobs_root=None, x_type="self_stack", **kwargs):
        calls["dimer"].append((ald_smiles, amine_smiles, method, x_type))
        from src.dft import dimer as dimer_mod
        dim = dimer_mod.make_dimer(ald_smiles, amine_smiles)["smiles"]
        return {
            "mode": "dimer",
            "smiles_a": engine.canonicalize_smiles(ald_smiles),
            "smiles_b": engine.canonicalize_smiles(amine_smiles),
            "dimer_smiles": dim,
            "dimer_multi_site": False,
            "dimer_note": None,
            "x_type": x_type,
            "x_smiles": dim,
            "x_description": "自身堆积（二聚体·二聚体）",
            "x_cache_part": "self_stack",
            "x_request": {"solvent_id": None, "ald2_smiles": None,
                          "amine2_smiles": None, "custom_smiles": None},
            "method": method,
            "method_label": "GFN2-xTB（精确）",
            "e_bind_hartree": -0.012,
            "e_bind_kcal": -7.5301,
            "e_bind_kj": -31.506,
            "energies_hartree": {"dimer": -100.0, "x": -100.0,
                                 "complex": -200.012},
            "gap_ev": {"dimer": 5.0, "x": 5.0, "complex": 4.2},
            "dipole_debye": {"dimer": 0.1, "x": 0.1, "complex": 1.2},
            "complex_atom_count": 60,
            "complex_xyz": FAKE_XYZ,
            "fragment_ranges": {"a": [0, 30], "b": [30, 60]},
            "elapsed_sec": 0.01,
        }

    monkeypatch.setattr(engine, "compute_pair_binding", _fake_pair)
    monkeypatch.setattr(engine, "compute_binding", _fake_dimer)
    return calls


def _wait_done(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/dft/jobs/{job_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 未在 {timeout}s 内完成")


class TestPairApi:
    def test_create_pair_job_done(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "method": "gfn2"})
        assert r.status_code == 202
        assert r.json()["mode"] == "pair"
        body = _wait_done(client, r.json()["job_id"])
        assert body["status"] == "done"
        res = body["result"]
        assert res["mode"] == "pair"
        assert res["dimer_smiles"] is None
        assert res["x_description"] == "A···B 直接结合"
        assert res["fragment_ranges"] == {"a": [0, 12], "b": [12, 25]}
        assert res["favorite"] is None  # pair 无收藏联动
        assert len(sandbox["pair"]) == 1
        assert len(sandbox["dimer"]) == 0

    def test_pair_ignores_x_type(self, client, sandbox):
        """pair 模式下 x_type 字段被忽略（即使给了非法值也不校验）。"""
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "x_type": "magic", "method": "gfn2"})
        assert r.status_code == 202
        body = _wait_done(client, r.json()["job_id"])
        assert body["status"] == "done"

    def test_pair_non_ald_amine_ok(self, client, sandbox):
        """非醛胺体系在 pair 模式下可正常建任务（不经过二聚体校验）。"""
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": "c1ccccc1",
            "amine_smiles": "C1COCCO1", "method": "gfnff"})
        assert r.status_code == 202

    def test_pair_empty_smiles_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": "", "amine_smiles": PHENOL})
        assert r.status_code == 400
        assert "分子 A 与分子 B" in r.json()["detail"]

    def test_pair_invalid_smiles_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": "xx!!", "amine_smiles": PHENOL})
        assert r.status_code == 400
        assert "分子 A 的 SMILES 无法解析" in r.json()["detail"]

    def test_unknown_mode_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "mode": "triple", "ald_smiles": BENZENE, "amine_smiles": PHENOL})
        assert r.status_code == 400
        assert "计算模式" in r.json()["detail"]

    def test_default_mode_is_dimer(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        assert r.status_code == 202
        assert r.json()["mode"] == "dimer"


class TestPairCache:
    def test_pair_repeat_hits_cache(self, client, sandbox):
        r1 = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        r2 = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "method": "gfn2"})
        assert r2.json()["status"] == "done"
        assert r2.json()["cached"] is True
        assert r2.json()["result"]["favorite"] is None
        assert len(sandbox["pair"]) == 1  # 引擎未再被调用

    def test_pair_vs_dimer_cache_isolated(self, client, sandbox):
        """同一对 SMILES：pair 与 dimer 结果互不命中。"""
        r1 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        r2 = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": ALD, "amine_smiles": AMINE,
            "method": "gfn2"})
        body = _wait_done(client, r2.json()["job_id"])
        assert body["cached"] is False
        assert body["result"]["mode"] == "pair"
        assert len(sandbox["pair"]) == 1
        assert len(sandbox["dimer"]) == 1
        # 反向：dimer 也不命中 pair 缓存
        r3 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        assert r3.json()["cached"] is True
        assert len(sandbox["dimer"]) == 1

    def test_pair_method_isolates_cache(self, client, sandbox):
        r1 = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        r2 = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "method": "gfnff"})
        body = _wait_done(client, r2.json()["job_id"])
        assert body["cached"] is False
        assert len(sandbox["pair"]) == 2


class TestPairGeometry:
    def test_geometry_fragment_ranges_header(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "method": "gfn2"})
        job_id = r.json()["job_id"]
        _wait_done(client, job_id)
        g = client.get(f"/api/dft/jobs/{job_id}/geometry")
        assert g.status_code == 200
        assert g.text.startswith("3\ncomplex")
        frag = json.loads(g.headers["x-fragment-ranges"])
        assert frag == {"a": [0, 12], "b": [12, 25]}


class TestPairHistory:
    def test_pair_history_written(self, client, sandbox, tmp_path):
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": BENZENE, "amine_smiles": PHENOL,
            "method": "gfn2"})
        _wait_done(client, r.json()["job_id"])
        log = tmp_path / "dft_log.jsonl"
        lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 1
        entry = lines[0]
        assert entry["mode"] == "pair"
        assert entry["dimer_smiles"] is None
        assert entry["x_description"] == "A···B 直接结合"
        assert entry["fragment_ranges"] == {"a": [0, 12], "b": [12, 25]}

        h = client.get("/api/dft/history")
        assert h.json()["count"] == 1
        assert h.json()["history"][0]["mode"] == "pair"


# ---------------------------------------------------------------- 真实冒烟

XTB = engine.xtb_binary()


@pytest.mark.skipif(XTB is None, reason="vendor/xtb 二进制不存在，跳过真实冒烟")
def test_smoke_pair_benzene_phenol_gfnff(tmp_path):
    """真实 xTB：苯 + 苯酚 pair 模式，GFN-FF 端到端。"""
    hints: list[str] = []
    r = engine.compute_pair_binding(
        BENZENE, PHENOL, method="gfnff",
        on_stage=hints.append, jobs_root=tmp_path)

    assert r["mode"] == "pair"
    assert r["dimer_smiles"] is None
    assert r["x_description"] == "A···B 直接结合"
    # 苯·苯酚复合物应表现为吸引（负结合能），量级合理（< 50 kcal/mol）
    assert r["e_bind_kcal"] < 0
    assert abs(r["e_bind_kcal"]) < 50
    for key in ("dimer", "x", "complex"):
        assert r["energies_hartree"][key] < 0
    # 片段区间：苯 12 原子，苯酚 13 原子，复合物 25 原子
    assert r["fragment_ranges"] == {"a": [0, 12], "b": [12, 25]}
    n_atoms = int(r["complex_xyz"].strip().splitlines()[0])
    assert n_atoms == r["complex_atom_count"] == 25
    assert any("分子 A" in h for h in hints)
    assert any("复合物" in h for h in hints)
