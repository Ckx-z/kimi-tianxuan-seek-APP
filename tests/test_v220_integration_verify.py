"""v2.20 四功能集成回归（独立验收的 pytest 版）。

与 `scripts/verify_v220_integration.py` 同源：走**真实 FastAPI 路由**
（`api.main:app` + `TestClient`）并在临时数据目录下同时校验 HTTP 响应与落盘
文件。本文件把该脚本的核心断言搬进 pytest，供以后回归自动跑到：
    实验进展 progress / 收藏档案卡与单体元数据 / DFT 计算记录规范 / 科研日记与助手工具。

隔离纪律（与红线条 `tests/test_test_isolation_redline.py` 一致）：
- 模块级 fixture 先把 `COF_DATA_DIR` 指到 `tmp_path_factory` 的临时目录，再导入
  `api.main`，并把 records/favorites 两个包名的**全部导入期路径常量**钉到该
  目录（重复导入会产出 `records.store` 与 `src.records.store` 两个模块实例，
  必须都钉，否则 API 与测试读写的不是同一个目录）；
- fixture 结束时对仓库 `data/` 下相关目录做文件快照比对，一旦被写入即报错
  （teardown 阶段 error，不会被忽略）。

独立验收发现的缺陷（**已修复**，末尾两个用例改为回归守护）：
`archive_type` / `dft_labels` 这两个「响应派生」字段曾被写进 `fav_*.json`，且写盘时机
早于同一次写操作对条目的改动 → 落盘值是陈旧副本，与 dft_entries 自相矛盾
（API 响应每次重算故 GET 不受影响，但直接读 json 的消费者会被误导）。
修复：`src/favorites/store.py::_write` 用 `_RESPONSE_ONLY_KEYS` 在唯一写边界剥离派生键。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 复用验收脚本里的隔离/落盘工具（单一事实来源，避免两处断言漂移）
from scripts.verify_v220_integration import (  # noqa: E402
    PATH_CONSTS, REAL_WATCH_DIRS, path_inside, pin_store_paths, snapshot_dirs,
    read_json)

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"                  # 对苯二胺
PA2 = "Nc1ccc(-c2ccc(N)cc2)cc1"      # 联苯二胺（pair 模式档案卡用）
PA3 = "Nc1cccc(N)c1"                 # 间苯二胺（DFT 记录规范用例专用）
PA4 = "Nc1ccccc1N"                   # 邻苯二胺（缺陷复现用例专用）
# 注意：POST /api/favorites 会按规范化 SMILES 对做 409 去重，
# 因此每个 fixture 必须用**不同**的单体对，否则同一会话里第二次收藏被拒。


class _Env:
    """一次测试会话共享的隔离环境（客户端 + store 模块 + 落盘路径）。"""

    def __init__(self, tmp_root, client, rec_store, fav_store, diary_store):
        self.tmp_root = tmp_root
        self.client = client
        self.rec_store = rec_store
        self.fav_store = fav_store
        self.diary_store = diary_store


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp_root = tmp_path_factory.mktemp("v220_verify")
    mp = pytest.MonkeyPatch()
    mp.setenv("COF_DATA_DIR", str(tmp_root))
    mp.setenv("COF_CREST_DOCKER_AUTOSTART", "0")

    from src import runtime_config  # noqa: PLC0415

    data_root = runtime_config.user_data_root()
    assert path_inside(data_root, tmp_root), (
        f"COF_DATA_DIR 未生效：user_data_root()={data_root} 不在 {tmp_root} 内")

    # 导入期路径常量（模块可能在 collection 阶段就被别的测试导入过）全部钉到隔离目录；
    # 记下原值，模块结束时还原，避免影响同一次 pytest 会话里的其它测试文件。
    import importlib  # noqa: PLC0415

    originals: dict[str, dict] = {}
    for mod_name, attr, _rel in PATH_CONSTS:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:  # pragma: no cover
            continue
        originals.setdefault(mod_name, {})[attr] = getattr(mod, attr, None)
    _, repinned = pin_store_paths(data_root)

    rec_store = importlib.import_module("records.store")
    fav_store = importlib.import_module("favorites.store")
    diary_store = importlib.import_module("src.diary.store")
    for mod in (rec_store, fav_store):
        for attr in ("RECORDS_DIR", "ATTACHMENTS_DIR", "FAVORITES_DIR"):
            if hasattr(mod, attr):
                assert path_inside(getattr(mod, attr), tmp_root), (
                    f"{mod.__name__}.{attr}={getattr(mod, attr)} 不在临时目录内"
                    f"（repinned={repinned}）")
    assert path_inside(diary_store.diary_path(), tmp_root)

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from api.main import app  # noqa: PLC0415

    # 不进 with：不触发 lifespan（避免加载真实 dft_jobs.json / 预热嵌入模型）
    client = TestClient(app)

    real_before = snapshot_dirs(REAL_WATCH_DIRS)
    yield _Env(tmp_root, client, rec_store, fav_store, diary_store)

    real_after = snapshot_dirs(REAL_WATCH_DIRS)
    added = sorted(set(real_after) - set(real_before))
    changed = sorted(k for k in set(real_after) & set(real_before)
                     if real_after[k] != real_before[k])
    for mod_name, attrs in originals.items():
        mod = sys.modules.get(mod_name)
        if mod is None:  # pragma: no cover
            continue
        for attr, value in attrs.items():
            if value is not None:
                setattr(mod, attr, value)
    mp.undo()
    assert not added and not changed, (
        f"仓库真实 data/ 被本测试写入：新增={added} 改动={changed}")


def _new_record(env, *, experiment_no, progress="running", outcome="film",
                status="final", **extra):
    r = env.client.post("/api/records", json={
        "aldehyde_smiles": TP, "amine_smiles": PA,
        "experiment_no": experiment_no, "outcome": outcome,
        "progress": progress, "status": status, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _disk_record(env, rec_id):
    path = Path(env.rec_store.RECORDS_DIR) / f"{rec_id}.json"
    assert path.is_file(), f"记录未落盘: {path}"
    return read_json(path)


def _new_favorite(env, *, amine=PA, **kw):
    r = env.client.post("/api/favorites", json={
        "aldehyde_smiles": TP, "amine_smiles": amine, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------- 功能 1：实验进展


def test_records_progress_filter_edit_and_persist(env):
    """progress：创建 → 按值筛选 → 编辑后筛选随之变化 → 落盘可见。"""
    assert tuple(env.rec_store.PROGRESS_VALUES) == ("running", "finished", "xrd_done")
    assert env.rec_store.PROGRESS_LABELS == {
        "running": "实验进行中", "finished": "实验结束", "xrd_done": "XRD 已完成"}

    rec = _new_record(env, experiment_no="T-XRD", progress="xrd_done")
    rec_id = rec["record_id"]
    assert re.match(r"^rec_\d{8}_\d{3}$", rec_id)
    assert rec["progress"] == "xrd_done"
    assert rec["status"] == "final"          # 与既有 status 并存
    assert _disk_record(env, rec_id)["progress"] == "xrd_done"

    other = _new_record(env, experiment_no="T-RUN", progress="running",
                        outcome="partial")

    r = env.client.get("/api/records", params={"progress": "xrd_done"})
    assert r.status_code == 200
    ids = [x["record_id"] for x in r.json()["records"]]
    assert rec_id in ids and other["record_id"] not in ids
    # 不带 progress → 不筛选
    ids_all = [x["record_id"] for x in env.client.get("/api/records").json()["records"]]
    assert rec_id in ids_all and other["record_id"] in ids_all

    r = env.client.put(f"/api/records/{rec_id}", json={"progress": "finished"})
    assert r.status_code == 200 and r.json()["progress"] == "finished"
    assert _disk_record(env, rec_id)["progress"] == "finished"
    assert env.client.get(f"/api/records/{rec_id}").json()["progress"] == "finished"

    in_xrd = [x["record_id"] for x in env.client.get(
        "/api/records", params={"progress": "xrd_done"}).json()["records"]]
    in_fin = [x["record_id"] for x in env.client.get(
        "/api/records", params={"progress": "finished"}).json()["records"]]
    assert rec_id not in in_xrd and rec_id in in_fin


def test_records_progress_invalid_value_400(env):
    """非法 progress：查询 400（不静默忽略）+ 创建/更新也 400 且不破坏落盘。"""
    rec = _new_record(env, experiment_no="T-BAD", progress="xrd_done")
    rec_id = rec["record_id"]

    r = env.client.get("/api/records", params={"progress": "bogus"})
    assert r.status_code == 400, r.text
    detail = str(r.json()["detail"])
    assert "progress" in detail and "xrd_done" in detail

    # 取值严格小写
    assert env.client.get("/api/records", params={"progress": "RUNNING"}).status_code == 400

    r = env.client.post("/api/records", json={
        "aldehyde_smiles": TP, "amine_smiles": PA,
        "experiment_no": "T-BAD2", "outcome": "film", "progress": "done"})
    assert r.status_code == 400, r.text

    r = env.client.put(f"/api/records/{rec_id}", json={"progress": "done"})
    assert r.status_code == 400, r.text
    assert _disk_record(env, rec_id)["progress"] == "xrd_done"   # 未被破坏


def test_records_progress_coexists_with_status_and_legacy(env):
    """草稿也能带 progress；旧记录（无该字段）读出补 running 并可被筛到。"""
    draft = _new_record(env, experiment_no="", outcome="", status="draft",
                        progress="finished")
    assert draft["status"] == "draft" and draft["progress"] == "finished"
    disk = _disk_record(env, draft["record_id"])
    assert disk["status"] == "draft" and disk["progress"] == "finished"

    legacy_id = "rec_20250101_777"
    legacy = {
        "schema_version": "1.0", "record_type": "experiment_record",
        "record_id": legacy_id, "experiment_no": "LEGACY-1", "status": "final",
        "favorite_id": None, "aldehyde": {"smiles": TP, "cas": "", "name": ""},
        "amine": {"smiles": PA, "cas": "", "name": ""},
        "conditions": {}, "outcome": "film", "notes": "老数据", "date": "2025-01-01",
    }
    path = Path(env.rec_store.RECORDS_DIR) / f"{legacy_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    assert env.client.get(f"/api/records/{legacy_id}").json()["progress"] == "running"
    ids = [x["record_id"] for x in env.client.get(
        "/api/records", params={"progress": "running"}).json()["records"]]
    assert legacy_id in ids


# ------------------------------------------------- 功能 2/3：档案卡 + DFT 计算记录


@pytest.fixture(scope="module")
def fav_plain(env):
    """无 DFT 记录的收藏（档案卡类型 / 单体元数据编辑用）。"""
    fav = _new_favorite(env, ald_name="验收醛", amine_name="验收胺")
    return {"id": fav["id"], "raw": fav,
            "path": Path(env.fav_store.FAVORITES_DIR) / f"{fav['id']}.json"}


@pytest.fixture(scope="module")
def fav_dft(env):
    """专门用于 DFT 记录规范的收藏（独立单体对，避免用例相互依赖与 409 去重）。"""
    fav = _new_favorite(env, amine=PA3, ald_name="DFT 醛", amine_name="DFT 胺")
    return {"id": fav["id"],
            "path": Path(env.fav_store.FAVORITES_DIR) / f"{fav['id']}.json"}


def test_archive_type_derived_from_entries(env, fav_plain):
    """archive_type：无记录 → cof_monomer_group；全部 pair → dft_pair。"""
    assert fav_plain["path"].is_file()
    assert fav_plain["raw"]["archive_type"] == "cof_monomer_group"
    listed = [f["id"] for f in env.client.get("/api/favorites").json()["favorites"]]
    assert fav_plain["id"] in listed

    pair_fav = _new_favorite(env, amine=PA2, ald_name="配对醛", amine_name="配对胺")
    r = env.client.post(f"/api/favorites/{pair_fav['id']}/dft-entries", json={
        "job_id": "t_pair_1", "mode": "pair", "method": "GFN2-xTB", "backend": "xtb",
        "x_type": "custom", "custom_smiles": "c1ccccc1", "a_smiles": TP,
        "b_smiles": PA2, "e_bind_kcal": -8.8})
    assert r.status_code == 200, r.text
    assert r.json()["archive_type"] == "dft_pair"


def test_monomer_meta_patch_name_and_cas(env, fav_plain):
    """PATCH monomer-meta：可改名称、可手动补 CAS，且不动 SMILES。"""
    fid = fav_plain["id"]
    before = env.client.get(f"/api/favorites/{fid}").json()
    r = env.client.patch(f"/api/favorites/{fid}/monomer-meta", json={
        "aldehyde": {"name": "验收醛-改名", "cas": "123-45-6"},
        "amine": {"cas": "77-78-1"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["aldehyde"]["name"] == "验收醛-改名"
    assert body["aldehyde"]["cas"] == "123-45-6"
    assert body["amine"]["cas"] == "77-78-1"
    assert body["amine"]["name"] == before["amine"]["name"]      # 未传=不改
    assert body["aldehyde"]["smiles"] == before["aldehyde"]["smiles"]
    assert body["amine"]["smiles"] == before["amine"]["smiles"]

    got = env.client.get(f"/api/favorites/{fid}").json()
    assert got["aldehyde"]["name"] == "验收醛-改名"
    assert got["amine"]["cas"] == "77-78-1"
    disk = read_json(fav_plain["path"])
    assert disk["aldehyde"]["cas"] == "123-45-6"
    assert disk["amine"]["cas"] == "77-78-1"

    # 全角连字符归一
    r = env.client.patch(f"/api/favorites/{fid}/monomer-meta",
                         json={"amine": {"cas": "1234－56－7"}})
    assert r.status_code == 200 and r.json()["amine"]["cas"] == "1234-56-7"

    # 空 patch / 不存在的收藏
    assert env.client.patch(f"/api/favorites/{fid}/monomer-meta",
                            json={"aldehyde": {}}).status_code == 400
    assert env.client.patch("/api/favorites/fav_19700101_001/monomer-meta",
                            json={"aldehyde": {"name": "x"}}).status_code == 404


def test_dft_entries_dedup_labels_and_delete(env, fav_dft):
    """DFT 记录规范：同条件指纹更新 / 不同条件新增并按 A-B-C 标号 / 失败记录可删且持久化。

    一个用例里走完整条链路（含删除），保证单独运行本用例（`pytest -k dft`）也成立。
    """
    fid = fav_dft["id"]
    base = {"job_id": "t_dft_1", "mode": "dimer", "method": "GFN2-xTB",
            "backend": "xtb", "x_type": "self_stack", "x_smiles": "c1ccccc1",
            "n_samples": 1, "with_props": False}

    # --- 同条件指纹：重复提交只保留 1 条，结果被覆盖、标号不变
    r = env.client.post(f"/api/favorites/{fid}/dft-entries",
                        json={**base, "e_bind_kcal": -12.3})
    assert r.status_code == 200, r.text
    ents = r.json()["dft_entries"]
    assert len(ents) == 1
    assert ents[0]["label"] == "A" and ents[0]["mode"] == "dimer"
    assert ents[0]["condition_key"]
    key_a = ents[0]["condition_key"]

    r = env.client.post(f"/api/favorites/{fid}/dft-entries",
                        json={**base, "e_bind_kcal": -13.9})
    ents = r.json()["dft_entries"]
    assert len(ents) == 1 and ents[0]["label"] == "A"
    assert ents[0]["e_bind_kcal"] == -13.9        # 同条件 → 更新本次结果

    # 同条件但换 job_id（重跑）→ 仍 1 条（job_id 不参与条件指纹）
    r = env.client.post(f"/api/favorites/{fid}/dft-entries",
                        json={**base, "job_id": "t_dft_1rerun", "e_bind_kcal": -14.2})
    ents = r.json()["dft_entries"]
    assert len(ents) == 1 and ents[0]["label"] == "A"
    assert ents[0]["e_bind_kcal"] == -14.2

    # --- 不同条件（换 method）→ 新增，标号 A、B
    r = env.client.post(f"/api/favorites/{fid}/dft-entries",
                        json={**base, "method": "B3LYP-D3", "e_bind_kcal": -10.5})
    ents = r.json()["dft_entries"]
    assert [e["label"] for e in ents] == ["A", "B"]
    assert ents[1]["condition_key"] and ents[1]["condition_key"] != key_a
    assert ents[0]["e_bind_kcal"] == -14.2        # 新增不影响已有记录

    # --- 失败记录（带 error）→ C
    r = env.client.post(f"/api/favorites/{fid}/dft-entries", json={
        **base, "job_id": "t_dft_failed", "method": "PBE0", "error": "SCF 未收敛",
        "status": "failed", "e_bind_kcal": None})
    ents = r.json()["dft_entries"]
    assert [e["label"] for e in ents] == ["A", "B", "C"]
    assert ents[2]["error"] == "SCF 未收敛"

    # --- 只给 job_id 的条目也能写入 → D
    r = env.client.post(f"/api/favorites/{fid}/dft-entries",
                        json={"job_id": "t_dft_only_job"})
    assert r.status_code == 200, r.text
    ents = r.json()["dft_entries"]
    assert len(ents) == 4 and ents[3]["label"] == "D" and ents[3]["mode"]

    # --- 删除「只给 job_id」那条（index 3）：已有标号不重排
    r = env.client.delete(f"/api/favorites/{fid}/dft-entries/3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["label"] for e in body["dft_entries"]] == ["A", "B", "C"]
    assert body["deleted_entry"]["job_id"] == "t_dft_only_job"

    # --- 删除失败记录（index 2）：可删 + 落盘持久化
    r = env.client.delete(f"/api/favorites/{fid}/dft-entries/2")
    assert r.status_code == 200, r.text
    assert [e["label"] for e in r.json()["dft_entries"]] == ["A", "B"]

    got = env.client.get(f"/api/favorites/{fid}").json()
    assert len(got["dft_entries"]) == 2
    assert not any(e.get("error") for e in got["dft_entries"])
    assert got["dft_labels"] == ["A", "B"]

    disk = read_json(fav_dft["path"])
    assert [e["label"] for e in disk["dft_entries"]] == ["A", "B"]
    assert all(e["condition_key"] for e in disk["dft_entries"])
    assert disk["dft_snapshot"] is None

    # --- 越界 / 不存在的收藏
    assert env.client.delete(f"/api/favorites/{fid}/dft-entries/9").status_code == 404
    assert env.client.delete(
        "/api/favorites/fav_19700101_001/dft-entries/0").status_code == 404

    # --- 删掉 A 后再加不同条件 → 取 C（空档不复用，已有标号不重排）
    assert env.client.delete(f"/api/favorites/{fid}/dft-entries/0").status_code == 200
    r = env.client.post(f"/api/favorites/{fid}/dft-entries", json={
        "job_id": "t_dft_4", "mode": "dimer", "method": "wB97X-D", "backend": "xtb",
        "x_type": "self_stack", "x_smiles": "c1ccccc1", "e_bind_kcal": -9.4})
    assert [e["label"] for e in r.json()["dft_entries"]] == ["B", "C"]


# --------------------------------------------------------------- 功能 4：科研日记


def test_diary_crud_mentions_and_by_record(env):
    from datetime import date
    today = date.today().isoformat()
    token = "ZQ7PYTEST"
    rec_id = _new_record(env, experiment_no="T-DIARY", progress="xrd_done")["record_id"]

    r = env.client.post("/api/diary", json={
        "date": today, "title": "回归日记：XRD 复盘",
        "content": f"# 回归\n今天 {token} 记录了 XRD 结果，关联 @{rec_id}。",
        "mentions": [rec_id]})
    assert r.status_code == 201, r.text
    entry = r.json()
    entry_id = entry["entry_id"]
    assert re.match(r"^dy_[0-9a-f]{12}$", entry_id)
    assert entry["mentions"] == [rec_id]
    assert entry["mention_details"][0]["record_id"] == rec_id
    assert entry["mention_details"][0]["exists"] is True

    listed = env.client.get("/api/diary").json()
    assert entry_id in [e["entry_id"] for e in listed["entries"]]
    assert listed["count"] >= 1
    assert entry_id in [e["entry_id"] for e in env.client.get(
        "/api/diary", params={"keyword": token}).json()["entries"]]
    assert entry_id in [e["entry_id"] for e in env.client.get(
        "/api/diary", params={"record_id": rec_id}).json()["entries"]]

    by = env.client.get(f"/api/diary/by-record/{rec_id}").json()
    assert by["record_id"] == rec_id and by["count"] >= 1
    assert entry_id in [e["entry_id"] for e in by["entries"]]
    assert env.client.get("/api/diary/by-record/rec_20200101_999").json()["count"] == 0
    assert env.client.get("/api/diary/by-record/not-an-id").status_code == 400

    # 假引用 / 空正文 / mentions 去重
    assert env.client.post("/api/diary", json={
        "date": today, "title": "假引用", "content": "正文够长",
        "mentions": ["rec_20200101_999"]}).status_code == 400
    assert env.client.post("/api/diary", json={
        "date": today, "content": "   "}).status_code == 400
    dedup = env.client.post("/api/diary", json={
        "date": today, "title": "去重", "content": f"正文 {token}",
        "mentions": [rec_id, rec_id]}).json()
    assert dedup["mentions"] == [rec_id]
    env.client.delete(f"/api/diary/{dedup['entry_id']}")

    # 落盘 JSONL
    rows = [json.loads(x) for x in
            Path(env.diary_store.diary_path()).read_text(encoding="utf-8").splitlines()
            if x.strip()]
    hit = [x for x in rows if x["entry_id"] == entry_id]
    assert len(hit) == 1 and hit[0]["mentions"] == [rec_id]

    # 更新 + 删除
    r = env.client.put(f"/api/diary/{entry_id}", json={"title": "回归日记：已更新"})
    assert r.status_code == 200 and r.json()["title"] == "回归日记：已更新"
    assert r.json()["mentions"] == [rec_id]
    assert env.client.get(f"/api/diary/{entry_id}").json()["title"] == "回归日记：已更新"

    tmp_id = env.client.post("/api/diary", json={
        "date": today, "title": "待删除", "content": "临时"}).json()["entry_id"]
    assert env.client.delete(f"/api/diary/{tmp_id}").status_code == 200
    assert env.client.get(f"/api/diary/{tmp_id}").status_code == 404
    raw = Path(env.diary_store.diary_path()).read_text(encoding="utf-8")
    assert tmp_id not in raw
    assert entry_id in [e["entry_id"] for e in env.client.get(
        f"/api/diary/by-record/{rec_id}").json()["entries"]]


def test_diary_assistant_tool_read_diary(env):
    """助手工具 read_diary：关键词/日期/关联记录可检索；无结果返回结构化空结果。"""
    from datetime import date
    import importlib
    today = date.today().isoformat()
    token = "ZQ7TOOL"
    rec_id = _new_record(env, experiment_no="T-TOOL", progress="finished")["record_id"]
    entry_id = env.client.post("/api/diary", json={
        "date": today, "title": "工具检索用日记",
        "content": f"正文含 {token} 标记", "mentions": [rec_id]}).json()["entry_id"]

    registry = None
    for name in ("assistant.registry", "src.assistant.registry"):
        try:
            registry = importlib.import_module(name)
            break
        except ImportError:  # pragma: no cover
            continue
    assert registry is not None and "read_diary" in registry.TOOLS

    res = registry.execute("read_diary", {"keyword": token})
    assert res["is_error"] is False and res["details"]["found"] is True
    assert entry_id in res["details"]["entry_ids"]
    assert token in res["text"]

    res = registry.execute("read_diary", {"record_id": rec_id})
    assert res["is_error"] is False and entry_id in res["details"]["entry_ids"]

    res = registry.execute("read_diary", {"date_from": today, "date_to": today})
    assert res["is_error"] is False and res["details"]["found"] is True

    # 关键词能命中 @ 关联记录的实验编号
    res = registry.execute("read_diary", {"keyword": "T-TOOL"})
    assert res["is_error"] is False and entry_id in res["details"]["entry_ids"]

    # 无结果：结构化空结果，且不夹带任何真实日记内容（不编造）
    res = registry.execute("read_diary", {"keyword": "绝无此词QXZ9871"})
    assert res["is_error"] is False
    assert res["details"]["found"] is False
    assert res["details"]["count"] == 0
    assert not res["details"]["entry_ids"]
    assert "未查到" in res["text"] and "编造" in res["text"]
    assert token not in res["text"]

    # 合法但无关联 / 非法参数
    res = registry.execute("read_diary", {"record_id": "rec_20200101_999"})
    assert res["is_error"] is False and res["details"]["found"] is False
    assert registry.execute("read_diary", {"record_id": "bad-id"})["is_error"] is True


# ------------------------------------------------------------------ 已知缺陷记录


@pytest.fixture(scope="module")
def fav_leak(env):
    """专用于「派生字段落盘」缺陷记录的收藏（独立单体对，与其它用例完全解耦）。"""
    fav = _new_favorite(env, amine=PA4, ald_name="缺陷复现醛", amine_name="缺陷复现胺")
    fid = fav["id"]
    base = {"mode": "dimer", "method": "GFN2-xTB", "backend": "xtb",
            "x_type": "self_stack", "x_smiles": "c1ccccc1"}
    for i, method in enumerate(("GFN2-xTB", "B3LYP-D3", "PBE0")):
        r = env.client.post(f"/api/favorites/{fid}/dft-entries",
                            json={**base, "job_id": f"t_leak_{i}", "method": method})
        assert r.status_code == 200, r.text
    return {"id": fid,
            "path": Path(env.fav_store.FAVORITES_DIR) / f"{fid}.json"}


def test_response_derived_fields_are_not_persisted(env, fav_leak):
    """响应派生字段（archive_type / dft_labels）**不得落盘**。

    回归来源：v2.20 独立验收发现 `_dft_response_view()` 就地注入这两个键，
    而写路径复用同一 dict → 陈旧派生值被写进 fav_*.json，与 dft_entries 自相矛盾。
    已在唯一写边界（`src/favorites/store.py::_write` 的 `_RESPONSE_ONLY_KEYS`）修复。
    """
    disk = read_json(fav_leak["path"])
    assert "archive_type" not in disk and "dft_labels" not in disk, (
        f"派生字段落盘了：archive_type={disk.get('archive_type')!r} "
        f"dft_labels={disk.get('dft_labels')!r}（磁盘键 {sorted(disk)}）")


def test_disk_stays_consistent_after_delete(env, fav_leak):
    """删除 DFT 记录后：磁盘只剩真实条目，且派生值由读路径现算（不再有陈旧副本）。"""
    fid = fav_leak["id"]
    r = env.client.delete(f"/api/favorites/{fid}/dft-entries/2")
    assert r.status_code == 200, r.text
    resp = env.client.get(f"/api/favorites/{fid}").json()
    disk = read_json(fav_leak["path"])
    assert len(disk["dft_entries"]) == 2
    # 磁盘不该有派生副本可比；响应仍是现算的新鲜值
    assert "dft_labels" not in disk and "archive_type" not in disk
    assert resp["dft_labels"] == [e.get("label") or resp["dft_labels"][i]
                                  for i, e in enumerate(resp["dft_entries"])]
    assert len(resp["dft_labels"]) == len(resp["dft_entries"]) == 2

