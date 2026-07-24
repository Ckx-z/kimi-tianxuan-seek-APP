"""实验记录增强测试：草稿暂存（draft/final）+ 实验过程时间线 + 附件。

覆盖：
- 草稿创建走宽松校验（experiment_no / outcome / 游离 SMILES 均可空）；
- 草稿继续编辑（update_record）与转正式（转正式时走完整校验）；
- 旧记录 json 缺 status/process_notes/timeline 时读取补默认值 final；
- 时间线条目清洗：entry_id 缺省生成、附件元数据按 entry_id 回接服务端；
- 附件 add/get/remove：大小限制、类型限制、下载路径、删除后摘除元数据；
- 删除记录时清理附件目录。

所有写操作通过 monkeypatch 把目录指到 tmp_path，不污染真实数据目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


class TestDraftCreate:
    def test_draft_without_experiment_no(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft")
        assert rec["status"] == "draft"
        assert rec["experiment_no"] == ""
        assert rec["outcome"] == ""
        # 草稿 notes 不加编号前缀
        assert not rec["notes"].startswith("实验编号：")

    def test_draft_orphan_without_smiles(self, rec_dirs):
        rec = rec_store.create_record(favorite_id=None, status="draft")
        assert rec["status"] == "draft"
        assert rec["favorite_id"] is None

    def test_draft_outcome_optional_but_validated(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft", outcome="film")
        assert rec["outcome"] == "film"
        with pytest.raises(ValueError):
            rec_store.create_record(
                favorite_id=fav["id"], status="draft", outcome="bogus")

    def test_final_validation_unchanged(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        with pytest.raises(ValueError):
            rec_store.create_record(favorite_id=fav["id"], status="final")
        with pytest.raises(ValueError):
            rec_store.create_record(favorite_id=None, status="final",
                                    outcome="film", experiment_no="A1")

    def test_invalid_status_rejected(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        with pytest.raises(ValueError):
            rec_store.create_record(favorite_id=fav["id"], status="wip",
                                    experiment_no="A1", outcome="film")


class TestDraftUpdateAndFinalize:
    def _make_draft(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        return rec_store.create_record(favorite_id=fav["id"], status="draft")

    def test_update_draft_fields(self, rec_dirs):
        rec = self._make_draft(rec_dirs)
        rid = rec["record_id"]
        updated = rec_store.update_record(rid, {
            "experiment_no": "A5", "strength": "脆",
            "conditions": {"solvent_1": "甲苯"},
        })
        assert updated["status"] == "draft"
        assert updated["experiment_no"] == "A5"
        assert updated["strength"] == "脆"
        assert updated["conditions"]["solvent_1"] == "甲苯"
        # 未更新字段保持
        assert updated["operator"] == ""

    def test_finalize_requires_experiment_no(self, rec_dirs):
        rec = self._make_draft(rec_dirs)
        with pytest.raises(ValueError):
            rec_store.update_record(rec["record_id"], {
                "status": "final", "outcome": "film"})

    def test_finalize_requires_outcome(self, rec_dirs):
        rec = self._make_draft(rec_dirs)
        with pytest.raises(ValueError):
            rec_store.update_record(rec["record_id"], {
                "status": "final", "experiment_no": "A5"})

    def test_finalize_success_adds_notes_prefix(self, rec_dirs):
        rec = self._make_draft(rec_dirs)
        rid = rec["record_id"]
        rec_store.update_record(rid, {"notes": "观察到晶体析出"})
        final = rec_store.update_record(rid, {
            "status": "final", "experiment_no": "A5", "outcome": "film"})
        assert final["status"] == "final"
        assert final["notes"].startswith("实验编号：A5")
        assert "晶体析出" in final["notes"]
        # 落盘持久化
        saved = json.loads(
            (rec_dirs[1] / f"{rid}.json").read_text(encoding="utf-8"))
        assert saved["status"] == "final"

    def test_finalize_orphan_requires_smiles(self, rec_dirs):
        rec = rec_store.create_record(favorite_id=None, status="draft")
        with pytest.raises(ValueError):
            rec_store.update_record(rec["record_id"], {
                "status": "final", "experiment_no": "A5", "outcome": "film"})

    def test_update_missing_record(self, rec_dirs):
        with pytest.raises(KeyError):
            rec_store.update_record("rec_20990101_001", {"notes": "x"})

    def test_finalize_notes_prefix_not_duplicated(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft", notes="实验编号：A5；已写")
        final = rec_store.update_record(rec["record_id"], {
            "status": "final", "experiment_no": "A5", "outcome": "film"})
        assert final["notes"].count("实验编号：") == 1


class TestLegacyCompat:
    def test_legacy_record_defaults_to_final(self, rec_dirs):
        _, rec_d, _ = rec_dirs
        rec_d.mkdir(parents=True)
        legacy = {
            "schema_version": "1.0", "record_type": "experiment_record",
            "record_id": "rec_20990101_001", "experiment_no": "OLD-1",
            "favorite_id": None,
            "aldehyde": {"smiles": TP, "cas": "", "name": ""},
            "amine": {"smiles": PA, "cas": "", "name": ""},
            "conditions": {}, "outcome": "film", "notes": "", "operator": "",
            "date": "2099-01-01",
        }
        (rec_d / "rec_20990101_001.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        got = rec_store.get_record("rec_20990101_001")
        assert got["status"] == "final"
        assert got["process_notes"] == ""
        assert got["timeline"] == []
        listed = rec_store.list_records()
        assert listed[0]["status"] == "final"
        # 原文件不被读取动作改写
        raw = json.loads((rec_d / "rec_20990101_001.json")
                         .read_text(encoding="utf-8"))
        assert "status" not in raw


class TestTimeline:
    def test_create_with_timeline(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft",
            process_notes="整体流程：投料→陈化→洗涤→干燥",
            timeline=[{"time_label": "第1天", "description": "投料"}])
        assert rec["process_notes"].startswith("整体流程")
        entry = rec["timeline"][0]
        assert entry["entry_id"].startswith("tl_")
        assert entry["time_label"] == "第1天"
        assert entry["attachments"] == []

    def test_update_timeline_keeps_server_attachments(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft",
            timeline=[{"time_label": "第1天", "description": "投料"}])
        rid = rec["record_id"]
        entry_id = rec["timeline"][0]["entry_id"]
        meta = rec_store.add_attachment(rid, entry_id, "photo.png", b"\x89PNG")
        # 客户端回传 timeline 时附件字段被忽略，按 entry_id 回接服务端登记值
        updated = rec_store.update_record(rid, {
            "timeline": [{"entry_id": entry_id, "time_label": "第1天",
                          "description": "投料（改）", "attachments": []}]})
        entry = updated["timeline"][0]
        assert entry["description"] == "投料（改）"
        assert [a["attachment_id"] for a in entry["attachments"]] == \
            [meta["attachment_id"]]


class TestAttachments:
    def _make(self, rec_dirs):
        fav = fav_store.add_favorite(TP, PA)
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="draft",
            timeline=[{"time_label": "第1天", "description": "投料"}])
        return rec, rec["timeline"][0]["entry_id"]

    def test_add_get_remove(self, rec_dirs):
        _, rec_d, att_d = rec_dirs
        rec, entry_id = self._make(rec_dirs)
        rid = rec["record_id"]
        meta = rec_store.add_attachment(rid, entry_id, "膜照片.png", b"\x89PNGdata")
        assert meta["attachment_id"].startswith("att_")
        assert meta["is_image"] is True
        assert meta["size"] == len(b"\x89PNGdata")
        assert (att_d / rid / f"{meta['attachment_id']}.png").is_file()
        # 元数据落盘
        saved = rec_store.get_record(rid)
        assert saved["timeline"][0]["attachments"][0]["filename"] == "膜照片.png"
        # 下载路径解析
        found = rec_store.get_attachment_path(rid, meta["attachment_id"])
        assert found is not None and found[0].is_file()
        # 删除
        assert rec_store.remove_attachment(rid, meta["attachment_id"]) is True
        assert rec_store.get_attachment_path(rid, meta["attachment_id"]) is None
        assert rec_store.get_record(rid)["timeline"][0]["attachments"] == []
        assert rec_store.remove_attachment(rid, meta["attachment_id"]) is False

    def test_size_limit(self, rec_dirs):
        rec, entry_id = self._make(rec_dirs)
        big = b"0" * (rec_store.MAX_ATTACHMENT_BYTES + 1)
        with pytest.raises(ValueError):
            rec_store.add_attachment(
                rec["record_id"], entry_id, "big.png", big)

    def test_type_restriction(self, rec_dirs):
        rec, entry_id = self._make(rec_dirs)
        with pytest.raises(ValueError):
            rec_store.add_attachment(
                rec["record_id"], entry_id, "evil.exe", b"MZ")
        with pytest.raises(ValueError):
            rec_store.add_attachment(
                rec["record_id"], entry_id, "noext", b"data")

    def test_entry_must_exist(self, rec_dirs):
        rec, _ = self._make(rec_dirs)
        with pytest.raises(KeyError):
            rec_store.add_attachment(
                rec["record_id"], "tl_nonexist", "a.png", b"x")

    def test_delete_record_cleans_attachments(self, rec_dirs):
        _, _, att_d = rec_dirs
        rec, entry_id = self._make(rec_dirs)
        rid = rec["record_id"]
        rec_store.add_attachment(rid, entry_id, "a.png", b"x")
        assert (att_d / rid).is_dir()
        assert rec_store.delete_record(rid) is True
        assert not (att_d / rid).exists()
