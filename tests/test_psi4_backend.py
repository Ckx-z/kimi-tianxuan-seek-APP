"""Psi4 精度档后端测试：输入生成 / 输出解析 / 子进程调用（假脚本）/ 环境检测降级 / API 集成。

不依赖真实 psi4-env：子进程测试用 sys.executable 跑伪造脚本（写 result.json +
@@PROGRESS@@ 行）；真实 Psi4 冒烟仅在 COF_TEST_PSI4_SMOKE=1 且环境已装时启用。
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

from src import runtime_config  # noqa: E402
from src.dft import cache as dft_cache  # noqa: E402
from src.dft import dimer as dimer_mod  # noqa: E402
from src.dft import engine  # noqa: E402
from src.dft import jobs as dft_jobs  # noqa: E402
from src.dft import log as dft_log  # noqa: E402
from src.dft import psi4_backend as pb  # noqa: E402
from favorites import store as fav_store  # noqa: E402

ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
AMINE = "Nc1ccc(N)cc1"
DIMER = dimer_mod.make_dimer(ALD, AMINE)["smiles"]

XYZ_6 = (
    "6\nmini complex\n"
    "C 0.0 0.0 0.0\nH 1.0 0.0 0.0\nO 0.0 1.0 0.0\n"
    "H 3.4 0.9 0.1\nC 4.4 0.9 0.1\nH 5.4 0.9 0.1\n"
)

FAKE_PSI4_JSON = {
    "method": "wb97x-d3bj",
    "basis": "def2-svp",
    "label": "ωB97X-D3BJ/def2-SVP（真 DFT）",
    "bsse_type": "cp",
    "psi4_version": "1.11",
    "e_bind_cp_hartree": -0.008,
    "e_bind_raw_hartree": -0.011,
    "e_monomer_a_hartree": -100.0,
    "e_monomer_b_hartree": -50.0,
    "e_complex_hartree": -150.011,
    "gap_ev_complex": 4.56,
    "dipole_debye_complex": 1.23,
    "fchk_written": True,
}


# ---------------------------------------------------------------- 输入生成

class TestScriptGeneration:
    def test_script_core_content(self):
        s = pb.generate_psi4_script(XYZ_6, 4, "wb97xd3bj_svp", optimize=True)
        assert "bsse_type=\"cp\"" in s          # counterpoise BSSE 校正
        assert "wb97x-d3bj/def2-svp" in s       # 默认方法/基组
        assert "psi4.optimize" in s             # 几何优化开启
        assert "symmetry c1" in s                 # 保持原子顺序（fragment 区间不失效）
        assert '"--"' in s                      # 片段分隔
        assert "save_xyz_file" in s             # 优化后 xyz 写出
        assert "psi4.fchk" in s                 # fchk 写出
        assert "result.json" in s
        assert pb.PROGRESS_PREFIX in s
        # 语法必须合法
        import py_compile
        import tempfile
        p = Path(tempfile.mkdtemp()) / "gen.py"
        p.write_text(s, encoding="utf-8")
        py_compile.compile(str(p), doraise=True)

    def test_script_optimize_off(self):
        s = pb.generate_psi4_script(XYZ_6, 4, optimize=False)
        assert "if False:" in s

    def test_script_same_fragments(self):
        s = pb.generate_psi4_script(XYZ_6, 3, same_fragments=True)
        assert "if True:" in s

    def test_unknown_method_raises(self):
        with pytest.raises(engine.DftError, match="未知的 Psi4 方法档位"):
            pb.generate_psi4_script(XYZ_6, 4, "b3lyp")

    def test_bad_fragment_boundary_raises(self):
        with pytest.raises(engine.DftError, match="片段边界非法"):
            pb.generate_psi4_script(XYZ_6, 0)
        with pytest.raises(engine.DftError, match="片段边界非法"):
            pb.generate_psi4_script(XYZ_6, 6)


# ---------------------------------------------------------------- 输出解析

class TestResultParsing:
    def test_parse_ok(self):
        r = pb.parse_psi4_result(FAKE_PSI4_JSON)
        assert r["e_bind_hartree"] == pytest.approx(-0.008)
        assert r["e_bind_kcal"] == pytest.approx(-0.008 * 627.509)
        assert r["e_bind_kj"] == pytest.approx(-0.008 * 2625.5)
        assert r["e_bind_raw_kcal"] == pytest.approx(-0.011 * 627.509)
        assert r["energies_hartree"]["dimer"] == pytest.approx(-100.0)
        assert r["energies_hartree"]["complex"] == pytest.approx(-150.011)
        assert r["gap_ev_complex"] == pytest.approx(4.56)
        assert r["dipole_debye_complex"] == pytest.approx(1.23)
        assert r["fchk_written"] is True
        assert r["psi4_version"] == "1.11"

    def test_parse_missing_cp_raises(self):
        with pytest.raises(engine.DftError, match="counterpoise"):
            pb.parse_psi4_result({"e_complex_hartree": -1.0})


# ---------------------------------------------------------------- 环境检测

class TestDetect:
    def test_not_installed_graceful(self, monkeypatch):
        monkeypatch.setattr(runtime_config, "psi4_python", lambda: None)
        det = pb.detect_psi4()
        assert det["installed"] is False
        assert det["version"] is None
        assert "install_psi4_env.bat" in det["reason"]

    def test_broken_interpreter(self, monkeypatch, tmp_path):
        fake = tmp_path / "python.exe"
        fake.write_text("not an exe", encoding="utf-8")
        monkeypatch.setattr(runtime_config, "psi4_python", lambda: fake)
        det = pb.detect_psi4(timeout=10)
        assert det["installed"] is False
        assert det["path"] == str(fake)

    def test_interpreter_without_psi4(self, monkeypatch):
        # 当前测试解释器没有 psi4 → import 失败 → installed False
        monkeypatch.setattr(runtime_config, "psi4_python",
                            lambda: Path(sys.executable))
        det = pb.detect_psi4(timeout=120)
        assert det["installed"] is False
        assert "import psi4" in det["reason"]


# ---------------------------------------------------------------- 子进程调用（假脚本）

@pytest.fixture()
def fake_psi4_python(monkeypatch):
    """detect_psi4 指向当前解释器（假脚本无需真实 psi4）。"""
    monkeypatch.setattr(pb, "detect_psi4", lambda: {
        "installed": True, "version": "fake", "path": sys.executable,
        "reason": "ok"})
    return sys.executable


class TestRunScript:
    def test_success_with_progress(self, fake_psi4_python, tmp_path):
        script = (
            "import pathlib\n"
            f"print('{pb.PROGRESS_PREFIX} 几何优化中', flush=True)\n"
            f"pathlib.Path('result.json').write_text({json.dumps(FAKE_PSI4_JSON)!r}"
            ", encoding='utf-8')\n"
            f"print('{pb.PROGRESS_PREFIX} 计算完成', flush=True)\n"
        )
        stages: list[str] = []
        data = pb._run_psi4_script(script, tmp_path, 60, on_stage=stages.append)
        assert data["e_bind_cp_hartree"] == pytest.approx(-0.008)
        assert "几何优化中" in stages
        assert "计算完成" in stages

    def test_failure_traceback(self, fake_psi4_python, tmp_path):
        script = "raise RuntimeError('SCF 不收敛模拟')\n"
        with pytest.raises(engine.DftError, match="Psi4 计算失败"):
            pb._run_psi4_script(script, tmp_path, 60)

    def test_error_json(self, fake_psi4_python, tmp_path):
        script = (
            "import json, sys\n"
            "json.dump({'error': 'Traceback...\\nValidationError: bad geometry'},"
            " open('result.json', 'w', encoding='utf-8'))\n"
            "sys.exit(1)\n"
        )
        with pytest.raises(engine.DftError, match="bad geometry"):
            pb._run_psi4_script(script, tmp_path, 60)

    def test_timeout_killed(self, fake_psi4_python, tmp_path):
        script = "import time\ntime.sleep(30)\n"
        with pytest.raises(engine.DftError, match="超时"):
            pb._run_psi4_script(script, tmp_path, 2)

    def test_no_result_file(self, fake_psi4_python, tmp_path):
        script = "print('done but forgot result')\n"
        with pytest.raises(engine.DftError, match="result.json"):
            pb._run_psi4_script(script, tmp_path, 60)

    def test_not_installed_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pb, "detect_psi4", lambda: {
            "installed": False, "version": None, "path": None,
            "reason": pb.INSTALL_HINT})
        with pytest.raises(pb.Psi4NotInstalledError, match="install_psi4_env"):
            pb._run_psi4_script("pass", tmp_path, 60)


# ---------------------------------------------------------------- 主管线（mock 子进程）

class TestPipeline:
    def test_compute_binding_dimer(self, monkeypatch, tmp_path):
        """二聚体模式全管线：真实 RDKit 构象 + 假子进程 → 结果字段与引擎口径对齐。"""
        monkeypatch.setattr(pb, "detect_psi4", lambda: {
            "installed": True, "version": "1.11", "path": sys.executable,
            "reason": "ok"})
        monkeypatch.setattr(pb, "_xtb_guess", lambda xyz, wd, stage: xyz)
        monkeypatch.setattr(runtime_config, "user_data_root", lambda: tmp_path)

        def fake_run(script, cwd, timeout, on_stage=None):
            cwd.mkdir(parents=True, exist_ok=True)
            (cwd / "complex_opt.xyz").write_text(XYZ_6, encoding="utf-8")
            (cwd / "complex.fchk").write_text("fake fchk", encoding="utf-8")
            if on_stage:
                on_stage("结合能计算中（counterpoise BSSE 校正）…")
            return dict(FAKE_PSI4_JSON)
        monkeypatch.setattr(pb, "_run_psi4_script", fake_run)

        stages: list[str] = []
        result = pb.compute_binding_psi4(
            ALD, AMINE, on_stage=stages.append, jobs_root=tmp_path / "jobs")
        assert result["backend"] == "psi4"
        assert result["method"] == "wb97xd3bj_svp"
        assert "ωB97X" in result["method_label"]
        assert result["dimer_smiles"] == DIMER
        assert result["e_bind_kcal"] == pytest.approx(-0.008 * 627.509)
        assert result["gap_ev"]["complex"] == pytest.approx(4.56)
        assert result["gap_ev"]["dimer"] is None
        assert result["dipole_debye"]["complex"] == pytest.approx(1.23)
        n_total = result["complex_atom_count"]
        frag = result["fragment_ranges"]
        assert frag["a"][1] == frag["b"][0] and frag["b"][1] == n_total
        assert result["psi4_detail"]["bsse_type"] == "cp"
        assert result["psi4_detail"]["fchk_available"] is True
        assert Path(result["psi4_detail"]["fchk_path"]).is_file()
        assert any("BSSE" in s for s in stages)

    def test_compute_pair(self, monkeypatch, tmp_path):
        """pair 模式全管线（苯·苯酚式任意双分子）。"""
        monkeypatch.setattr(pb, "detect_psi4", lambda: {
            "installed": True, "version": "1.11", "path": sys.executable,
            "reason": "ok"})
        monkeypatch.setattr(pb, "_xtb_guess", lambda xyz, wd, stage: xyz)
        monkeypatch.setattr(runtime_config, "user_data_root", lambda: tmp_path)

        def fake_run(script, cwd, timeout, on_stage=None):
            cwd.mkdir(parents=True, exist_ok=True)
            return dict(FAKE_PSI4_JSON)
        monkeypatch.setattr(pb, "_run_psi4_script", fake_run)

        result = pb.compute_pair_binding_psi4(
            "c1ccccc1", "Oc1ccccc1", jobs_root=tmp_path / "jobs")
        assert result["mode"] == "pair"
        assert result["backend"] == "psi4"
        assert result["dimer_smiles"] is None
        assert result["x_description"] == engine.PAIR_X_DESCRIPTION
        assert result["x_cache_part"].startswith("pair:")

    def test_not_installed_pipeline(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pb, "detect_psi4", lambda: {
            "installed": False, "version": None, "path": None,
            "reason": pb.INSTALL_HINT})
        with pytest.raises(pb.Psi4NotInstalledError):
            pb.compute_binding_psi4(ALD, AMINE, jobs_root=tmp_path / "jobs")


# ---------------------------------------------------------------- 缓存 key

class TestCacheKey:
    def test_backend_isolation(self):
        k_xtb = dft_cache.cache_key(DIMER, "self_stack", "gfn2", backend="xtb")
        k_psi4 = dft_cache.cache_key(DIMER, "self_stack", "wb97xd3bj_svp",
                                     backend="psi4")
        assert k_xtb != k_psi4

    def test_xtb_key_backward_compat(self):
        """xtb 档保持旧串格式，存量缓存不失效。"""
        import hashlib
        expect = hashlib.sha1(
            f"dimer::{DIMER}::self_stack::gfn2".encode()).hexdigest()
        assert dft_cache.cache_key(DIMER, "self_stack", "gfn2") == expect
        assert dft_cache.cache_key(DIMER, "self_stack", "gfn2",
                                   backend="xtb") == expect


# ---------------------------------------------------------------- API 集成

FAKE_PSI4_RESULT = {
    "mode": "dimer",
    "smiles_a": engine.canonicalize_smiles(ALD),
    "smiles_b": engine.canonicalize_smiles(AMINE),
    "dimer_smiles": DIMER,
    "dimer_multi_site": True,
    "dimer_note": "示意单点缩合：多位点单体仅缩合第一个位点",
    "x_type": "self_stack",
    "x_smiles": DIMER,
    "x_description": "自身堆积（二聚体·二聚体）",
    "x_cache_part": "self_stack",
    "x_request": {"solvent_id": None, "ald2_smiles": None,
                  "amine2_smiles": None, "custom_smiles": None},
    "backend": "psi4",
    "method": "wb97xd3bj_svp",
    "method_label": "ωB97X-D3BJ/def2-SVP（真 DFT）",
    "e_bind_hartree": -0.008,
    "e_bind_kcal": -5.02,
    "e_bind_kj": -21.0,
    "energies_hartree": {"dimer": -100.0, "x": -100.0, "complex": -200.008},
    "gap_ev": {"dimer": None, "x": None, "complex": 4.56},
    "dipole_debye": {"dimer": None, "x": None, "complex": 1.23},
    "complex_atom_count": 60,
    "complex_xyz": "3\ncomplex\nC 0 0 0\nN 1.4 0 0\nO 2.5 0 0\n",
    "fragment_ranges": {"a": [0, 30], "b": [30, 60]},
    "elapsed_sec": 95.5,
    "psi4_detail": {"method": "wb97x-d3bj", "basis": "def2-svp",
                    "bsse_type": "cp", "psi4_version": "1.11",
                    "e_bind_raw_kcal": -6.89,
                    "fchk_available": True, "fchk_path": None},
}


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离缓存/历史/收藏目录 + 假 xTB/Psi4 引擎；返回调用记录 dict。"""
    monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path / "dft_cache")
    monkeypatch.setattr(dft_log, "LOG_PATH", tmp_path / "dft_log.jsonl")
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favorites")
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    monkeypatch.setattr(pb, "detect_psi4", lambda: {
        "installed": True, "version": "1.11",
        "path": "E:/ANACONDA/envs/psi4-env/python.exe", "reason": "ok"})
    calls = {"xtb": [], "psi4": []}

    def _fake_xtb(ald_smiles, amine_smiles, method="gfn2", on_stage=None,
                  jobs_root=None, x_type="self_stack", **kwargs):
        calls["xtb"].append((ald_smiles, amine_smiles, method, x_type))
        r = dict(FAKE_PSI4_RESULT)
        r.update(backend="xtb", method=method, method_label="GFN2-xTB（精确）",
                 x_type=x_type, psi4_detail=None)
        return r

    def _fake_psi4(ald_smiles, amine_smiles, method="wb97xd3bj_svp",
                   on_stage=None, jobs_root=None, x_type="self_stack",
                   **kwargs):
        calls["psi4"].append((ald_smiles, amine_smiles, method, x_type))
        if on_stage:
            on_stage("几何优化中（ωB97X-D3BJ/def2-SVP）…")
        return dict(FAKE_PSI4_RESULT)

    monkeypatch.setattr(engine, "compute_binding", _fake_xtb)
    monkeypatch.setattr(pb, "compute_binding_psi4", _fake_psi4)
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


class TestBackendsEndpoint:
    def test_shape(self, client, sandbox):
        r = client.get("/api/dft/backends")
        assert r.status_code == 200
        b = r.json()["backends"]
        assert b["xtb"]["installed"] is True
        assert b["psi4"]["installed"] is True
        assert b["psi4"]["version"] == "1.11"
        assert b["psi4"]["install_hint"] is None
        assert b["psi4"]["default_method"] == "wb97xd3bj_svp"

    def test_psi4_not_installed_hint(self, client, sandbox, monkeypatch):
        monkeypatch.setattr(pb, "detect_psi4", lambda: {
            "installed": False, "version": None, "path": None,
            "reason": pb.INSTALL_HINT})
        b = client.get("/api/dft/backends").json()["backends"]
        assert b["psi4"]["installed"] is False
        assert "install_psi4_env.bat" in b["psi4"]["install_hint"]


class TestPsi4Jobs:
    def test_psi4_job_lifecycle(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "backend": "psi4"})
        assert r.status_code == 202
        assert r.json()["backend"] == "psi4"
        # 未显式给方法档 → 默认 wb97xd3bj_svp
        assert r.json()["method"] == "wb97xd3bj_svp"
        body = _wait_done(client, r.json()["job_id"])
        assert body["status"] == "done"
        res = body["result"]
        assert res["backend"] == "psi4"
        assert res["psi4_detail"]["bsse_type"] == "cp"
        assert res["gap_ev"]["complex"] == pytest.approx(4.56)
        assert len(sandbox["psi4"]) == 1
        assert len(sandbox["xtb"]) == 0

    def test_cache_isolated_between_backends(self, client, sandbox):
        """同组合：psi4 与 xtb 结果缓存互不命中。"""
        r1 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "backend": "psi4"})
        _wait_done(client, r1.json()["job_id"])
        r2 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        body2 = _wait_done(client, r2.json()["job_id"])
        assert body2["cached"] is False  # xtb 不命中 psi4 缓存
        assert body2["result"]["backend"] == "xtb"
        assert len(sandbox["psi4"]) == 1
        assert len(sandbox["xtb"]) == 1
        # 同后端重复 → 各自命中
        r3 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "backend": "psi4"})
        assert r3.json()["cached"] is True
        r4 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        assert r4.json()["cached"] is True

    def test_history_marks_backend(self, client, sandbox, tmp_path):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "backend": "psi4"})
        _wait_done(client, r.json()["job_id"])
        h = client.get("/api/dft/history").json()
        assert h["count"] == 1
        entry = h["history"][0]
        assert entry["backend"] == "psi4"
        assert entry["method"] == "wb97xd3bj_svp"
        assert "ωB97X" in entry["method_label"]

    def test_psi4_not_installed_503(self, client, sandbox, monkeypatch):
        monkeypatch.setattr(pb, "detect_psi4", lambda: {
            "installed": False, "version": None, "path": None,
            "reason": pb.INSTALL_HINT})
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "backend": "psi4"})
        assert r.status_code == 503
        assert "install_psi4_env.bat" in r.json()["detail"]
        # xtb 档不受影响
        r2 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        assert r2.status_code == 202

    def test_unknown_backend_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "backend": "mace"})
        assert r.status_code == 400
        assert "未知的计算后端" in r.json()["detail"]

    def test_psi4_pair_mode(self, client, sandbox, monkeypatch):
        def _fake_pair(a, b, method="wb97xd3bj_svp", on_stage=None, **kw):
            r = dict(FAKE_PSI4_RESULT)
            r.update(mode="pair", smiles_a=engine.canonicalize_smiles(a),
                     smiles_b=engine.canonicalize_smiles(b),
                     dimer_smiles=None, x_type=None,
                     x_smiles=engine.canonicalize_smiles(b),
                     x_description=engine.PAIR_X_DESCRIPTION,
                     x_cache_part=f"pair:{engine.canonicalize_smiles(b)}")
            return r
        monkeypatch.setattr(pb, "compute_pair_binding_psi4", _fake_pair)
        r = client.post("/api/dft/jobs", json={
            "mode": "pair", "ald_smiles": "c1ccccc1",
            "amine_smiles": "Oc1ccccc1", "backend": "psi4"})
        assert r.status_code == 202
        body = _wait_done(client, r.json()["job_id"])
        assert body["status"] == "done"
        assert body["result"]["backend"] == "psi4"
        assert body["result"]["mode"] == "pair"


# ---------------------------------------------------------------- 真实 Psi4 冒烟（默认跳过）

@pytest.mark.skipif(
    not (pb.detect_psi4()["installed"]
         and __import__("os").environ.get("COF_TEST_PSI4_SMOKE") == "1"),
    reason="真实 Psi4 冒烟：需 COF_TEST_PSI4_SMOKE=1 且 psi4-env 已安装")
def test_real_psi4_smoke(tmp_path):
    """苯·苯酚 pair 结合能（omegaB97X-D3BJ/def2-SVP + CP，不做几何优化加速）。"""
    result = pb.compute_pair_binding_psi4(
        "c1ccccc1", "Oc1ccccc1", jobs_root=tmp_path / "jobs",
        optimize=False)
    assert result["backend"] == "psi4"
    # 苯·苯酚结合能应在典型色散/氢键区间（-2 ~ -15 kcal/mol）
    assert -15.0 < result["e_bind_kcal"] < 0.0
    assert result["energies_hartree"]["complex"] < 0
    assert result["psi4_detail"]["psi4_version"]
