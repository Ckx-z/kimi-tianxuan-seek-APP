"""正式记录整体修改测试：PUT /api/records/{id} 全字段编辑（问题1）。

覆盖：
- store 级：update_record 对 final 记录可更新全部可编辑字段
  （experiment_no / outcome / strength / notes / operator / process_notes /
  self_summary / mistakes / conditions / timeline）；
  修改 experiment_no 时 notes 中残留的「实验编号：」前缀同步刷新；
- API 级：PUT 对 final 记录整体修改生效并持久化；
  final 校验不放松（experiment_no 必填 / outcome 三选）；
  PUT 后 GET 详情与列表读回一致。

数据目录 monkeypatch 到 tmp_path，不碰真实数据。
"""

from __future__ import annotations

import json

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


def _create_final(fid: str) -> dict:
    r = client.post("/api/records", json={
        "favorite_id": fid, "status": "final",
        "experiment_no": "A1", "outcome": "film",
        "strength": "中", "operator": "张三", "notes": "首次尝试",
        "conditions": {"solvent_1": "均三甲苯", "temperature_c": "120"},
        "self_summary": "初版总结", "mistakes": "初版失误",
        "process_notes": "投料→陈化",
        "timeline": [{"time_label": "第1天", "description": "投料"}]})
    assert r.status_code == 201
    return r.json()


class TestStoreFullEdit:
    """store 级：正式记录全字段更新 + 编号前缀刷新。"""

    def test_final_record_all_fields_updatable(self, isolated_data):
        _, rec_store = isolated_data
        from favorites import store as fav_store
        fav = fav_store.add_favorite("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1")
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="final",
            experiment_no="A1", outcome="film", notes="旧备注",
            self_summary="旧总结", mistakes="旧失误")
        rid = rec["record_id"]
        updated = rec_store.update_record(rid, {
            "experiment_no": "B2",
            "outcome": "partial",
            "strength": "高",
            "notes": "改后备注",
            "operator": "李四",
            "process_notes": "改后流程",
            "self_summary": "改后总结",
            "mistakes": "改后失误",
            "conditions": {"solvent_1": "邻二氯苯", "catalyst": "乙酸"},
            "timeline": [{"time_label": "第2天", "description": "出现膜层"}],
        })
        assert updated["status"] == "final"
        assert updated["experiment_no"] == "B2"
        assert updated["outcome"] == "partial"
        assert updated["strength"] == "高"
        assert updated["operator"] == "李四"
        assert updated["process_notes"] == "改后流程"
        assert updated["self_summary"] == "改后总结"
        assert updated["mistakes"] == "改后失误"
        assert updated["conditions"]["solvent_1"] == "邻二氯苯"
        assert updated["conditions"]["catalyst"] == "乙酸"
        assert len(updated["timeline"]) == 1
        # 编号变更后 notes 前缀同步刷新，旧编号不残留
        assert updated["notes"].startswith("实验编号：B2")
        assert "A1" not in updated["notes"]

    def test_final_edit_requires_experiment_no(self, isolated_data):
        _, rec_store = isolated_data
        from favorites import store as fav_store
        fav = fav_store.add_favorite("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1")
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="final",
            experiment_no="A1", outcome="film")
        with pytest.raises(ValueError):
            rec_store.update_record(rec["record_id"], {"experiment_no": "  "})

    def test_final_edit_rejects_bad_outcome(self, isolated_data):
        _, rec_store = isolated_data
        from favorites import store as fav_store
        fav = fav_store.add_favorite("O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1")
        rec = rec_store.create_record(
            favorite_id=fav["id"], status="final",
            experiment_no="A1", outcome="film")
        with pytest.raises(ValueError):
            rec_store.update_record(rec["record_id"], {"outcome": "unknown"})


class TestApiFullEdit:
    """API 级：PUT /api/records/{id} 正式记录整体修改。"""

    def test_put_final_all_fields(self, isolated_data):
        fid = _create_favorite()
        rec = _create_final(fid)
        rid = rec["record_id"]

        r = client.put(f"/api/records/{rid}", json={
            "experiment_no": "B7",
            "outcome": "failed",
            "strength": "脆",
            "notes": "后补：干燥过度",
            "operator": "王五",
            "process_notes": "投料→陈化→干燥（改）",
            "self_summary": "后补的自我总结",
            "mistakes": "后补的失误反思",
            "conditions": {"solvent_1": "均三甲苯", "solvent_2": "乙醇",
                           "temperature_c": "100"},
            "timeline": [{"time_label": "第1天", "description": "投料"},
                         {"time_label": "第5天", "description": "膜碎裂"}],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "final"
        assert body["experiment_no"] == "B7"
        assert body["outcome"] == "failed"
        assert body["strength"] == "脆"
        assert body["operator"] == "王五"
        assert body["process_notes"] == "投料→陈化→干燥（改）"
        assert body["self_summary"] == "后补的自我总结"
        assert body["mistakes"] == "后补的失误反思"
        assert body["conditions"]["solvent_2"] == "乙醇"
        assert len(body["timeline"]) == 2
        # notes 前缀刷新为新编号
        assert body["notes"].startswith("实验编号：B7；")
        assert "A1" not in body["notes"]

        # GET 详情读回一致（持久化）
        got = client.get(f"/api/records/{rid}").json()
        assert got["experiment_no"] == "B7"
        assert got["self_summary"] == "后补的自我总结"
        assert got["mistakes"] == "后补的失误反思"
        # 列表读回一致
        listed = client.get(f"/api/records?favorite_id={fid}").json()["records"]
        assert listed[0]["experiment_no"] == "B7"
        assert listed[0]["outcome"] == "failed"

    def test_put_final_partial_update_keeps_others(self, isolated_data):
        fid = _create_favorite()
        rec = _create_final(fid)
        rid = rec["record_id"]
        # 仅后补 self_summary / mistakes，其余字段不变
        r = client.put(f"/api/records/{rid}", json={
            "self_summary": "只补总结", "mistakes": "只补失误"})
        assert r.status_code == 200
        body = r.json()
        assert body["self_summary"] == "只补总结"
        assert body["mistakes"] == "只补失误"
        assert body["experiment_no"] == "A1"
        assert body["outcome"] == "film"
        assert body["strength"] == "中"
        assert body["operator"] == "张三"
        assert body["process_notes"] == "投料→陈化"
        assert len(body["timeline"]) == 1

    def test_put_final_empty_experiment_no_400(self, isolated_data):
        fid = _create_favorite()
        rec = _create_final(fid)
        rid = rec["record_id"]
        r = client.put(f"/api/records/{rid}", json={"experiment_no": "   "})
        assert r.status_code == 400
        # 校验失败不落盘：原编号仍在
        got = client.get(f"/api/records/{rid}").json()
        assert got["experiment_no"] == "A1"

    def test_put_final_invalid_outcome_400(self, isolated_data):
        fid = _create_favorite()
        rec = _create_final(fid)
        rid = rec["record_id"]
        r = client.put(f"/api/records/{rid}", json={"outcome": "maybe"})
        assert r.status_code == 400
        got = client.get(f"/api/records/{rid}").json()
        assert got["outcome"] == "film"

    def test_put_final_disk_persisted(self, isolated_data):
        """整体修改真实落盘（直接读 json 文件核对）。"""
        import sys
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[1]
        if str(project_root / "src") not in sys.path:
            sys.path.insert(0, str(project_root / "src"))
        from records import store as rec_store

        fid = _create_favorite()
        rec = _create_final(fid)
        rid = rec["record_id"]
        r = client.put(f"/api/records/{rid}", json={
            "self_summary": "落盘总结", "mistakes": "落盘失误",
            "experiment_no": "C3"})
        assert r.status_code == 200
        saved = json.loads(
            (rec_store.RECORDS_DIR / f"{rid}.json").read_text(encoding="utf-8"))
        assert saved["experiment_no"] == "C3"
        assert saved["self_summary"] == "落盘总结"
        assert saved["mistakes"] == "落盘失误"
        assert saved["notes"].startswith("实验编号：C3")
