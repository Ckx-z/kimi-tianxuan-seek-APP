"""DFT API 测试（2.0）：任务生命周期 / X 类型参数校验 / 缓存隔离 / 历史 / 收藏联动 / 几何下载。

不依赖真实 xtb：engine.compute_binding 用 monkeypatch 替换为秒回的假实现；
缓存目录 / 历史日志 / 收藏目录全部 monkeypatch 到 tmp_path。
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
from src.dft import dimer as dimer_mod  # noqa: E402
from src.dft import engine  # noqa: E402
from src.dft import jobs as dft_jobs  # noqa: E402
from src.dft import log as dft_log  # noqa: E402
from favorites import store as fav_store  # noqa: E402

ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
AMINE = "Nc1ccc(N)cc1"
DIMER = dimer_mod.make_dimer(ALD, AMINE)["smiles"]

FAKE_RESULT = {
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
    "method": "gfn2",
    "method_label": "GFN2-xTB（精确）",
    "e_bind_hartree": -0.012,
    "e_bind_kcal": -7.5301,
    "e_bind_kj": -31.506,
    "energies_hartree": {"dimer": -100.0, "x": -100.0, "complex": -200.012},
    "gap_ev": {"dimer": 5.0, "x": 5.0, "complex": 4.2},
    "dipole_debye": {"dimer": 0.1, "x": 0.1, "complex": 1.2},
    "complex_atom_count": 60,
    "complex_xyz": "3\ncomplex\nC 0 0 0\nN 1.4 0 0\nO 2.5 0 0\n",
    "elapsed_sec": 0.01,
}


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离缓存/历史/收藏目录 + 假计算引擎；返回 fake 函数的调用记录。"""
    monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path / "dft_cache")
    monkeypatch.setattr(dft_log, "LOG_PATH", tmp_path / "dft_log.jsonl")
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favorites")
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    calls = []

    def _fake_compute(ald_smiles, amine_smiles, method="gfn2", on_stage=None,
                      jobs_root=None, x_type="self_stack", **kwargs):
        calls.append((ald_smiles, amine_smiles, method, x_type))
        if on_stage:
            on_stage("正在优化二聚体几何…")
            on_stage("正在优化 D·X 复合物几何…")
        result = dict(FAKE_RESULT)
        result["method"] = method
        result["x_type"] = x_type
        if x_type == "solvent":
            s = engine.solvent_by_id(kwargs.get("solvent_id") or "")
            result["x_smiles"] = engine.canonicalize_smiles(s["smiles"])
            result["x_description"] = f"溶剂分子：{s['name_zh']}"
            result["x_cache_part"] = f"solvent:{s['id']}"
        return result

    monkeypatch.setattr(engine, "compute_binding", _fake_compute)
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


class TestJobLifecycle:
    def test_create_and_poll_done(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        body = _wait_done(client, job_id)
        assert body["status"] == "done"
        assert body["cached"] is False
        res = body["result"]
        assert res["e_bind_kcal"] == pytest.approx(-7.5301)
        assert res["e_bind_kj"] == pytest.approx(-31.506)
        assert res["dimer_smiles"] == DIMER
        assert res["x_type"] == "self_stack"
        assert "自身堆积" in res["x_description"]
        assert res["gap_ev"]["complex"] == pytest.approx(4.2)
        assert res["dipole_debye"]["x"] == pytest.approx(0.1)
        assert res["energies_hartree"]["complex"] == pytest.approx(-200.012)
        assert len(sandbox) == 1

    def test_legacy_fields_compat(self, client, sandbox):
        """旧字段 smiles_a/smiles_b 兼容映射为醛/胺单体。"""
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        assert r.status_code == 202
        body = _wait_done(client, r.json()["job_id"])
        assert body["status"] == "done"
        assert body["result"]["dimer_smiles"] == DIMER

    def test_progress_hint_updated(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        assert body["progress_hint"] == "计算完成"

    def test_default_method_is_gfn2(self, client, sandbox):
        r = client.post("/api/dft/jobs",
                        json={"ald_smiles": ALD, "amine_smiles": AMINE})
        assert r.status_code == 202
        assert r.json()["method"] == "gfn2"

    def test_invalid_method_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "b3lyp"})
        assert r.status_code == 400

    def test_empty_smiles_400(self, client, sandbox):
        r = client.post("/api/dft/jobs",
                        json={"ald_smiles": "", "amine_smiles": AMINE})
        assert r.status_code == 400
        assert "不能为空" in r.json()["detail"]

    def test_non_ald_amine_400_chinese(self, client, sandbox):
        """非醛胺体系：前置校验 400，中文原因。"""
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": "c1ccccc1", "amine_smiles": AMINE})
        assert r.status_code == 400
        assert "二聚体生成失败" in r.json()["detail"]

    def test_unknown_x_type_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "x_type": "magic"})
        assert r.status_code == 400
        assert "X 类型" in r.json()["detail"]

    def test_solvent_missing_id_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "x_type": "solvent"})
        assert r.status_code == 400
        assert "solvent_id" in r.json()["detail"]

    def test_solvent_unknown_id_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "solvent", "solvent_id": "benzene"})
        assert r.status_code == 400
        assert "未知溶剂" in r.json()["detail"]

    def test_other_dimer_missing_params_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "other_dimer", "ald2_smiles": "O=CC=O"})
        assert r.status_code == 400
        assert "amine2_smiles" in r.json()["detail"]

    def test_custom_missing_smiles_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "x_type": "custom"})
        assert r.status_code == 400
        assert "custom_smiles" in r.json()["detail"]

    def test_custom_invalid_smiles_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "custom", "custom_smiles": "xx!!"})
        assert r.status_code == 400
        assert "无法解析" in r.json()["detail"]

    def test_engine_missing_503(self, client, sandbox, monkeypatch,
                                tmp_path):
        monkeypatch.setattr(engine, "xtb_binary", lambda: None)
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        assert r.status_code == 503
        assert "未安装计算引擎" in r.json()["detail"]

    def test_unknown_job_404(self, client):
        assert client.get("/api/dft/jobs/no-such-job").status_code == 404

    def test_failed_job_chinese_error(self, client, sandbox, monkeypatch):
        def _boom(*_a, **_k):
            raise engine.DftError("几何优化未收敛或计算中途失败（可尝试改用「快速」档位重试）")
        monkeypatch.setattr(engine, "compute_binding", _boom)
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        assert body["status"] == "failed"
        assert "未收敛" in body["error"]


class TestSolvents:
    def test_solvents_table(self, client, sandbox):
        r = client.get("/api/dft/solvents")
        assert r.status_code == 200
        solvents = r.json()["solvents"]
        ids = {s["id"] for s in solvents}
        assert {"toluene", "mesitylene", "dioxane", "dmf",
                "water", "chloroform", "ethanol", "heptane"} <= ids
        for s in solvents:
            assert s["name_zh"] and s["smiles"]


class TestDimerPreview:
    def test_preview_ok(self, client, sandbox):
        r = client.get("/api/dft/dimer-preview",
                       params={"ald_smiles": "O=Cc1ccccc1",
                               "amine_smiles": "Nc1ccccc1"})
        assert r.status_code == 200
        body = r.json()
        assert "=" in body["dimer_smiles"]  # 含 C=N
        assert body["multi_site"] is False

    def test_preview_multi_site(self, client, sandbox):
        r = client.get("/api/dft/dimer-preview",
                       params={"ald_smiles": "O=Cc1ccc(C=O)cc1",
                               "amine_smiles": "Nc1ccc(N)cc1"})
        assert r.json()["multi_site"] is True
        assert "示意单点缩合" in r.json()["note"]

    def test_preview_non_ald_400(self, client, sandbox):
        r = client.get("/api/dft/dimer-preview",
                       params={"ald_smiles": "c1ccccc1",
                               "amine_smiles": "Nc1ccccc1"})
        assert r.status_code == 400
        assert "醛基" in r.json()["detail"]


class TestCache:
    def test_cache_hit_on_repeat(self, client, sandbox):
        r1 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        assert len(sandbox) == 1

        r2 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        body = r2.json()
        assert body["status"] == "done"
        assert body["cached"] is True
        assert body["result"]["e_bind_kcal"] == pytest.approx(-7.5301)
        assert len(sandbox) == 1  # 引擎未再被调用

    def test_different_method_not_cached(self, client, sandbox):
        r1 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        r2 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfnff"})
        body = _wait_done(client, r2.json()["job_id"])
        assert body["cached"] is False
        assert len(sandbox) == 2

    def test_different_x_type_not_cached(self, client, sandbox):
        """缓存 key 隔离：同一对单体不同 X 类型互不命中。"""
        r1 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "self_stack", "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        r2 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "solvent", "solvent_id": "toluene", "method": "gfn2"})
        body = _wait_done(client, r2.json()["job_id"])
        assert body["cached"] is False
        assert len(sandbox) == 2
        # 同溶剂重复 → 命中
        r3 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "solvent", "solvent_id": "toluene", "method": "gfn2"})
        assert r3.json()["cached"] is True
        # 不同溶剂 → 不命中
        r4 = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "solvent", "solvent_id": "ethanol", "method": "gfn2"})
        body4 = _wait_done(client, r4.json()["job_id"])
        assert body4["cached"] is False
        assert len(sandbox) == 3


class TestGeometry:
    def test_geometry_xyz_download(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        job_id = r.json()["job_id"]
        _wait_done(client, job_id)
        g = client.get(f"/api/dft/jobs/{job_id}/geometry")
        assert g.status_code == 200
        assert g.text.startswith("3\ncomplex")

    def test_geometry_404_before_done(self, client, sandbox, monkeypatch):
        def _slow(*_a, on_stage=None, **_k):
            import threading
            threading.Event().wait(0.5)
            return dict(FAKE_RESULT)
        monkeypatch.setattr(engine, "compute_binding", _slow)
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        job_id = r.json()["job_id"]
        assert client.get(f"/api/dft/jobs/{job_id}/geometry").status_code == 404
        _wait_done(client, job_id)


class TestHistory:
    def test_history_written_and_served(self, client, sandbox, tmp_path):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        _wait_done(client, r.json()["job_id"])

        log = tmp_path / "dft_log.jsonl"
        assert log.is_file()
        lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 1
        assert lines[0]["type"] == "dft"
        assert lines[0]["status"] == "done"
        assert lines[0]["e_bind_kcal"] == pytest.approx(-7.5301)
        assert lines[0]["dimer_smiles"] == DIMER
        assert lines[0]["x_type"] == "self_stack"
        assert "自身堆积" in lines[0]["x_description"]

        h = client.get("/api/dft/history")
        assert h.status_code == 200
        body = h.json()
        assert body["count"] == 1
        entry = body["history"][0]
        assert entry["method"] == "gfn2"
        assert entry["complex_xyz"].startswith("3\n")

    def test_history_empty_when_missing(self, client, sandbox):
        h = client.get("/api/dft/history")
        assert h.json() == {"history": [], "count": 0}

    def test_history_pagination(self, client, sandbox, tmp_path):
        log = tmp_path / "dft_log.jsonl"
        log.write_text(
            "\n".join(json.dumps({"type": "dft", "i": i}) for i in range(5)) + "\n",
            encoding="utf-8")
        h = client.get("/api/dft/history?limit=2&offset=1")
        body = h.json()
        assert body["count"] == 5
        assert [e["i"] for e in body["history"]] == [3, 2]


class TestFavoriteLinkage:
    def test_result_flags_existing_favorite(self, client, sandbox, tmp_path):
        fav = fav_store.add_favorite(ALD, AMINE, ald_name="醛A", amine_name="胺B")
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        favinfo = body["result"]["favorite"]
        assert favinfo is not None
        assert favinfo["id"] == fav["id"]
        assert favinfo["has_dft"] is False

    def test_result_favorite_none_when_not_collected(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        assert body["result"]["favorite"] is None
