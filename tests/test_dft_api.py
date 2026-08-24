"""DFT API 测试：任务生命周期 / 缓存命中 / 历史 / 收藏联动 / 几何下载。

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
from src.dft import engine  # noqa: E402
from src.dft import jobs as dft_jobs  # noqa: E402
from src.dft import log as dft_log  # noqa: E402
from favorites import store as fav_store  # noqa: E402

ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
AMINE = "Nc1ccc(N)cc1"

FAKE_RESULT = {
    "smiles_a": engine.canonicalize_smiles(ALD),
    "smiles_b": engine.canonicalize_smiles(AMINE),
    "method": "gfn2",
    "method_label": "GFN2-xTB（精确）",
    "e_bind_hartree": -0.012,
    "e_bind_kcal": -7.5301,
    "e_bind_kj": -31.506,
    "energies_hartree": {"a": -100.0, "b": -50.0, "complex": -150.012},
    "gap_ev": {"a": 5.0, "b": 6.1, "complex": 4.2},
    "dipole_debye": {"a": 0.1, "b": 1.5, "complex": 1.2},
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

    def _fake_compute(smiles_a, smiles_b, method="gfn2", on_stage=None,
                      jobs_root=None):
        calls.append((smiles_a, smiles_b, method))
        if on_stage:
            on_stage("正在优化单体 A 几何…")
            on_stage("正在优化复合物几何…")
        result = dict(FAKE_RESULT)
        result["method"] = method
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
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        body = _wait_done(client, job_id)
        assert body["status"] == "done"
        assert body["cached"] is False
        res = body["result"]
        assert res["e_bind_kcal"] == pytest.approx(-7.5301)
        assert res["e_bind_kj"] == pytest.approx(-31.506)
        assert res["gap_ev"]["complex"] == pytest.approx(4.2)
        assert res["dipole_debye"]["b"] == pytest.approx(1.5)
        assert res["energies_hartree"]["complex"] == pytest.approx(-150.012)
        assert len(sandbox) == 1

    def test_progress_hint_updated(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        assert body["progress_hint"] == "计算完成"

    def test_default_method_is_gfn2(self, client, sandbox):
        r = client.post("/api/dft/jobs",
                        json={"smiles_a": ALD, "smiles_b": AMINE})
        assert r.status_code == 202
        assert r.json()["method"] == "gfn2"

    def test_invalid_method_400(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "b3lyp"})
        assert r.status_code == 400

    def test_empty_smiles_400(self, client, sandbox):
        r = client.post("/api/dft/jobs",
                        json={"smiles_a": "", "smiles_b": AMINE})
        assert r.status_code == 400

    def test_engine_missing_503(self, client, sandbox, monkeypatch,
                                tmp_path):
        monkeypatch.setattr(engine, "xtb_binary", lambda: None)
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        assert r.status_code == 503
        assert "未安装计算引擎" in r.json()["detail"]

    def test_unknown_job_404(self, client):
        assert client.get("/api/dft/jobs/no-such-job").status_code == 404

    def test_failed_job_chinese_error(self, client, sandbox, monkeypatch):
        def _boom(*_a, **_k):
            raise engine.DftError("几何优化未收敛或计算中途失败（可尝试改用「快速」档位重试）")
        monkeypatch.setattr(engine, "compute_binding", _boom)
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        assert body["status"] == "failed"
        assert "未收敛" in body["error"]


class TestCache:
    def test_cache_hit_on_repeat_and_swapped_pair(self, client, sandbox):
        r1 = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        assert len(sandbox) == 1

        # 同一对（顺序互换）→ 缓存命中，不再调用计算引擎
        r2 = client.post("/api/dft/jobs", json={
            "smiles_a": AMINE, "smiles_b": ALD, "method": "gfn2"})
        body = r2.json()
        assert body["status"] == "done"
        assert body["cached"] is True
        assert body["result"]["e_bind_kcal"] == pytest.approx(-7.5301)
        assert len(sandbox) == 1  # 引擎未再被调用

    def test_different_method_not_cached(self, client, sandbox):
        r1 = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        _wait_done(client, r1.json()["job_id"])
        r2 = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfnff"})
        body = _wait_done(client, r2.json()["job_id"])
        assert body["cached"] is False
        assert len(sandbox) == 2


class TestGeometry:
    def test_geometry_xyz_download(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        job_id = r.json()["job_id"]
        _wait_done(client, job_id)
        g = client.get(f"/api/dft/jobs/{job_id}/geometry")
        assert g.status_code == 200
        assert g.text.startswith("3\ncomplex")

    def test_geometry_404_before_done(self, client, sandbox, monkeypatch):
        event = []

        def _slow(*_a, on_stage=None, **_k):
            import threading
            threading.Event().wait(0.5)
            return dict(FAKE_RESULT)
        monkeypatch.setattr(engine, "compute_binding", _slow)
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        job_id = r.json()["job_id"]
        assert client.get(f"/api/dft/jobs/{job_id}/geometry").status_code == 404
        _wait_done(client, job_id)


class TestHistory:
    def test_history_written_and_served(self, client, sandbox, tmp_path):
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        _wait_done(client, r.json()["job_id"])

        log = tmp_path / "dft_log.jsonl"
        assert log.is_file()
        lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 1
        assert lines[0]["type"] == "dft"
        assert lines[0]["status"] == "done"
        assert lines[0]["e_bind_kcal"] == pytest.approx(-7.5301)

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
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        favinfo = body["result"]["favorite"]
        assert favinfo is not None
        assert favinfo["id"] == fav["id"]
        assert favinfo["has_dft"] is False

    def test_result_favorite_none_when_not_collected(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "smiles_a": ALD, "smiles_b": AMINE, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        assert body["result"]["favorite"] is None

    def test_swapped_pair_also_matches_favorite(self, client, sandbox):
        """DFT 输入无序：B/A 顺序提交也能命中已收藏的 A/B 组合。"""
        fav_store.add_favorite(ALD, AMINE)
        r = client.post("/api/dft/jobs", json={
            "smiles_a": AMINE, "smiles_b": ALD, "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        assert body["result"]["favorite"] is not None
