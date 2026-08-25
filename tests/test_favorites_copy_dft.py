"""收藏复制（POST /{fav_id}/copy）与 DFT 条目列表化（dft_entries）测试。

覆盖：复制成功/目标夹不存在/原收藏不存在；dft-entries 追加（补 created_at、
累积分条）；旧 dft_snapshot 幂等迁移与 GET 响应回填；PATCH dft_snapshot
旧前端路径兼容。数据目录一律 monkeypatch 到 tmp_path，不碰真实数据。
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

from api.main import app  # noqa: E402

client = TestClient(app)

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"


@pytest.fixture()
def fav_dir(tmp_path, monkeypatch):
    d = tmp_path / "favorites"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", d)
    return d


def _create_favorite(**kw) -> dict:
    body = {"aldehyde_smiles": TP, "amine_smiles": PA}
    body.update(kw)
    r = client.post("/api/favorites", json=body)
    assert r.status_code == 201
    return r.json()


# ---------------------------------------------------------------- 复制

class TestCopyFavorite:
    def test_copy_success_full_content(self, fav_dir):
        src = _create_favorite(notes="重点组合", score=0.71)  # 先建收藏→兜底夹
        fid = src["id"]
        src_folder = src["folder_id"]
        target = fav_store.create_folder("目标夹")
        # 给原收藏补一条 DFT 条目，验证 dft 相关字段随复制
        client.post(f"/api/favorites/{fid}/dft-entries",
                    json={"job_id": "j1", "x_type": "水", "e_bind_kcal": -3.2})

        r = client.post(f"/api/favorites/{fid}/copy",
                        json={"folder_id": target["id"]})
        assert r.status_code == 201
        new = r.json()
        assert new["id"] != fid
        assert new["id"].startswith("fav_")
        assert new["folder_id"] == target["id"]
        # 单体信息 / 打分快照 / notes / dft 条目全量复制
        assert new["aldehyde"]["smiles"] == src["aldehyde"]["smiles"]
        assert new["amine"]["smiles"] == src["amine"]["smiles"]
        assert new["notes"] == "重点组合"
        assert new["latest_prediction"]["score"] == pytest.approx(0.71)
        assert len(new["dft_entries"]) == 1
        assert new["dft_entries"][0]["job_id"] == "j1"
        assert new["dft_snapshot"]["job_id"] == "j1"  # 回填最新一条
        # created_at 取当前（不早于原收藏）
        assert new["created_at"] >= src["created_at"]

        # 原收藏不动，两个夹各一条
        orig = client.get(f"/api/favorites/{fid}").json()
        assert orig["folder_id"] == src_folder
        counts = {f["id"]: f["favorite_count"]
                  for f in fav_store.list_folders()}
        assert counts[target["id"]] == 1
        assert counts[src_folder] == 1
        # 复制的条目与原条目无别名（改新条目不污染原收藏）
        new2 = fav_store.update_favorite(new["id"], notes="副本备注")
        assert fav_store.get_favorite(fid)["notes"] == "重点组合"
        assert new2["notes"] == "副本备注"

    def test_copy_missing_folder_400(self, fav_dir):
        fid = _create_favorite()["id"]
        r = client.post(f"/api/favorites/{fid}/copy",
                        json={"folder_id": "folder_nope"})
        assert r.status_code == 400
        assert "收藏夹不存在" in r.json()["detail"]

    def test_copy_missing_favorite_404(self, fav_dir):
        target = fav_store.create_folder("目标夹")
        r = client.post("/api/favorites/fav_20990101_999/copy",
                        json={"folder_id": target["id"]})
        assert r.status_code == 404

    def test_copy_empty_folder_id_400(self, fav_dir):
        fid = _create_favorite()["id"]
        r = client.post(f"/api/favorites/{fid}/copy", json={"folder_id": "  "})
        assert r.status_code == 400


# ---------------------------------------------------------------- DFT 条目追加

class TestDftEntries:
    def test_append_fills_created_at_and_accumulates(self, fav_dir):
        fid = _create_favorite()["id"]
        r = client.post(f"/api/favorites/{fid}/dft-entries", json={
            "job_id": "j1", "x_type": "甲醇", "x_smiles": "CO",
            "e_bind_kcal": -3.2, "e_bind_kj": -13.4, "method": "xtb"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["dft_entries"]) == 1
        entry = body["dft_entries"][0]
        assert entry["job_id"] == "j1"
        assert entry["created_at"]  # 缺 created_at → 后端补当前时间

        r = client.post(f"/api/favorites/{fid}/dft-entries", json={
            "job_id": "j2", "x_type": "水", "e_bind_kcal": -5.1,
            "created_at": "2026-01-02T10:00:00+08:00"})
        assert r.status_code == 200
        entries = r.json()["dft_entries"]
        assert [e["job_id"] for e in entries] == ["j1", "j2"]
        # 显式 created_at 不被覆盖
        assert entries[1]["created_at"] == "2026-01-02T10:00:00+08:00"
        # GET 响应 dft_snapshot 回填为最新一条（旧前端兼容）
        got = client.get(f"/api/favorites/{fid}").json()
        assert got["dft_snapshot"]["job_id"] == "j2"
        assert got["dft_snapshot"] == got["dft_entries"][-1]
        # 列表接口同样回填
        listed = client.get("/api/favorites").json()["favorites"]
        assert listed[0]["dft_snapshot"]["job_id"] == "j2"
        # 磁盘上 dft_snapshot 保持 None（单一数据源是 dft_entries）
        on_disk = json.loads(
            (fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        assert on_disk["dft_snapshot"] is None
        assert len(on_disk["dft_entries"]) == 2

    def test_append_missing_favorite_404(self, fav_dir):
        r = client.post("/api/favorites/fav_20990101_999/dft-entries",
                        json={"job_id": "j1"})
        assert r.status_code == 404

    def test_append_empty_entry_400(self, fav_dir):
        fid = _create_favorite()["id"]
        r = client.post(f"/api/favorites/{fid}/dft-entries", json={})
        assert r.status_code == 400


# ---------------------------------------------------------------- 迁移

class TestDftEntriesMigration:
    def _write_legacy_fav(self, fav_dir: Path, snapshot) -> str:
        """写一条带旧单条 dft_snapshot、无 dft_entries 的旧格式收藏。"""
        fav_id = "fav_20200101_001"
        fav_dir.mkdir(parents=True, exist_ok=True)
        legacy = {
            "id": fav_id,
            "aldehyde": {"smiles": TP, "cas": "", "name": "Tp"},
            "amine": {"smiles": PA, "cas": "", "name": "Pa"},
            "created_at": "2020-01-01T00:00:00+08:00",
            "notes": "",
            "latest_prediction": None,
            "dft_snapshot": snapshot,
            "references": [],
            "experiment_record_ids": [],
        }
        (fav_dir / f"{fav_id}.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        return fav_id

    def test_legacy_snapshot_wrapped_into_entries(self, fav_dir):
        snapshot = {"job_id": "old1", "e_bind_kcal": -2.0}
        fid = self._write_legacy_fav(fav_dir, snapshot)
        fav = client.get(f"/api/favorites/{fid}").json()
        assert fav["dft_entries"] == [snapshot]
        # 旧前端读 dft_snapshot 仍拿到这条快照
        assert fav["dft_snapshot"] == snapshot
        # 落盘：snapshot 置 None，entries 持久化
        on_disk = json.loads(
            (fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        assert on_disk["dft_snapshot"] is None
        assert on_disk["dft_entries"] == [snapshot]

    def test_migration_idempotent(self, fav_dir):
        snapshot = {"job_id": "old1"}
        fid = self._write_legacy_fav(fav_dir, snapshot)
        fav_store.get_favorite(fid)
        first = json.loads(
            (fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        fav_store.get_favorite(fid)
        fav_store.list_favorites()
        second = json.loads(
            (fav_dir / f"{fid}.json").read_text(encoding="utf-8"))
        assert first == second  # 重复读取不重复包快照
        assert second["dft_entries"] == [snapshot]

    def test_empty_snapshot_migrates_to_empty_entries(self, fav_dir):
        fid = self._write_legacy_fav(fav_dir, None)
        fav = client.get(f"/api/favorites/{fid}").json()
        assert fav["dft_entries"] == []
        assert fav["dft_snapshot"] is None

    def test_existing_entries_not_rewrapped(self, fav_dir):
        """已有 dft_entries 且磁盘 dft_snapshot 非空（异常中间态）时不重复包。"""
        fav_id = "fav_20200101_002"
        fav_dir.mkdir(parents=True, exist_ok=True)
        entry = {"job_id": "j1"}
        (fav_dir / f"{fav_id}.json").write_text(json.dumps({
            "id": fav_id,
            "aldehyde": {"smiles": TP, "cas": "", "name": ""},
            "amine": {"smiles": PA, "cas": "", "name": ""},
            "created_at": "2020-01-01T00:00:00+08:00",
            "notes": "", "latest_prediction": None,
            "dft_snapshot": {"job_id": "stale"},
            "dft_entries": [entry],
            "references": [], "experiment_record_ids": [],
        }, ensure_ascii=False), encoding="utf-8")
        fav = fav_store.get_favorite(fav_id)
        assert fav["dft_entries"] == [entry]
        assert fav["dft_snapshot"] == entry  # 回填最新一条而非陈旧值

    def test_patch_dft_snapshot_also_wrapped_when_entries_empty(self, fav_dir):
        """旧前端 PATCH dft_snapshot 路径：dft_entries 为空时同步包入。"""
        fid = _create_favorite()["id"]
        r = client.patch(f"/api/favorites/{fid}",
                         json={"dft_snapshot": {"binding_energy": -1.2}})
        assert r.status_code == 200
        body = r.json()
        assert body["dft_snapshot"] == {"binding_energy": -1.2}
        assert body["dft_entries"] == [{"binding_energy": -1.2}]

    def test_patch_dft_snapshot_not_wrapped_when_entries_exist(self, fav_dir):
        fid = _create_favorite()["id"]
        client.post(f"/api/favorites/{fid}/dft-entries", json={"job_id": "j1"})
        r = client.patch(f"/api/favorites/{fid}",
                         json={"dft_snapshot": {"binding_energy": -9.9}})
        assert r.status_code == 200
        # dft_entries 已有内容 → 不再包入，响应 dft_snapshot 仍为最新条目
        assert len(r.json()["dft_entries"]) == 1
        assert r.json()["dft_snapshot"]["job_id"] == "j1"
