"""取消计算与原子数预估测试（v1.5.0 修复 2/3/5）。

- _stage_percent：阶段文案带「完成」字样不再提前到 100
- POST /jobs/{id}/cancel：置位取消事件 → 子进程轮询终止 → 终态 cancelled
- GET /api/dft/atom-estimate：后端含氢原子数口径（替代前端「重原子×2」启发式）
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.dft import engine  # noqa: E402
from src.dft import jobs as dft_jobs  # noqa: E402


class TestStagePercent:
    """问题2：进度不再因「完成」字样提前满格。"""

    def test_complete_keyword_no_longer_100(self):
        assert dft_jobs._stage_percent("Psi4 初始化完成") == 65
        assert dft_jobs._stage_percent("几何优化完成") == 40
        assert dft_jobs._stage_percent("取向筛选完成") == 15

    def test_all_stage_hints_below_100(self):
        for hint in ("正在准备计算…", "正在生成缩合二聚体（醛 + 胺 → 亚胺）…",
                     "正在优化二聚体几何…", "正在生成 Psi4 输入脚本…",
                     "正在解析计算结果…", "计算完成"):
            assert dft_jobs._stage_percent(hint) < 100


class TestCancelRegistry:
    def test_request_cancel_missing_job(self):
        ok, job = dft_jobs.request_cancel("no-such-job")
        assert ok is False
        assert job is None

    def test_request_cancel_terminal_job_not_cancelable(self):
        dft_jobs._JOBS["fake-done"] = {"job_id": "fake-done", "status": "done",
                                       "progress_hint": "x"}
        try:
            ok, job = dft_jobs.request_cancel("fake-done")
            assert ok is False
            assert job["status"] == "done"
        finally:
            dft_jobs._JOBS.pop("fake-done", None)

    def test_cancel_event_reused_per_job(self):
        ev1 = dft_jobs.cancel_event_for("ev-test")
        ev2 = dft_jobs.cancel_event_for("ev-test")
        assert ev1 is ev2
        dft_jobs._CANCEL_EVENTS.pop("ev-test", None)


class TestCancelEndpoint:
    """问题3：取消端点全链路（事件置位 → 计算函数察觉 → 终态 cancelled）。"""

    @pytest.fixture()
    def client(self):
        from api.main import app
        return TestClient(app)

    def test_cancel_running_job(self, client, monkeypatch):
        started = threading.Event()

        def slow_compute(*args, cancel_event=None, **kwargs):
            started.set()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise engine.JobCancelledError("任务已取消")
                time.sleep(0.05)
            raise engine.DftError("test-slow-compute-timeout")

        monkeypatch.setattr(dft_jobs.engine, "compute_binding", slow_compute)
        # 对本机真实使用/历史验收产生的 DFT 缓存免疫：强制缓存未命中，确保起线程
        monkeypatch.setattr(dft_jobs.dft_cache, "load_cache", lambda key: None)

        # 用不常见的分子对，避免命中本机真实使用产生的 DFT 缓存
        #（缓存命中会直接 done、不起计算线程）
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": "O=Cc1ccc(OC)cc1", "amine_smiles": "Nc1ccc(C#N)cc1",
            "method": "gfnff", "backend": "xtb", "x_type": "self_stack"})
        assert r.status_code == 202
        assert r.json()["cached"] is False
        jid = r.json()["job_id"]

        deadline = time.monotonic() + 10
        while not started.is_set() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not started.is_set():
            job = client.get(f"/api/dft/jobs/{jid}").json()
            pytest.fail(f"compute not invoked; job={job}")

        r2 = client.post(f"/api/dft/jobs/{jid}/cancel")
        assert r2.status_code == 200

        job = {}
        for _ in range(100):
            job = client.get(f"/api/dft/jobs/{jid}").json()
            if job["status"] == "cancelled":
                break
            time.sleep(0.1)
        assert job["status"] == "cancelled"
        assert job["error"] == "任务已取消"

        # 已终态再取消 → 409
        r3 = client.post(f"/api/dft/jobs/{jid}/cancel")
        assert r3.status_code == 409

    def test_cancel_missing_job_404(self, client):
        assert client.post("/api/dft/jobs/nope/cancel").status_code == 404


class TestCacheDeleteV153:
    """v1.5.3：按组合删除 DFT 结果缓存（仅删目标 key，不误删其他）。"""

    @pytest.fixture()
    def client(self):
        from api.main import app
        return TestClient(app)

    def test_delete_cache_endpoint(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(dft_jobs.dft_cache, "CACHE_DIR", tmp_path)
        probe = dft_jobs.probe_cache_key("O=Cc1ccccc1", "Nc1ccccc1", "gfnff",
                                         x_type="self_stack", mode="dimer",
                                         backend="xtb")
        assert probe is not None
        key = probe[0]
        (tmp_path / f"{key}.json").write_text("{}", encoding="utf-8")
        # 另一个组合的缓存不应被误删
        other = tmp_path / "deadbeef.json"
        other.write_text("{}", encoding="utf-8")

        r = client.post("/api/dft/cache/delete", json={
            "ald_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1",
            "method": "gfnff", "backend": "xtb", "x_type": "self_stack"})
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        assert not (tmp_path / f"{key}.json").exists()
        assert other.exists()  # 其他任务数据保留

    def test_delete_missing_cache_false(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(dft_jobs.dft_cache, "CACHE_DIR", tmp_path)
        r = client.post("/api/dft/cache/delete", json={
            "ald_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1",
            "method": "gfnff", "backend": "xtb", "x_type": "self_stack"})
        assert r.status_code == 200
        assert r.json()["deleted"] is False


class TestAtomEstimateEndpoint:
    """问题5：后端含氢原子数口径。"""

    @pytest.fixture()
    def client(self):
        from api.main import app
        return TestClient(app)

    def test_pair_mode_sums_monomers(self, client):
        r = client.get("/api/dft/atom-estimate", params={
            "mode": "pair", "ald_smiles": "c1ccccc1", "amine_smiles": "O"})
        assert r.status_code == 200
        body = r.json()
        assert body["dimer_atom_count"] is None
        assert body["x_atom_count"] == 3  # H2O
        assert body["complex_atom_count"] == 15  # C6H6(12) + H2O(3)

    def test_self_stack_doubles_dimer(self, client):
        r = client.get("/api/dft/atom-estimate", params={
            "ald_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1",
            "x_type": "self_stack"})
        assert r.status_code == 200
        body = r.json()
        d = body["dimer_atom_count"]
        assert d is not None and d > 10
        assert body["x_atom_count"] == d
        assert body["complex_atom_count"] == 2 * d

    def test_invalid_smiles_returns_nulls(self, client):
        r = client.get("/api/dft/atom-estimate", params={
            "ald_smiles": "bad!", "amine_smiles": "also-bad!"})
        assert r.status_code == 200
        assert r.json()["complex_atom_count"] is None
