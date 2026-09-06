"""GNN 成膜打分修正机制（v1.8.0）测试：反馈库 / registry / 解析优先级 / API。

路径全部隔离到 tmp_path；重训启动一律打桩，绝不真正拉起训练。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.predictor import gnn_feedback, gnn_jobs  # noqa: E402
from src.predictor import gnn_model  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"       # Tp 三醛
PA = "Nc1ccc(N)cc1"                      # 对苯二胺
TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(gnn_feedback, "FEEDBACK_PATH",
                        tmp_path / "feedback" / "gnn_feedback.jsonl")
    gnn_feedback._base_keys = None
    gnn_feedback._base_load_failed = False
    monkeypatch.setattr(gnn_jobs, "GNN_MODELS_DIR", tmp_path / "gnn_models")
    monkeypatch.setattr(gnn_jobs, "JOBS_DIR", tmp_path / "gnn_jobs")
    monkeypatch.setattr(gnn_jobs, "REGISTRY_PATH",
                        tmp_path / "gnn_models" / "registry.json")
    # 隔离随包 registry（v1.8.0 双通道：models/gnn_feedback/registry.json
    # 存在时会被 load_registry 读到，测试必须指向 tmp）
    monkeypatch.setattr(gnn_jobs, "BUNDLED_FEEDBACK_DIR",
                        tmp_path / "models_feedback")
    yield tmp_path


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


# ---------------------------------------------------------------- 反馈库

def test_submit_and_validate():
    rec = gnn_feedback.submit(TFPT, PA, 1.0, note="文献成膜", source="score_correction")
    assert rec["status"] == "pending"
    assert rec["can_network"] is True
    with pytest.raises(ValueError):
        gnn_feedback.submit("", PA, 1.0)
    with pytest.raises(ValueError):
        gnn_feedback.submit(TFPT, PA, 0.7)
    with pytest.raises(ValueError):
        gnn_feedback.submit(TFPT, PA, 1.0, source="unknown")


def test_confirm_dedupe_and_conflict():
    a = gnn_feedback.submit(TFPT, PA, 1.0, note="文献")
    rec = gnn_feedback.confirm(a["feedback_id"])
    assert rec["status"] == "confirmed"
    # 同组合不同标签 → conflict
    b = gnn_feedback.submit(TFPT, PA, 0.0, note="质疑")
    rec2 = gnn_feedback.confirm(b["feedback_id"])
    assert rec2["status"] == "conflict"
    assert rec2["dedupe"]["existing_label"] == 1.0
    # 改标签后重新确认 → 解除冲突
    gnn_feedback.update_feedback(b["feedback_id"], label=1.0)
    assert gnn_feedback.confirm(b["feedback_id"])["status"] == "confirmed"


def test_reject_and_delete():
    a = gnn_feedback.submit(TFPT, PA, 1.0)
    assert gnn_feedback.reject(a["feedback_id"])["status"] == "rejected"
    assert gnn_feedback.delete_feedback(a["feedback_id"]) is True
    assert gnn_feedback.delete_feedback(a["feedback_id"]) is False


def test_export_feedback_csv(tmp_path):
    gnn_feedback.submit(TFPT, PA, 1.0)
    gnn_feedback.submit(TFPT, "Nc1ccccc1", 0.0)  # 单官能胺（未确认不入导出）
    fb = gnn_feedback.confirm(gnn_feedback.list_feedback()[0]["feedback_id"])
    path, n = gnn_feedback.export_feedback_csv(tmp_path / "out.csv")
    assert n == 1
    import csv
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert rows[0]["aldehyde_smiles"] == TFPT
    assert rows[0]["is_film"] == "1"


# ---------------------------------------------------------------- registry

def test_registry_activate_roundtrip(tmp_path):
    ver = "gnn_v5.5_20260905_100000"
    (tmp_path / "gnn_models" / ver).mkdir(parents=True)
    (tmp_path / "gnn_models" / ver / "v5_model.pt").write_text("x")
    reg = {"active": "gnn_v5.4", "versions": [{"version": ver, "status": "rejected"}]}
    gnn_jobs.save_registry(reg)
    assert gnn_jobs.active_version() == "gnn_v5.4"
    assert gnn_jobs.active_checkpoint() is None
    out = gnn_jobs.activate_version(ver)
    assert out["active"] == ver
    assert gnn_jobs.active_checkpoint() == tmp_path / "gnn_models" / ver / "v5_model.pt"
    # 未知版本 → None
    assert gnn_jobs.activate_version("gnn_v9.9_x") is None
    # 回退 base
    assert gnn_jobs.activate_version("gnn_v5.4")["active"] == "gnn_v5.4"


# ---------------------------------------------------------------- gnn_model 解析优先级

def test_env_checkpoint_highest_priority(monkeypatch, tmp_path):
    ckpt = tmp_path / "override.pt"
    ckpt.write_text("x")
    monkeypatch.setenv("COF_GNN_CHECKPOINT", str(ckpt))
    monkeypatch.setattr(gnn_model, "_active_retrained_checkpoint", lambda: None)
    script, resolved = gnn_model._resolve_runtime()
    assert resolved == ckpt
    monkeypatch.delenv("COF_GNN_CHECKPOINT")


def test_registry_active_version_used(monkeypatch, tmp_path):
    ckpt = tmp_path / "active_v55.pt"
    monkeypatch.setattr(gnn_model, "_active_retrained_checkpoint", lambda: ckpt)
    monkeypatch.setattr(gnn_model, "_active_model_name", lambda: "gnn_v5.5_test")
    script, resolved = gnn_model._resolve_runtime()
    assert resolved == ckpt
    assert script == gnn_model.BUNDLED_SCRIPT or script == gnn_model.LEGACY_SCRIPT


def test_no_default_checkpoint_override_bug(monkeypatch, tmp_path):
    """v1.8.0 回归：未显式传 checkpoint 时，predict_single 不得用
    DEFAULT_CHECKPOINT 覆盖 registry/动态解析结果（pilot 真机 bug：
    版本名显示新版本、实际权重仍是 v5.4）。"""
    script = tmp_path / "gnn_runtime" / "predict_pair.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("")
    active = tmp_path / "gnn_models" / "v55" / "v5_model.pt"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text("")
    monkeypatch.setattr(gnn_model, "_find_python", lambda: tmp_path / "py.exe")
    monkeypatch.setattr(gnn_model, "_resolve_runtime", lambda: (script, active))

    captured = {}

    def fake_run(cmd, cwd, capture_output, timeout):
        captured["cmd"] = cmd
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0,
                               stdout="成膜概率: 0.9\n不确定性: ±0.01\n".encode(),
                               stderr=b"")

    monkeypatch.setattr(gnn_model.subprocess, "run", fake_run)
    p = gnn_model.GNNFilmPredictor()  # 无显式 checkpoint
    assert p.checkpoint_path is None
    p.predict_single("O=Cc1ccccc1", "Nc1ccccc1")
    assert Path(captured["cmd"][7]) == active  # --model 必须用动态解析结果


# ---------------------------------------------------------------- API

def test_feedback_api_crud(client):
    r = client.post("/api/gnn/feedback", json={
        "ald_smiles": TFPT, "amine_smiles": PA, "label": 1.0,
        "note": "文献成膜", "source": "score_correction"})
    assert r.status_code == 201
    fid = r.json()["feedback_id"]

    lst = client.get("/api/gnn/feedback").json()
    assert lst["count"] == 1 and lst["feedback"][0]["feedback_id"] == fid

    # 先改理由（pending 态可改），再确认
    up = client.patch(f"/api/gnn/feedback/{fid}", json={"note": "补充依据"})
    assert up.json()["note"] == "补充依据"

    c = client.post(f"/api/gnn/feedback/{fid}/confirm")
    assert c.json()["status"] == "confirmed"

    d = client.delete(f"/api/gnn/feedback/{fid}")
    assert d.json()["deleted"] is True
    assert client.get("/api/gnn/feedback").json()["count"] == 0


def test_feedback_api_bad_input(client):
    assert client.post("/api/gnn/feedback", json={
        "ald_smiles": "", "amine_smiles": PA, "label": 1.0}).status_code == 400
    assert client.patch("/api/gnn/feedback/fb_000000000000",
                        json={"note": "x"}).status_code == 404


def test_env_and_versions_api(client):
    env = client.get("/api/gnn/env").json()
    assert "available" in env and "active_version" in env
    assert env["active_version"] == "gnn_v5.4"
    ver = client.get("/api/gnn/versions").json()
    assert ver["active"] == "gnn_v5.4"


def test_retrain_api_degrades_when_env_missing(client, monkeypatch):
    monkeypatch.setattr(gnn_jobs, "env_ready",
                        lambda: {"available": False, "gnn_python": None,
                                 "reason": "未找到 dphuanjing 推理环境或训练资产"})
    r = client.post("/api/gnn/retrain", json={})
    assert r.status_code == 503
    assert "dphuanjing" in r.json()["detail"]


def test_retrain_requires_confirmed_feedback(monkeypatch, tmp_path):
    monkeypatch.setattr(gnn_jobs, "env_ready",
                        lambda: {"available": True, "gnn_python": "py",
                                 "reason": ""})
    with pytest.raises(RuntimeError, match="没有已确认的反馈样本"):
        gnn_jobs.start_retrain()


def test_start_retrain_builds_command(monkeypatch, tmp_path):
    monkeypatch.setattr(gnn_jobs, "env_ready",
                        lambda: {"available": True, "gnn_python": "py",
                                 "reason": ""})
    monkeypatch.setattr(gnn_jobs, "_find_anaconda_python",
                        lambda: Path("E:/ANACONDA/python.exe"))
    rec = gnn_feedback.submit(TFPT, PA, 1.0)
    gnn_feedback.confirm(rec["feedback_id"])

    captured = {}

    class _FakeProc:
        pid = 4242

    def _fake_popen(cmd, cwd, stdout, stderr, creationflags):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return _FakeProc()

    monkeypatch.setattr(gnn_jobs.subprocess, "Popen", _fake_popen)
    job = gnn_jobs.start_retrain(freeze=2, epochs=12)
    assert job["status"] == "running"
    assert job["feedback_count"] == 1
    assert job["pid"] == 4242
    assert "--feedback-csv" in captured["cmd"]
    assert "--freeze" in captured["cmd"] and "2" in captured["cmd"]
    assert any("run_job.py" in str(x) for x in captured["cmd"])
    # job 可查询（含进度合并）
    got = gnn_jobs.get_job(job["job_id"])
    assert got["version"].startswith("gnn_v5.5_")
    # 运行中再启动 → 拒绝
    with pytest.raises(RuntimeError, match="已有运行中的重训任务"):
        gnn_jobs.start_retrain()
    # 取消
    assert gnn_jobs.cancel_job(job["job_id"]) is True
    assert gnn_jobs.get_job(job["job_id"])["status"] == "cancelled"
