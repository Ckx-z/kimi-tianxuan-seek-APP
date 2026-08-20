"""实验记录新增字段测试：自我总结（self_summary）+ 本人认为的失误（mistakes）。

覆盖：
- store 级：create_record 携带新字段落盘；缺省为空串；
  update_record 可更新新字段且不影响其他字段；
  旧记录 json 缺 self_summary/mistakes 时读取补空串（兼容默认值）；
- API 级：POST /api/records 携带新字段创建；PUT /api/records/{id} 更新新字段；
  草稿也可填写新字段。

风格对齐 tests/test_records_draft_timeline.py：monkeypatch 数据目录到 tmp_path。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from favorites import store as fav_store  # noqa: E402
from records import store as rec_store  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"


@pytest.fixture
def rec_dirs(tmp_path, monkeypatch):
    fav_d = tmp_path / "favorites"
    rec_d = tmp_path / "records"
    att_d = tmp_path / "attachments"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", fav_d)
    monkeypatch.setattr(rec_store, "RECORDS_DIR", rec_d)
    monkeypatch.setattr(rec_store, "ATTACHMENTS_DIR", att_d)
    return fav_d, rec_d, att_d


class TestCreateSummaryMistakes:
    def test_create_with_summary_mistakes(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft",
            self_summary="本次陈化时间偏短，膜层偏薄",
            mistakes="加料顺序颠倒，先加了胺")
        assert rec["self_summary"] == "本次陈化时间偏短，膜层偏薄"
        assert rec["mistakes"] == "加料顺序颠倒，先加了胺"
        # 落盘持久化
        saved = json.loads(
            (rec_dirs[1] / f"{rec['record_id']}.json").read_text(encoding="utf-8"))
        assert saved["self_summary"] == "本次陈化时间偏短，膜层偏薄"
        assert saved["mistakes"] == "加料顺序颠倒，先加了胺"

    def test_create_defaults_empty(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(favorite_id=fav["id"], status="draft")
        assert rec["self_summary"] == ""
        assert rec["mistakes"] == ""

    def test_create_strips_whitespace(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft",
            self_summary="  总结  ", mistakes="  失误  ")
        assert rec["self_summary"] == "总结"
        assert rec["mistakes"] == "失误"

    def test_create_final_with_summary_mistakes(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="final",
            experiment_no="A1", outcome="film",
            self_summary="成膜良好", mistakes="无")
        assert rec["status"] == "final"
        assert rec["self_summary"] == "成膜良好"
        assert rec["mistakes"] == "无"


class TestUpdateSummaryMistakes:
    def test_update_summary_mistakes(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(favorite_id=fav["id"], status="draft")
        rid = rec["record_id"]
        updated = rec_store.update_record(rid, {
            "self_summary": "更新后的自我总结",
            "mistakes": "更新后的失误",
        })
        assert updated["self_summary"] == "更新后的自我总结"
        assert updated["mistakes"] == "更新后的失误"
        # 未更新字段保持
        assert updated["notes"] == ""
        assert updated["status"] == "draft"
        # 落盘持久化
        saved = json.loads(
            (rec_dirs[1] / f"{rid}.json").read_text(encoding="utf-8"))
        assert saved["self_summary"] == "更新后的自我总结"
        assert saved["mistakes"] == "更新后的失误"

    def test_update_partial_keeps_other_field(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft",
            self_summary="原总结", mistakes="原失误")
        updated = rec_store.update_record(rec["record_id"], {
            "self_summary": "只改总结"})
        assert updated["self_summary"] == "只改总结"
        assert updated["mistakes"] == "原失误"


class TestLegacyCompatSummaryMistakes:
    def test_legacy_record_defaults_empty(self, rec_dirs):
        _, rec_d, _ = rec_dirs
        rec_d.mkdir(parents=True)
        legacy = {
            "schema_version": "1.0", "record_type": "experiment_record",
            "record_id": "rec_20990101_002", "experiment_no": "OLD-2",
            "favorite_id": None,
            "aldehyde": {"smiles": TP, "cas": "", "name": ""},
            "amine": {"smiles": PA, "cas": "", "name": ""},
            "conditions": {}, "outcome": "film", "notes": "", "operator": "",
            "date": "2099-01-01",
        }
        (rec_d / "rec_20990101_002.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        got = rec_store.get_record("rec_20990101_002")
        assert got["self_summary"] == ""
        assert got["mistakes"] == ""
        listed = rec_store.list_records()
        assert listed[0]["self_summary"] == ""
        assert listed[0]["mistakes"] == ""
        # 原文件不被读取动作改写
        raw = json.loads((rec_d / "rec_20990101_002.json")
                         .read_text(encoding="utf-8"))
        assert "self_summary" not in raw
        assert "mistakes" not in raw


# ---------------------------------------------------------------- API 级

from api.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favs")
    monkeypatch.setattr(rec_store, "RECORDS_DIR", tmp_path / "recs")
    monkeypatch.setattr(rec_store, "ATTACHMENTS_DIR", tmp_path / "atts")


def _create_favorite() -> str:
    r = client.post("/api/favorites", json={
        "aldehyde_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1"})
    assert r.status_code == 201
    return r.json()["id"]


class TestApiSummaryMistakes:
    def test_post_with_summary_mistakes(self, isolated_data):
        fid = _create_favorite()
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "draft",
            "self_summary": "草稿里的自我总结",
            "mistakes": "草稿里的失误",
            "timeline": [{"time_label": "第1天", "description": "投料"}]})
        assert r.status_code == 201
        rec = r.json()
        assert rec["self_summary"] == "草稿里的自我总结"
        assert rec["mistakes"] == "草稿里的失误"
        # GET 详情/列表均带新字段
        got = client.get(f"/api/records/{rec['record_id']}").json()
        assert got["self_summary"] == "草稿里的自我总结"
        assert got["mistakes"] == "草稿里的失误"
        listed = client.get("/api/records").json()["records"]
        assert listed[0]["self_summary"] == "草稿里的自我总结"
        assert listed[0]["mistakes"] == "草稿里的失误"

    def test_post_omitted_defaults_empty(self, isolated_data):
        fid = _create_favorite()
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "draft"})
        assert r.status_code == 201
        rec = r.json()
        assert rec["self_summary"] == ""
        assert rec["mistakes"] == ""

    def test_put_summary_mistakes(self, isolated_data):
        fid = _create_favorite()
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "draft"})
        assert r.status_code == 201
        rid = r.json()["record_id"]
        r = client.put(f"/api/records/{rid}", json={
            "self_summary": "PUT 更新的自我总结",
            "mistakes": "PUT 更新的失误"})
        assert r.status_code == 200
        rec = r.json()
        assert rec["self_summary"] == "PUT 更新的自我总结"
        assert rec["mistakes"] == "PUT 更新的失误"

    def test_put_partial_keeps_other_field(self, isolated_data):
        fid = _create_favorite()
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "draft",
            "self_summary": "原总结", "mistakes": "原失误"})
        rid = r.json()["record_id"]
        r = client.put(f"/api/records/{rid}", json={"mistakes": "只改失误"})
        assert r.status_code == 200
        rec = r.json()
        assert rec["self_summary"] == "原总结"
        assert rec["mistakes"] == "只改失误"

    def test_finalize_keeps_summary_mistakes(self, isolated_data):
        fid = _create_favorite()
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "draft",
            "self_summary": "草稿总结", "mistakes": "草稿失误"})
        rid = r.json()["record_id"]
        r = client.put(f"/api/records/{rid}", json={
            "status": "final", "experiment_no": "A9", "outcome": "film"})
        assert r.status_code == 200
        rec = r.json()
        assert rec["status"] == "final"
        assert rec["self_summary"] == "草稿总结"
        assert rec["mistakes"] == "草稿失误"
