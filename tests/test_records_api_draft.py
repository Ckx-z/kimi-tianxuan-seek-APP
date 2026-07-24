"""实验记录增强 API 测试：草稿暂存 + 转正式 + 时间线附件接口。

数据目录打到临时目录，不碰真实数据。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    from favorites import store as fav_store
    from records import store as rec_store
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favs")
    monkeypatch.setattr(rec_store, "RECORDS_DIR", tmp_path / "recs")
    monkeypatch.setattr(rec_store, "ATTACHMENTS_DIR", tmp_path / "atts")
    return fav_store, rec_store


def _create_favorite() -> str:
    r = client.post("/api/favorites", json={
        "aldehyde_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1"})
    assert r.status_code == 201
    return r.json()["id"]


class TestDraftFlow:
    def test_draft_create_then_finalize(self, isolated_data):
        fid = _create_favorite()
        # 草稿：编号/结果均可空
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "draft"})
        assert r.status_code == 201
        rec = r.json()
        assert rec["status"] == "draft"
        assert rec["experiment_no"] == ""
        rid = rec["record_id"]
        # 列表/详情读取均带 status
        assert client.get("/api/records").json()["records"][0]["status"] == "draft"
        # 继续编辑（仍为草稿）
        r = client.put(f"/api/records/{rid}", json={
            "experiment_no": "A5", "notes": "初步观察"})
        assert r.status_code == 200
        assert r.json()["status"] == "draft"
        # 缺 outcome 转正式 → 400
        r = client.put(f"/api/records/{rid}", json={"status": "final"})
        assert r.status_code == 400
        # 补齐转正式
        r = client.put(f"/api/records/{rid}", json={
            "status": "final", "outcome": "film"})
        assert r.status_code == 200
        final = r.json()
        assert final["status"] == "final"
        assert final["notes"].startswith("实验编号：A5")

    def test_final_create_still_requires_experiment_no(self, isolated_data):
        fid = _create_favorite()
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "final", "outcome": "film"})
        assert r.status_code == 400

    def test_update_missing_record_404(self, isolated_data):
        r = client.put("/api/records/rec_20990101_001", json={"notes": "x"})
        assert r.status_code == 404


class TestTimelineAndAttachments:
    def _make_draft_with_entry(self):
        fid = _create_favorite()
        r = client.post("/api/records", json={
            "favorite_id": fid, "status": "draft",
            "process_notes": "投料→陈化→干燥",
            "timeline": [{"time_label": "第1天", "description": "投料"}]})
        assert r.status_code == 201
        rec = r.json()
        return rec["record_id"], rec["timeline"][0]["entry_id"]

    def test_upload_download_delete_attachment(self, isolated_data):
        rid, entry_id = self._make_draft_with_entry()
        # 上传（multipart）
        r = client.post(
            f"/api/records/{rid}/attachments",
            data={"entry_id": entry_id},
            files={"file": ("photo.png", io.BytesIO(b"\x89PNGfake"), "image/png")})
        assert r.status_code == 201
        meta = r.json()
        att_id = meta["attachment_id"]
        assert meta["is_image"] is True
        # 记录详情含附件元数据
        rec = client.get(f"/api/records/{rid}").json()
        atts = rec["timeline"][0]["attachments"]
        assert [a["attachment_id"] for a in atts] == [att_id]
        # 下载
        r = client.get(f"/api/records/{rid}/attachments/{att_id}")
        assert r.status_code == 200
        assert r.content == b"\x89PNGfake"
        assert r.headers["content-type"].startswith("image/png")
        # 删除
        assert client.delete(
            f"/api/records/{rid}/attachments/{att_id}").status_code == 200
        assert client.get(
            f"/api/records/{rid}/attachments/{att_id}").status_code == 404

    def test_upload_type_rejected(self, isolated_data):
        rid, entry_id = self._make_draft_with_entry()
        r = client.post(
            f"/api/records/{rid}/attachments",
            data={"entry_id": entry_id},
            files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
        assert r.status_code == 400

    def test_upload_bad_entry_404(self, isolated_data):
        rid, _ = self._make_draft_with_entry()
        r = client.post(
            f"/api/records/{rid}/attachments",
            data={"entry_id": "tl_nope"},
            files={"file": ("a.png", io.BytesIO(b"x"), "image/png")})
        assert r.status_code == 404

    def test_update_process_notes_and_timeline(self, isolated_data):
        rid, entry_id = self._make_draft_with_entry()
        r = client.put(f"/api/records/{rid}", json={
            "process_notes": "更新后的完整流程",
            "timeline": [
                {"entry_id": entry_id, "time_label": "第1天",
                 "description": "投料完成"},
                {"time_label": "第3天", "description": "出现膜层"},
            ]})
        assert r.status_code == 200
        rec = r.json()
        assert rec["process_notes"] == "更新后的完整流程"
        assert len(rec["timeline"]) == 2
        assert rec["timeline"][1]["entry_id"].startswith("tl_")
