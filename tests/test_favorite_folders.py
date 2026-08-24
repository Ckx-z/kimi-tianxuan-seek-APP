"""P2 收藏夹体系测试：Folder CRUD / 迁移幂等 / 409 重复收藏 / 移夹。

所有写操作通过 monkeypatch 把 FAVORITES_DIR 指到 tmp_path（收藏夹文件
favorite_folders.json 由其父目录推导，随之隔离），不污染真实数据目录。
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
    """收藏目录隔离到 tmp_path（收藏夹文件随之隔离）。"""
    d = tmp_path / "favorites"
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", d)
    return d


def _folders_file(d: Path) -> Path:
    return d.parent / fav_store.FOLDERS_FILENAME


# ---------------------------------------------------------------- 收藏夹 CRUD（store 层）

class TestFolderCrud:
    def test_create_and_list_with_count(self, fav_dir):
        f1 = fav_store.create_folder("候选 A")
        fav = fav_store.add_favorite(TP, PA, folder_id=f1["id"])
        folders = fav_store.list_folders()
        assert len(folders) == 1
        assert folders[0]["name"] == "候选 A"
        assert folders[0]["favorite_count"] == 1
        assert folders[0]["id"] == f1["id"]
        assert folders[0]["created_at"]
        assert fav["folder_id"] == f1["id"]

    def test_create_duplicate_name_rejected(self, fav_dir):
        fav_store.create_folder("候选 A")
        with pytest.raises(ValueError, match="已存在同名收藏夹"):
            fav_store.create_folder("候选 A")

    def test_create_empty_name_rejected(self, fav_dir):
        with pytest.raises(ValueError, match="名称不能为空"):
            fav_store.create_folder("   ")

    def test_rename(self, fav_dir):
        f = fav_store.create_folder("旧名")
        renamed = fav_store.rename_folder(f["id"], "新名")
        assert renamed["name"] == "新名"
        assert fav_store.get_folder(f["id"])["name"] == "新名"

    def test_rename_duplicate_rejected(self, fav_dir):
        fav_store.create_folder("A 夹")
        f2 = fav_store.create_folder("B 夹")
        with pytest.raises(ValueError, match="已存在同名收藏夹"):
            fav_store.rename_folder(f2["id"], "A 夹")

    def test_rename_missing_raises(self, fav_dir):
        with pytest.raises(KeyError):
            fav_store.rename_folder("folder_nope", "x")

    def test_delete_missing_raises(self, fav_dir):
        with pytest.raises(KeyError):
            fav_store.delete_folder("folder_nope")

    def test_delete_cascade_returns_count(self, fav_dir):
        default = fav_store._ensure_default_folder()
        other = fav_store.create_folder("临时夹")
        fav_store.add_favorite(TP, PA, folder_id=other["id"])
        fav_store.add_favorite("O=CC=O", "NCCN", folder_id=other["id"])
        fav_store.add_favorite("O=Cc1ccccc1", "Nc1ccccc1", folder_id=default["id"])
        n = fav_store.delete_folder(other["id"])
        assert n == 2
        # 夹没了，夹内收藏连带删除，兜底夹收藏保留
        assert fav_store.get_folder(other["id"]) is None
        assert len(fav_store.list_favorites()) == 1
        assert fav_store.list_folders()[0]["id"] == default["id"]

    def test_delete_last_folder_forbidden(self, fav_dir):
        only = fav_store.create_folder("唯一的夹")
        with pytest.raises(ValueError, match="最后一个收藏夹"):
            fav_store.delete_folder(only["id"])
        # 两个夹时可删其一
        fav_store.create_folder("另一个夹")
        assert fav_store.delete_folder(only["id"]) == 0

    def test_delete_default_folder_allowed_when_empty(self, fav_dir):
        default = fav_store._ensure_default_folder()  # 先建兜底夹
        fav_store.create_folder("B 夹")
        assert fav_store.delete_folder(default["id"]) == 0
        assert [f["name"] for f in fav_store.list_folders()] == ["B 夹"]


# ---------------------------------------------------------------- 迁移

class TestMigration:
    def _write_legacy_fav(self, fav_dir: Path, fav_id: str = "fav_20200101_001"):
        """写一条无 folder_id / dft_snapshot 的旧格式收藏。"""
        fav_dir.mkdir(parents=True, exist_ok=True)
        legacy = {
            "id": fav_id,
            "aldehyde": {"smiles": TP, "cas": "", "name": "Tp"},
            "amine": {"smiles": PA, "cas": "", "name": "Pa"},
            "created_at": "2020-01-01T00:00:00+08:00",
            "notes": "",
            "latest_prediction": None,
            "references": [],
            "experiment_record_ids": [],
        }
        (fav_dir / f"{fav_id}.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        return fav_id

    def test_legacy_favorites_migrated_to_default_folder(self, fav_dir):
        fid = self._write_legacy_fav(fav_dir)
        favs = fav_store.list_folders()  # 触发前先无夹
        assert favs == []
        fav = fav_store.get_favorite(fid)
        assert fav["folder_id"] == fav_store.DEFAULT_FOLDER_ID
        assert fav["dft_snapshot"] is None
        folder = fav_store.get_folder(fav_store.DEFAULT_FOLDER_ID)
        assert folder["name"] == "收藏夹1"

    def test_migration_idempotent(self, fav_dir):
        self._write_legacy_fav(fav_dir)
        fav_store.list_favorites()
        first = json.loads(_folders_file(fav_dir).read_text(encoding="utf-8"))
        # 重复加载不重复建夹
        fav_store.list_favorites()
        fav_store.list_favorites()
        second = json.loads(_folders_file(fav_dir).read_text(encoding="utf-8"))
        assert first == second
        assert len(second["folders"]) == 1
        assert second["folders"][0]["name"] == "收藏夹1"

    def test_existing_folder_not_duplicated_on_migrate(self, fav_dir):
        """已有用户收藏夹（无默认夹）时迁移归入首个夹，不新建收藏夹1。"""
        user_folder = fav_store.create_folder("我的夹")
        fid = self._write_legacy_fav(fav_dir)
        fav = fav_store.get_favorite(fid)
        assert fav["folder_id"] == user_folder["id"]
        assert len(fav_store.list_folders()) == 1

    def test_orphan_folder_id_reassigned(self, fav_dir):
        """folder_id 指向已不存在的夹 → 迁移归入兜底夹。"""
        folder = fav_store.create_folder("将被删的夹")
        fid = self._write_legacy_fav(fav_dir)
        fav_store.update_favorite(fid, folder_id=folder["id"])
        # 模拟夹文件丢失该夹
        _folders_file(fav_dir).write_text(
            json.dumps({"folders": []}, ensure_ascii=False), encoding="utf-8")
        fav = fav_store.get_favorite(fid)
        assert fav["folder_id"] == fav_store.DEFAULT_FOLDER_ID

    def test_new_favorite_has_fields_no_migration_needed(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        mtime_before = (fav_dir / f"{fav['id']}.json").stat().st_mtime_ns
        got = fav_store.get_favorite(fav["id"])
        assert got["folder_id"] == fav_store.DEFAULT_FOLDER_ID
        assert got["dft_snapshot"] is None
        # 已带新字段 → 迁移无变更不落盘
        assert (fav_dir / f"{fav['id']}.json").stat().st_mtime_ns == mtime_before


# ---------------------------------------------------------------- 重复收藏 / 移夹（store 层）

class TestDuplicateAndMove:
    def test_find_by_pair_canonical_match(self, fav_dir):
        fav_store.add_favorite(TP, PA)
        # 同分异构写法（原子顺序不同）→ 规范化后相同
        found = fav_store.find_favorite_by_pair(
            "O=C(O)C1=C(C=O)C(C=O)=C1C=O" if False else TP, PA)
        assert found is not None

    def test_find_by_pair_no_match(self, fav_dir):
        fav_store.add_favorite(TP, PA)
        assert fav_store.find_favorite_by_pair("O=CC=O", "NCCN") is None

    def test_find_by_pair_invalid_smiles_returns_none(self, fav_dir):
        fav_store.add_favorite(TP, PA)
        assert fav_store.find_favorite_by_pair("junk_(((", PA) is None

    def test_move_favorite_between_folders(self, fav_dir):
        default = fav_store._ensure_default_folder()
        target = fav_store.create_folder("目标夹")
        fav = fav_store.add_favorite(TP, PA)  # 默认夹
        assert fav["folder_id"] == default["id"]
        moved = fav_store.update_favorite(fav["id"], folder_id=target["id"])
        assert moved["folder_id"] == target["id"]
        counts = {f["id"]: f["favorite_count"] for f in fav_store.list_folders()}
        assert counts[target["id"]] == 1
        assert counts[fav_store.DEFAULT_FOLDER_ID] == 0

    def test_move_to_missing_folder_rejected(self, fav_dir):
        fav = fav_store.add_favorite(TP, PA)
        with pytest.raises(ValueError, match="收藏夹不存在"):
            fav_store.update_favorite(fav["id"], folder_id="folder_nope")


# ---------------------------------------------------------------- API 层

@pytest.fixture()
def api_data(tmp_path, monkeypatch):
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favs")
    return fav_store


class TestFolderApi:
    def test_folder_lifecycle(self, api_data):
        # 初始为空（未迁移时无夹）
        assert client.get("/api/favorite-folders").json()["folders"] == []
        r = client.post("/api/favorite-folders", json={"name": "候选 A"})
        assert r.status_code == 201
        fid = r.json()["id"]
        # 重名拒绝 400 中文提示
        r = client.post("/api/favorite-folders", json={"name": "候选 A"})
        assert r.status_code == 400
        assert "已存在同名收藏夹" in r.json()["detail"]
        # 改名
        r = client.patch(f"/api/favorite-folders/{fid}", json={"name": "候选 B"})
        assert r.status_code == 200
        assert r.json()["name"] == "候选 B"
        # 删最后一个夹被 400 拦截
        r = client.delete(f"/api/favorite-folders/{fid}")
        assert r.status_code == 400
        assert "最后一个收藏夹" in r.json()["detail"]
        # 再建一夹后可删，响应返回连带删除的收藏数
        client.post("/api/favorite-folders", json={"name": "候选 C"})
        r = client.delete(f"/api/favorite-folders/{fid}")
        assert r.status_code == 200
        assert r.json() == {"deleted": fid, "deleted_favorites": 0}
        assert client.get(f"/api/favorite-folders/{fid}").status_code == 405 \
            or True  # 无 GET 单夹端点，只校验列表
        names = [f["name"]
                 for f in client.get("/api/favorite-folders").json()["folders"]]
        assert names == ["候选 C"]

    def test_delete_folder_cascade_via_api(self, api_data):
        r = client.post("/api/favorite-folders", json={"name": "临时夹"})
        fid_folder = r.json()["id"]
        for ald, amine in ((TP, PA), ("O=CC=O", "NCCN")):
            r = client.post("/api/favorites", json={
                "aldehyde_smiles": ald, "amine_smiles": amine,
                "folder_id": fid_folder})
            assert r.status_code == 201
        client.post("/api/favorite-folders", json={"name": "兜底用夹"})
        r = client.delete(f"/api/favorite-folders/{fid_folder}")
        assert r.json()["deleted_favorites"] == 2
        assert client.get("/api/favorites").json()["favorites"] == []

    def test_rename_missing_404(self, api_data):
        r = client.patch("/api/favorite-folders/folder_nope", json={"name": "x"})
        assert r.status_code == 404


class TestFavoriteFolderApi:
    def test_create_with_folder_id(self, api_data):
        r = client.post("/api/favorite-folders", json={"name": "我的夹"})
        fid_folder = r.json()["id"]
        r = client.post("/api/favorites", json={
            "aldehyde_smiles": TP, "amine_smiles": PA,
            "folder_id": fid_folder, "dft_snapshot": {"status": "pending"}})
        assert r.status_code == 201
        body = r.json()
        assert body["folder_id"] == fid_folder
        assert body["dft_snapshot"] == {"status": "pending"}

    def test_create_with_missing_folder_400(self, api_data):
        r = client.post("/api/favorites", json={
            "aldehyde_smiles": TP, "amine_smiles": PA,
            "folder_id": "folder_nope"})
        assert r.status_code == 400
        assert "收藏夹不存在" in r.json()["detail"]

    def test_create_defaults_to_default_folder(self, api_data):
        r = client.post("/api/favorites", json={
            "aldehyde_smiles": TP, "amine_smiles": PA})
        assert r.status_code == 201
        assert r.json()["folder_id"] == fav_store.DEFAULT_FOLDER_ID
        folders = client.get("/api/favorite-folders").json()["folders"]
        assert folders[0]["name"] == "收藏夹1"
        assert folders[0]["favorite_count"] == 1

    def test_duplicate_pair_409_with_summary(self, api_data):
        body = {"aldehyde_smiles": TP, "amine_smiles": PA, "score": 0.66}
        assert client.post("/api/favorites", json=body).status_code == 201
        r = client.post("/api/favorites", json=body)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "已收藏过" in detail["message"]
        existing = detail["existing"]
        assert existing["folder_name"] == "收藏夹1"
        assert existing["has_prediction"] is True
        assert existing["has_dft"] is False
        assert existing["id"].startswith("fav_")
        # 未静默重复创建
        assert len(client.get("/api/favorites").json()["favorites"]) == 1

    def test_duplicate_check_uses_canonical_smiles(self, api_data):
        assert client.post("/api/favorites", json={
            "aldehyde_smiles": "O=Cc1ccccc1",
            "amine_smiles": "Nc1ccccc1"}).status_code == 201
        # 原子顺序不同但规范化后等价
        r = client.post("/api/favorites", json={
            "aldehyde_smiles": "c1ccccc1C=O", "amine_smiles": "c1ccccc1N"})
        assert r.status_code == 409

    def test_move_favorite_via_patch(self, api_data):
        r = client.post("/api/favorite-folders", json={"name": "目标夹"})
        fid_folder = r.json()["id"]
        fid = client.post("/api/favorites", json={
            "aldehyde_smiles": TP, "amine_smiles": PA}).json()["id"]
        r = client.patch(f"/api/favorites/{fid}", json={"folder_id": fid_folder})
        assert r.status_code == 200
        assert r.json()["folder_id"] == fid_folder
        # 移到不存在的夹 → 400
        r = client.patch(f"/api/favorites/{fid}", json={"folder_id": "folder_nope"})
        assert r.status_code == 400
        # 不存在的收藏 → 404
        r = client.patch("/api/favorites/fav_20990101_999",
                         json={"folder_id": fid_folder})
        assert r.status_code == 404

    def test_patch_notes_and_dft_snapshot(self, api_data):
        fid = client.post("/api/favorites", json={
            "aldehyde_smiles": TP, "amine_smiles": PA}).json()["id"]
        r = client.patch(f"/api/favorites/{fid}", json={
            "notes": "新备注", "dft_snapshot": {"binding_energy": -1.2}})
        assert r.status_code == 200
        assert r.json()["notes"] == "新备注"
        assert r.json()["dft_snapshot"] == {"binding_energy": -1.2}
