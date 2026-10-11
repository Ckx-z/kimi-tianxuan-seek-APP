"""科研日记（v2.2.0）测试：存储 CRUD/持久化、字段校验、@ 实验记录关联、
助手工具 read_diary、REST API。

隔离（参考 test_annotation_image_v198.py 的写法）：本文件显式把
`runtime_config.user_data_root` 与实验记录 `RECORDS_DIR` 打桩到 tmp_path，
并在 fixture 里断言日记路径确实落在 tmp 目录 —— 绝不写仓库 data/ 或真实
用户数据目录；@ 关联用的是 tmp 里手写的**假实验记录**，不依赖真实用户数据。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.assistant import registry  # noqa: E402
from src.diary import store as diary_store  # noqa: E402
from src.records import store as rec_store  # noqa: E402

client = TestClient(app)

REC_A = "rec_20260902_006"          # tmp 里真实存在的假记录
REC_B = "rec_20260903_007"          # 同上（第二个体系）
REC_MISSING = "rec_20260101_999"    # 格式合法但不存在
CJK = re.compile(r"[\u4e00-\u9fff]")


def _fake_record(rec_id: str, *, experiment_no: str = "E-006",
                 outcome: str = "film", day: str = "2026-09-02") -> dict:
    """手写一条实验记录 JSON（与 src/records/store.py 落盘契约一致）。"""
    return {
        "schema_version": "1.0",
        "record_type": "experiment_record",
        "record_id": rec_id,
        "experiment_no": experiment_no,
        "status": "final",
        "favorite_id": None,
        "aldehyde": {"smiles": "O=Cc1ccccc1", "cas": "100-52-7", "name": "苯甲醛"},
        "amine": {"smiles": "Nc1ccccc1", "cas": "62-53-3", "name": "苯胺"},
        "prediction_snapshot": None,
        "conditions": {},
        "outcome": outcome,
        "strength": "",
        "notes": f"实验编号：{experiment_no}",
        "process_notes": "",
        "timeline": [{"entry_id": "tl_0001", "time_label": f"{day} 9:00",
                      "description": "投料", "attachments": []}],
        "self_summary": "",
        "mistakes": "",
        "operator": "张三",
        "date": day,
    }


def _write_record(recs: Path, rec_id: str, **kw) -> dict:
    rec = _fake_record(rec_id, **kw)
    (recs / f"{rec_id}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """数据根 + 实验记录目录全部隔离到 tmp；返回常用路径。"""
    data = tmp_path / "data"
    recs = tmp_path / "records"
    (data / "diary").mkdir(parents=True)
    recs.mkdir(parents=True)

    patched = []
    for name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            patched.append(name)
    assert patched, "runtime_config 未在 sys.modules 中，测试隔离失败"

    for name in ("src.records.store", "records.store"):
        mod = sys.modules.get(name)
        if mod is not None:
            monkeypatch.setattr(mod, "RECORDS_DIR", recs)
    monkeypatch.setattr(rec_store, "RECORDS_DIR", recs)
    monkeypatch.setenv("COF_DATA_DIR", str(data))

    diary_file = data / "diary" / "diary_entries.jsonl"
    assert diary_store.diary_path() == diary_file, "日记未隔离到 tmp 目录"
    return SimpleNamespace(data=data, records=recs, diary=diary_file, tmp=tmp_path)


# --------------------------------------------------------------- 存储 CRUD

def test_create_read_update_delete_and_persist(env):
    entry = diary_store.create_entry(
        date="2026-09-10", title="首次尝试",
        content="# 2026-09-10\n\n今天试了 A5 体系，成膜良好。")
    eid = entry["entry_id"]
    assert diary_store.ID_RE.match(eid), "entry_id 应为 dy_ + 12 位十六进制"
    assert entry["mentions"] == []
    assert entry["created_at"] and entry["updated_at"]

    # 持久化：JSONL 一行一条；原子写不留 .tmp 残file
    lines = _lines(env.diary)
    assert len(lines) == 1
    raw = json.loads(lines[0])
    assert raw["entry_id"] == eid
    assert raw["content"] == entry["content"], "Markdown 原文应原样落盘"
    assert raw["date"] == "2026-09-10" and raw["title"] == "首次尝试"
    assert not env.diary.with_name(env.diary.name + ".tmp").exists()

    # 重新读回一致（新的一次 _load，等价于重开应用）
    got = diary_store.get_entry(eid)
    assert got["title"] == "首次尝试" and got["content"] == entry["content"]
    assert [e["entry_id"] for e in diary_store.list_entries()] == [eid]

    # 多篇 + 日期倒序
    second = diary_store.create_entry(date="2026-09-12", title="后一天",
                                      content="第二天")
    assert [e["date"] for e in diary_store.list_entries()] == \
        ["2026-09-12", "2026-09-10"]
    assert [e["entry_id"] for e in diary_store.list_entries(limit=1)] == \
        [second["entry_id"]]

    # 更新：只改传进来的字段，created_at 保持不变
    updated = diary_store.update_entry(eid, {"title": "首次尝试（修订）",
                                             "content": "改过的正文"})
    assert updated["entry_id"] == eid and updated["created_at"] == entry["created_at"]
    assert updated["title"] == "首次尝试（修订）"
    assert updated["date"] == "2026-09-10" and updated["mentions"] == []
    assert diary_store.get_entry(eid)["content"] == "改过的正文"
    assert len(_lines(env.diary)) == 2, "更新不应新增行"

    # 删除
    deleted = diary_store.delete_entry(eid)
    assert deleted["entry_id"] == eid
    assert diary_store.get_entry(eid) is None
    assert diary_store.delete_entry(eid) is None
    assert [e["entry_id"] for e in diary_store.list_entries()] == [second["entry_id"]]


def test_empty_content_rejected(env):
    for bad in ("", "   ", "\n\t  \n"):
        with pytest.raises(ValueError) as ei:
            diary_store.create_entry(date="2026-09-10", content=bad)
        assert "不能为空" in str(ei.value)
    assert diary_store.list_entries() == []
    assert not env.diary.exists(), "校验失败不应写盘"


def test_overlong_content_and_title_rejected(env):
    limit = diary_store.MAX_CONTENT_CHARS
    with pytest.raises(ValueError) as ei:
        diary_store.create_entry(content="字" * (limit + 1))
    msg = str(ei.value)
    assert "超长" in msg and str(limit) in msg
    assert not env.diary.exists()

    with pytest.raises(ValueError) as ei:
        diary_store.create_entry(
            title="长" * (diary_store.MAX_TITLE_CHARS + 1), content="正文")
    assert "标题超长" in str(ei.value)

    # 边界：正好到上限可以写入
    ok = diary_store.create_entry(content="字" * limit)
    assert diary_store.get_entry(ok["entry_id"])["content"] == "字" * limit


def test_invalid_entry_id(env):
    for bad in ("", "abc", "dy_ZZZZZZZZZZZZ", "dy_123", REC_A):
        with pytest.raises(ValueError) as ei:
            diary_store.get_entry(bad)
        assert "格式非法" in str(ei.value)
    with pytest.raises(ValueError):
        diary_store.update_entry("nope", {"title": "x"})
    with pytest.raises(ValueError):
        diary_store.delete_entry("dy_")
    # 格式合法但不存在 → None（路由层据此 404）
    assert diary_store.get_entry("dy_000000000000") is None
    assert diary_store.update_entry("dy_000000000000", {"title": "x"}) is None
    assert diary_store.delete_entry("dy_000000000000") is None


def test_invalid_date_rejected(env):
    for bad in ("2026/09/10", "20260910", "2026-13-01", "2026-02-30", "今天"):
        with pytest.raises(ValueError) as ei:
            diary_store.create_entry(date=bad, content="正文")
        assert "日期" in str(ei.value)
    assert diary_store.list_entries() == []
    with pytest.raises(ValueError):
        diary_store.list_entries(date_from="2026-9-1")
    with pytest.raises(ValueError) as ei:
        diary_store.list_entries(date_from="2026-09-10", date_to="2026-09-01")
    assert "日期范围非法" in str(ei.value)


# ------------------------------------------------------- @ 实验记录关联

def test_mentions_roundtrip_and_reverse_lookup(env):
    _write_record(env.records, REC_A, experiment_no="E-006")
    _write_record(env.records, REC_B, experiment_no="E-007", outcome="failed")

    e1 = diary_store.create_entry(
        date="2026-09-10", title="A5 成膜", content="A5 体系今天成了",
        mentions=[REC_A, REC_A, "  "])          # 去重 + 空串忽略
    e2 = diary_store.create_entry(date="2026-09-11", title="B 体系失败",
                                  content="B 体系没成", mentions=[REC_B])
    assert e1["mentions"] == [REC_A]

    # 结构化落盘（不是正文里的一段文本）
    raw = json.loads(_lines(env.diary)[0])
    assert raw["mentions"] == [REC_A]
    assert diary_store.get_entry(e1["entry_id"])["mentions"] == [REC_A]

    # 按实验记录反查
    hit = diary_store.find_by_record(REC_A)
    assert [e["entry_id"] for e in hit] == [e1["entry_id"]]
    assert [e["entry_id"] for e in diary_store.list_entries(record_id=REC_B)] == \
        [e2["entry_id"]]
    assert diary_store.find_by_record(REC_MISSING) == []
    # 反查同时支持组合过滤
    assert diary_store.list_entries(record_id=REC_A, date_from="2026-09-11") == []

    # 关联摘要：实验编号 / 单体标签 / exists
    det = diary_store.mention_details(diary_store.get_entry(e1["entry_id"]))
    assert det[0]["record_id"] == REC_A and det[0]["exists"] is True
    assert det[0]["experiment_no"] == "E-006"
    assert det[0]["label"] == "苯甲醛 + 苯胺" and det[0]["outcome"] == "film"
    assert "mention_details" in diary_store.with_mentions(
        diary_store.get_entry(e1["entry_id"]))


def test_mention_missing_or_malformed_rejected(env):
    """选择「拒绝」语义：不存在的 / 格式非法的 record_id 一律报错，不静默丢弃。"""
    _write_record(env.records, REC_A, experiment_no="E-006")

    with pytest.raises(ValueError) as ei:
        diary_store.create_entry(content="正文", mentions=[REC_MISSING])
    assert "不存在" in str(ei.value) and REC_MISSING in str(ei.value)

    with pytest.raises(ValueError) as ei:
        diary_store.create_entry(content="正文", mentions=["随便写的编号"])
    assert "格式非法" in str(ei.value)

    with pytest.raises(ValueError):
        diary_store.create_entry(content="正文", mentions=123)  # 非数组

    assert diary_store.list_entries() == [], "拒绝时不应写盘"
    assert not env.diary.exists()


def test_mention_count_limit(env):
    ids = [f"rec_2026090{i % 9 + 1}_{i:03d}" for i in range(51)]
    with pytest.raises(ValueError) as ei:
        diary_store.create_entry(content="正文", mentions=ids)
    assert "最多" in str(ei.value) and str(diary_store.MAX_MENTIONS) in str(ei.value)


def test_historical_mention_survives_record_deletion(env):
    """记录后来被删：老日记的 @ 不失效（只校验新增项），并标记 exists=False。"""
    _write_record(env.records, REC_A, experiment_no="E-006")
    e = diary_store.create_entry(date="2026-09-10", content="正文",
                                 mentions=[REC_A])
    (env.records / f"{REC_A}.json").unlink()

    updated = diary_store.update_entry(e["entry_id"], {"title": "改标题"})
    assert updated["mentions"] == [REC_A], "历史关联不应被清掉"
    det = diary_store.mention_details(updated)
    assert det[0]["exists"] is False and det[0]["record_id"] == REC_A

    # 想新增一条确实不存在的关联 → 仍然拒绝
    with pytest.raises(ValueError):
        diary_store.update_entry(e["entry_id"],
                                 {"mentions": [REC_A, REC_MISSING]})


# ----------------------------------------------------- 关键词 / 日期检索

def test_keyword_and_date_filters(env):
    _write_record(env.records, REC_A, experiment_no="E-006")
    a = diary_store.create_entry(date="2026-09-01", title="A5 成膜",
                                 content="今天 A5 体系成膜，界面聚合 24 h。")
    b = diary_store.create_entry(date="2026-09-10", title="B 体系",
                                 content="B 体系失败了，溶剂换成 DMF。",
                                 mentions=[REC_A])
    c = diary_store.create_entry(date="2026-09-20", title="复盘",
                                 content="把 A5 与 B 体系一起复盘。")

    only = lambda kw, **kw2: [e["entry_id"] for e in            # noqa: E731
                              diary_store.list_entries(keyword=kw, **kw2)]
    assert only("成膜") == [a["entry_id"]]
    assert set(only("A5 体系")) == {a["entry_id"], c["entry_id"]}   # 多词 AND
    # 关键词也能命中「关联记录」的实验编号
    assert only("E-006") == [b["entry_id"]]
    # 日期范围（含端点）+ 组合
    assert only("", date_from="2026-09-10", date_to="2026-09-20") == \
        [c["entry_id"], b["entry_id"]]
    assert only("", record_id=REC_A, date_from="2026-09-05") == [b["entry_id"]]
    # limit
    assert len(diary_store.list_entries(limit=2)) == 2


# ------------------------------------------------------------ 助手工具

def test_tool_registered_schema_and_readonly(env):
    assert "read_diary" in registry.TOOLS
    spec = registry.TOOLS["read_diary"]
    assert "confirm" not in spec, "读工具不应挂 confirm（只读、无二次确认）"
    assert registry.confirm_impact("read_diary", {"keyword": "x"}) is None

    fn = spec["schema"]["function"]
    assert fn["name"] == "read_diary"
    props = fn["parameters"]["properties"]
    assert {"keyword", "date_from", "date_to", "record_id", "limit"} <= set(props)
    assert fn["parameters"]["required"] == []
    assert CJK.search(fn["description"]), "工具描述应为中文"
    for key, p in props.items():
        assert p.get("description") and CJK.search(p["description"]), key
    # 出现在给 LLM 的工具清单里（路径 A 与路径 B 共用 _available_tools）
    assert "read_diary" in [s["function"]["name"] for s in registry.list_tool_schemas()]
    assert "read_diary" in registry.describe_tools()


def test_tool_keyword_date_record_search(env):
    _write_record(env.records, REC_A, experiment_no="E-006")
    diary_store.create_entry(date="2026-09-01", title="A5 成膜",
                             content="界面聚合 24 小时，成膜良好。")
    diary_store.create_entry(date="2026-09-10", title="B 体系",
                             content="B 体系失败了。", mentions=[REC_A])

    r = registry.execute("read_diary", {"keyword": "成膜"})
    assert r["is_error"] is False and r["details"]["count"] == 1
    assert "2026-09-01" in r["text"] and "界面聚合 24 小时" in r["text"]
    assert "A5 成膜" in r["text"]

    r2 = registry.execute("read_diary",
                          {"date_from": "2026-09-05", "date_to": "2026-09-30"})
    assert r2["details"]["count"] == 1 and "2026-09-10" in r2["text"]

    r3 = registry.execute("read_diary", {"record_id": REC_A})
    assert r3["details"]["count"] == 1
    assert REC_A in r3["text"] and "E-006" in r3["text"]

    # limit：只影响展示条数，matched 仍是全部命中数
    r4 = registry.execute("read_diary", {"limit": 1})
    assert r4["details"]["count"] == 1 and r4["details"]["matched"] == 2
    assert r4["details"]["total"] == 2

    # 只读：工具调用不改变落盘文件
    before = env.diary.read_bytes()
    registry.execute("read_diary", {})
    registry.execute("read_diary", {"keyword": "成膜", "limit": 20})
    assert env.diary.read_bytes() == before


def test_tool_no_result_structured_not_fabricated(env):
    e = diary_store.create_entry(date="2026-09-10", title="A5 成膜",
                                 content="独一无二的正文内容ABC")
    r = registry.execute("read_diary", {"keyword": "完全不存在的关键词"})
    assert r["details"]["count"] == 0 and r["details"]["found"] is False
    assert "未查到" in r["text"]
    # 不编造、也不把无关日记倒出来
    assert "ABC" not in r["text"] and "A5 成膜" not in r["text"]

    # 空日记本同样给结构化空结果
    diary_store.delete_entry(e["entry_id"])
    r2 = registry.execute("read_diary", {})
    assert r2["is_error"] is False and r2["details"]["count"] == 0
    assert r2["details"]["total"] == 0 and "未查到" in r2["text"]


def test_tool_invalid_params_are_errors(env):
    diary_store.create_entry(date="2026-09-10", content="正文")
    for args in ({"date_from": "2026/09/01"},
                 {"record_id": "abc"},
                 {"record_id": "../../etc/passwd"},          # 越权/穿越写法
                 {"record_id": "rec_20260902_006; rm -rf"},  # 注入口吻的非法 id
                 {"date_from": "2026-09-10", "date_to": "2026-09-01"}):
        r = registry.execute("read_diary", args)
        assert r["is_error"] is True, args
        assert CJK.search(r["text"]) and "参数" in r["text"]

    r = registry.execute("read_diary", {"limit": "abc"})
    assert r["is_error"] is True and "整数" in r["text"]

    # 越权/未知参数被 execute 白名单丢弃，不影响正常检索
    ok = registry.execute("read_diary", {"keyword": "正文", "恶意参数": "x"})
    assert ok["is_error"] is False and ok["details"]["count"] == 1


def test_tool_storage_failure_is_structured_error(env, monkeypatch):
    """存储层异常 → 结构化失败 + 中文原因（不抛异常、不编造内容）。"""
    def boom(**_kw):
        raise RuntimeError("磁盘炸了")

    monkeypatch.setattr(diary_store, "list_entries", boom)
    r = registry.execute("read_diary", {"keyword": "任何"})
    assert r["is_error"] is True
    assert "日记读取失败" in r["text"] and "磁盘炸了" in r["text"]
    assert r["details"] == {}


# --------------------------------------------------------------- REST API

def test_routes_registered():
    paths = set(app.openapi()["paths"])
    assert "/api/diary" in paths
    assert "/api/diary/{entry_id}" in paths
    assert "/api/diary/by-record/{record_id}" in paths


def test_api_crud_and_reverse_lookup(env):
    _write_record(env.records, REC_A, experiment_no="E-006")

    r = client.post("/api/diary", json={
        "date": "2026-09-10", "title": "A5", "content": "# 正文",
        "mentions": [REC_A]})
    assert r.status_code == 201, r.text
    body = r.json()
    eid = body["entry_id"]
    assert body["mentions"] == [REC_A]
    assert body["mention_details"][0]["experiment_no"] == "E-006"
    assert body["mention_details"][0]["exists"] is True

    lst = client.get("/api/diary").json()
    assert lst["count"] == 1 and lst["stats"]["total"] == 1
    assert lst["entries"][0]["entry_id"] == eid

    assert client.get(f"/api/diary/{eid}").json()["content"] == "# 正文"

    r = client.put(f"/api/diary/{eid}", json={"title": "A5（改）"})
    assert r.status_code == 200
    assert r.json()["title"] == "A5（改）" and r.json()["content"] == "# 正文"

    # 按实验记录反查
    rev = client.get(f"/api/diary/by-record/{REC_A}").json()
    assert rev["count"] == 1 and rev["record_id"] == REC_A
    assert rev["entries"][0]["mention_details"][0]["experiment_no"] == "E-006"
    assert client.get(f"/api/diary/by-record/{REC_MISSING}").json()["count"] == 0

    # 清空关联：mentions=[] 明确生效，反查随即查不到
    r = client.put(f"/api/diary/{eid}", json={"mentions": []})
    assert r.status_code == 200 and r.json()["mentions"] == []
    assert client.get(f"/api/diary/by-record/{REC_A}").json()["count"] == 0

    # 关键词 / 日期过滤
    assert client.get("/api/diary", params={"keyword": "正文"}).json()["count"] == 1
    assert client.get("/api/diary",
                      params={"date_from": "2026-09-11"}).json()["count"] == 0

    assert client.delete(f"/api/diary/{eid}").status_code == 200
    assert client.get(f"/api/diary/{eid}").status_code == 404
    assert client.delete(f"/api/diary/{eid}").status_code == 404


def test_api_validation_and_missing(env):
    assert client.post("/api/diary", json={"content": "   "}).status_code == 400

    r = client.post("/api/diary", json={"content": "正文", "date": "2026/09/10"})
    assert r.status_code == 400 and "日期" in r.json()["detail"]

    r = client.post("/api/diary",
                    json={"content": "正文", "mentions": [REC_MISSING]})
    assert r.status_code == 400 and "不存在" in r.json()["detail"]

    assert client.get("/api/diary/not-an-id").status_code == 400
    assert client.get("/api/diary/dy_000000000000").status_code == 404
    assert client.put("/api/diary/dy_000000000000",
                      json={"title": "x"}).status_code == 404
    assert client.delete("/api/diary/dy_000000000000").status_code == 404

    r = client.get("/api/diary", params={"date_from": "2026-9-1"})
    assert r.status_code == 400
    assert client.get("/api/diary/by-record/乱写").status_code == 400
