"""GET /api/predict/history 测试：倒序 / limit+offset 分页 / 空文件容错 /
非 prediction 行过滤 / 收藏携带打分快照落盘。

日志路径 monkeypatch 到 tmp_path，不碰真实 data/prediction_log.jsonl。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import utils.predict_log as predict_log  # noqa: E402
from favorites import store as fav_store  # noqa: E402


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


def _pred(i: int) -> dict:
    return {
        "schema_version": "1.0",
        "type": "prediction",
        "ald_smiles": f"ALD{i}",
        "amine_smiles": f"AMI{i}",
        "score": 0.5 + i * 0.1,
        "score_policy": "max_tree_gnn",
        "tree_score": 0.5 + i * 0.1,
        "gnn_score": None,
        "std": 0.05,
        "arm": "tree_v4",
        "route": "both_seen",
        "ood_level": "in",
        "source": "api_single",
        "timestamp": f"2026-01-0{i + 1}T10:00:00",
    }


def _write_log(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")


def test_history_empty_when_file_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(predict_log, "LOG_PATH", tmp_path / "no_such.jsonl")
    r = client.get("/api/predict/history")
    assert r.status_code == 200
    assert r.json() == {"history": [], "count": 0}


def test_history_reversed_and_fields(client, tmp_path, monkeypatch):
    log = tmp_path / "prediction_log.jsonl"
    # 混入非 prediction 行与损坏行：应被过滤/跳过
    records = [_pred(i) for i in range(4)]
    records.append({"type": "suggestion", "timestamp": "2026-01-09T00:00:00"})
    _write_log(log, records)
    log.write_text(log.read_text(encoding="utf-8") + "{bad json\n", encoding="utf-8")
    monkeypatch.setattr(predict_log, "LOG_PATH", log)

    r = client.get("/api/predict/history")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 4  # 只计 prediction 行
    hist = body["history"]
    assert [h["ald_smiles"] for h in hist] == ["ALD3", "ALD2", "ALD1", "ALD0"]
    top = hist[0]
    # 回显所需字段齐全
    for k in ("ald_smiles", "amine_smiles", "score", "tree_score",
              "ood_level", "timestamp", "score_policy"):
        assert k in top
    assert top["score"] == pytest.approx(0.8)


def test_history_limit_and_offset(client, tmp_path, monkeypatch):
    log = tmp_path / "prediction_log.jsonl"
    _write_log(log, [_pred(i) for i in range(5)])
    monkeypatch.setattr(predict_log, "LOG_PATH", log)

    r = client.get("/api/predict/history?limit=2")
    body = r.json()
    assert body["count"] == 5
    assert [h["ald_smiles"] for h in body["history"]] == ["ALD4", "ALD3"]

    r = client.get("/api/predict/history?limit=2&offset=2")
    assert [h["ald_smiles"] for h in r.json()["history"]] == ["ALD2", "ALD1"]

    r = client.get("/api/predict/history?limit=2&offset=4")
    assert [h["ald_smiles"] for h in r.json()["history"]] == ["ALD0"]

    r = client.get("/api/predict/history?limit=2&offset=99")
    assert r.json()["history"] == []


# ---------------------------------------------------------------- 收藏携带打分快照

@pytest.fixture()
def fav_dir(tmp_path, monkeypatch):
    d = tmp_path / "favorites"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", d)
    return d


def test_add_favorite_with_prediction_snapshot(fav_dir):
    """收藏时携带当前打分结果 → latest_prediction 直接落盘（我的页不再未打分）。"""
    fav = fav_store.add_favorite(
        "O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1",
        prediction={"score": 0.83, "std": 0.04, "ood": "in",
                    "score_policy": "max_tree_gnn", "tree_score": 0.83})
    snap = fav["latest_prediction"]
    assert snap is not None
    assert snap["score"] == pytest.approx(0.83)
    assert snap["tree_score"] == pytest.approx(0.83)
    assert snap["score_policy"] == "max_tree_gnn"
    assert snap["date"]
    # 落盘后读回一致
    loaded = fav_store.get_favorite(fav["id"])
    assert loaded["latest_prediction"]["score"] == pytest.approx(0.83)


def test_add_favorite_without_prediction_keeps_none(fav_dir):
    """不带快照的旧调用方式保持兼容（latest_prediction=None）。"""
    fav = fav_store.add_favorite("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1")
    assert fav["latest_prediction"] is None
