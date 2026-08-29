"""DFT 任务落盘与草稿端点测试（状态保持修复，2026-08-29）。

覆盖：落盘写入 / 重启恢复（done 保留结果）/ running→interrupted /
_job_public 透出 input / 草稿端点往返与空读。
不依赖真实 xtb：engine.compute_binding 用 monkeypatch 替换为秒回的假实现。
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

from api.routers import dft as dft_router  # noqa: E402

ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
AMINE = "Nc1ccc(N)cc1"

FAKE_RESULT = {
    "smiles_a": ALD,
    "smiles_b": AMINE,
    "dimer_smiles": "dimer-smiles",
    "x_type": "self_stack",
    "x_smiles": "dimer-smiles",
    "x_description": "自身堆积（二聚体·二聚体）",
    "x_cache_part": "self_stack",
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
    """隔离缓存/历史/草稿路径 + 假计算引擎。任务落盘路径由 conftest 全局隔离。"""
    monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path / "dft_cache")
    monkeypatch.setattr(dft_log, "LOG_PATH", tmp_path / "dft_log.jsonl")
    monkeypatch.setattr(engine, "xtb_binary", lambda: tmp_path / "xtb.exe")
    monkeypatch.setattr(dft_router, "_draft_path",
                        lambda: tmp_path / "dft_draft.json")

    def _fake_compute(ald_smiles, amine_smiles, method="gfn2",
                      on_stage=None, **kwargs):
        if on_stage:
            on_stage("正在优化复合物几何…")
        result = dict(FAKE_RESULT)
        result["method"] = method
        result["smiles_a"] = engine.canonicalize_smiles(ald_smiles)
        result["smiles_b"] = engine.canonicalize_smiles(amine_smiles)
        return result

    monkeypatch.setattr(engine, "compute_binding", _fake_compute)


@pytest.fixture(autouse=True)
def _clear_jobs():
    """每个测试前清空内存任务注册表，避免跨测试残留。"""
    with dft_jobs._LOCK:
        dft_jobs._JOBS.clear()
    yield
    with dft_jobs._LOCK:
        dft_jobs._JOBS.clear()


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


def _wait_store_status(store: Path, job_id: str, status: str,
                       timeout: float = 3.0) -> dict:
    """轮询落盘文件直到指定任务达到目标状态（落盘晚于内存状态可见，防竞态）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if store.is_file():
            try:
                data = json.loads(store.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get(job_id, {}).get("status") == status:
                    return data
            except Exception:
                pass
        time.sleep(0.05)
    raise AssertionError(f"落盘文件 {store} 中任务 {job_id} 未在 {timeout}s 内达到 {status}")


class TestJobPersistence:
    def test_store_file_written_and_contains_job(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        _wait_done(client, job_id)

        # 落盘路径由 conftest session 级夹具隔离，从模块取当前实际路径
        store = dft_jobs._job_store_path()
        data = _wait_store_status(store, job_id, "done")
        assert data[job_id]["ald_smiles_input"] == ALD

    def test_restore_done_job_after_restart(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE, "method": "gfn2"})
        job_id = r.json()["job_id"]
        _wait_done(client, job_id)
        # 等待 done 状态落盘（防「内存已 done、落盘仍是 running」的竞态）
        _wait_store_status(dft_jobs._job_store_path(), job_id, "done")

        # 模拟重启：清内存 → 重新加载落盘注册表
        with dft_jobs._LOCK:
            dft_jobs._JOBS.clear()
        dft_jobs.load_persisted_jobs()

        job = dft_jobs.get_job(job_id)
        assert job is not None
        assert job["status"] == "done"
        assert job["result"]["e_bind_kcal"] == pytest.approx(-7.5301)
        # API 视角同样可见（含 input 透出）
        body = client.get(f"/api/dft/jobs/{job_id}").json()
        assert body["status"] == "done"
        assert body["result"]["e_bind_kcal"] == pytest.approx(-7.5301)

    def test_load_marks_running_and_pending_as_interrupted(self, tmp_path,
                                                           monkeypatch):
        """单元级：预写包含 running/pending/done 的注册表 → 加载后状态正确。"""
        store = tmp_path / "dft_jobs.json"
        store.write_text(json.dumps({
            "job-running": {"job_id": "job-running", "status": "running",
                            "progress_hint": "正在计算", "method": "gfn2",
                            "mode": "dimer", "backend": "xtb",
                            "created_at": "2026-08-29T00:00:00+00:00"},
            "job-pending": {"job_id": "job-pending", "status": "pending",
                            "progress_hint": "排队中", "method": "gfn2",
                            "mode": "dimer", "backend": "xtb",
                            "created_at": "2026-08-29T00:00:00+00:00"},
            "job-done": {"job_id": "job-done", "status": "done",
                         "progress_hint": "计算完成", "method": "gfn2",
                         "mode": "dimer", "backend": "xtb",
                         "result": {"e_bind_kcal": -1.0},
                         "created_at": "2026-08-29T00:00:00+00:00"},
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(dft_jobs, "_job_store_path", lambda: store)

        dft_jobs.load_persisted_jobs()

        running = dft_jobs.get_job("job-running")
        assert running["status"] == "interrupted"
        assert "中断" in running["error"]
        assert dft_jobs.get_job("job-pending")["status"] == "interrupted"
        assert dft_jobs.get_job("job-done")["status"] == "done"
        assert dft_jobs.get_job("job-done")["result"]["e_bind_kcal"] == -1.0

    def test_load_ignores_missing_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dft_jobs, "_job_store_path",
                            lambda: tmp_path / "no-such-file.json")
        dft_jobs.load_persisted_jobs()  # 不抛异常
        assert dft_jobs._JOBS == {}


class TestPublicJobInput:
    def test_input_passthrough(self, client, sandbox):
        r = client.post("/api/dft/jobs", json={
            "ald_smiles": ALD, "amine_smiles": AMINE,
            "x_type": "solvent", "solvent_id": "toluene", "method": "gfn2"})
        body = _wait_done(client, r.json()["job_id"])
        inp = body["input"]
        assert inp["ald_smiles"] == ALD
        assert inp["amine_smiles"] == AMINE
        assert inp["x_type"] == "solvent"
        assert inp["solvent_id"] == "toluene"
        assert inp["custom_smiles"] is None


class TestDraftEndpoints:
    def test_draft_roundtrip(self, client, sandbox):
        draft = {
            "mode": "dimer",
            "monoA": {"smiles": ALD, "name": "醛A"},
            "monoB": {"smiles": AMINE, "name": "胺B"},
            "xType": "self_stack",
            "currentJobId": "job-123",
        }
        r = client.put("/api/dft/draft", json={"draft": draft})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        g = client.get("/api/dft/draft")
        assert g.status_code == 200
        assert g.json()["draft"] == draft

    def test_draft_empty_when_missing(self, client, sandbox):
        g = client.get("/api/dft/draft")
        assert g.status_code == 200
        assert g.json() == {"draft": None}

    def test_draft_overwrite(self, client, sandbox):
        client.put("/api/dft/draft", json={"draft": {"mode": "dimer"}})
        client.put("/api/dft/draft", json={"draft": {"mode": "pair"}})
        assert client.get("/api/dft/draft").json()["draft"] == {"mode": "pair"}
