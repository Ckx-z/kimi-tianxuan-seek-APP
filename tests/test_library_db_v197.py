"""文献库 SQLite 单库（v1.9.7 P1/P2）测试。

覆盖：建表与默认集合、从既有文件迁移（含只读备份）、迁移校验、服务端检索
（q/limit/offset/sort/collection/tag）、集合 CRUD 与归属、标签（manual/auto）、笔记 CRUD、
跨文献数值检索、关系幂等写入、以及 API 层端到端。

设计前提：DB 是**镜像 + 组织层**，原文件仍是写入事实来源 → 测试既验证迁移正确，
也验证「DB 可丢弃重建」。
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

from literature import library_db  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

PAPERS_BUILTIN = {
    "1": {"title": "Fluorinated COF coatings for SPME", "doi": "10.1016/j.talanta.1",
          "journal": "Talanta", "year": 2026, "authors": ["Hong Xu"],
          "source": "builtin", "abstract": "Fluorinated covalent organic frameworks"},
    "2": {"title": "Triazine COFs for CO2 separation", "doi": "10.1000/triazine",
          "journal": "JACS", "year": 2025, "authors": ["A. Author"],
          "source": "builtin", "abstract": "Triazine-based frameworks"},
}
PAPERS_USER = {
    "1782": {"title": "新录入：TAPT-A7 成膜", "doi": "10.1000/new1782",
             "journal": "COF Letters", "year": 2026, "authors": [], "source": "user-intake"},
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离 user_data_root / resource_root，并铺好 4 类源文件。"""
    data = tmp_path / "data"
    res = tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS_BUILTIN, ensure_ascii=False), encoding="utf-8")
    (data / "literature" / "paper_titles.json").write_text(
        json.dumps(PAPERS_USER, ensure_ascii=False), encoding="utf-8")
    (data / "literature" / "pdfs_index.jsonl").write_text("\n".join([
        json.dumps({"file_id": "pf_a", "paper_id": "1", "filename": "main.pdf",
                    "role": "main", "sha1": "s1", "size": 100, "pages": 11,
                    "chars": 61011, "uploaded_at": "2026-09-17T10:00:00+08:00"}),
        json.dumps({"file_id": "pf_b", "paper_id": "1", "filename": "si.pdf",
                    "role": "si", "sha1": "s2", "size": 50, "pages": 17,
                    "chars": 14341, "uploaded_at": "2026-09-17T10:01:00+08:00"}),
    ]), encoding="utf-8")
    (data / "literature" / "figures_index.json").write_text(json.dumps({
        "figures": [{"fig_id": "fig_1", "paper_id": "1", "figure_type": "spectra",
                     "caption": "Fig. 1. PXRD patterns", "tags": ["自动抽取"],
                     "meta": {"page": 4, "kind": "embedded"}, "file": "fig_1.jpg",
                     "mime": "image/jpeg", "size": 1234,
                     "created_at": "2026-09-17T10:00:00+08:00"}],
    }, ensure_ascii=False), encoding="utf-8")
    (data / "literature" / "knowledge_entries.jsonl").write_text("\n".join([
        json.dumps({"entry_id": "e1", "paper_id": "1", "group_id": "G1",
                    "kind": "characterization", "technique": "BET",
                    "metrics": [{"name": "比表面积", "value": 2105, "unit": "m2/g"},
                                {"name": "孔径", "value": 3.26, "unit": "nm"}],
                    "evidence": "BET surface area 2105", "source": "llm_extract",
                    "created_at": "2026-09-17T10:00:00+08:00"}),
        json.dumps({"entry_id": "e2", "paper_id": "1782", "group_id": "G2",
                    "kind": "film_outcome", "film_label": 1.0,
                    "evidence": "完整成膜", "source": "user"}),
    ]), encoding="utf-8")

    patched = []
    for mod_name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(mod_name)
    assert patched, "未能定位 runtime_config"
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    library_db.reset_ready_cache()
    yield {"data": data, "res": res}
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    yield c
    c.close()


# ---------------------------------------------------------------- 建表 / 迁移

def test_schema_and_default_collections(conn):
    cols = library_db.list_collections(conn)
    ids = [c["collection_id"] for c in cols]
    for cid, _name in library_db.DEFAULT_COLLECTIONS:
        assert cid in ids
    assert ids[-1] == ""                      # 末位是「未分类」
    assert cols[-1]["name"] == "未分类"


def test_migration_from_files_with_backup(conn, env):
    row = conn.execute("SELECT value_json FROM settings WHERE key='migrated_counts'").fetchone()
    counts = json.loads(row["value_json"])
    # 文献 = 内置 2 + 用户 1（覆盖 1 篇不重复）
    assert counts["papers"] == 3
    assert counts["attachments"] == 2
    assert counts["figures"] == 1
    assert counts["entries"] == 2
    assert counts["metrics"] == 2
    # 只读备份目录存在且含原文件
    backups = list((env["data"] / "literature").glob("_backup_files_*"))
    assert backups, "迁移应生成只读备份"
    names = {p.name for p in backups[0].iterdir()}
    assert {"pdfs_index.jsonl", "figures_index.json", "knowledge_entries.jsonl"} <= names


def test_migration_is_idempotent(conn):
    again = library_db.migrate_from_files(conn)
    assert again.get("skipped") is True


def test_verify_migration_ok(conn):
    res = library_db.verify_migration(conn)
    assert res["ok"] is True, res
    assert res["mismatch"] == {}
    assert res["doi_mismatch"] == 0


def test_rebuild_keeps_counts(env):
    """丢弃重建后计数不变（Windows 下须先关闭连接，否则文件被占用）。"""
    library_db.ensure_ready()
    path = env["data"] / "literature" / "library.db"
    conn = library_db.connect(path)
    before = library_db.stats(conn)
    conn.close()                       # 关键：释放文件句柄
    out = library_db.rebuild(backup=True)
    c2 = library_db.connect(path)
    after = library_db.stats(c2)
    c2.close()
    assert after["papers"] == before["papers"] == 3
    assert after["entries"] == before["entries"] == 2
    assert out.get("replaced")


# ---------------------------------------------------------------- 检索

def test_search_papers_full_text_and_filters(conn):
    res = library_db.search_papers(conn, q="SPME")
    assert res["total"] == 1 and res["papers"][0]["paper_id"] == "1"

    res2 = library_db.search_papers(conn, q="Triazine")
    assert res2["total"] == 1 and res2["papers"][0]["paper_id"] == "2"

    # 分词 OR 检索（FTS5）
    res3 = library_db.search_papers(conn, q="SPME Triazine")
    assert res3["total"] == 2

    # 分页
    page1 = library_db.search_papers(conn, limit=1, offset=0, sort="paper_id", direction="asc")
    page2 = library_db.search_papers(conn, limit=1, offset=1, sort="paper_id", direction="asc")
    assert page1["count"] == 1 and page2["count"] == 1
    assert page1["papers"][0]["paper_id"] != page2["papers"][0]["paper_id"]


def test_search_papers_sort_and_source(conn):
    by_year = library_db.search_papers(conn, sort="year", direction="desc")
    years = [p["year"] for p in by_year["papers"]]
    assert years == sorted(years, reverse=True)
    only_user = library_db.search_papers(conn, source="user-intake")
    assert [p["paper_id"] for p in only_user["papers"]] == ["1782"]


def test_search_papers_parse_status(conn):
    """v1.9.8 R1：列表行带解析状态（摘要/条目/图片 三项）供中栏徽标。

    断言「状态与三项计数自洽」而非硬编码某篇文献的状态（fixture 里各文献
    的条目/图片归属会随用例演进，硬编码易脆）。
    """
    res = library_db.search_papers(conn, limit=10)
    assert res["papers"], "应有文献"
    for p in res["papers"]:
        done = sum((bool(p["has_abstract"]), p["entries_count"] > 0,
                    p["figures_count"] > 0))
        expect = "parsed" if done >= 2 else "partial" if done == 1 else "none"
        assert p["parse_status"] == expect, p
        assert len(p["missing_parts"]) == 3 - done, p
    # 详情行必须带上这三个计数字段（UI 徽标与 R2「补缺」判定都依赖）
    assert {"entries_count", "figures_count", "has_abstract",
            "parse_status", "missing_parts"} <= set(res["papers"][0].keys())


def test_search_papers_by_collection_and_tag(conn):
    library_db.assign_collection(conn, "1", "col_cof_film", add=True)
    res = library_db.search_papers(conn, collection_id="col_cof_film")
    assert [p["paper_id"] for p in res["papers"]] == ["1"]

    library_db.set_tag(conn, "1", "溶剂热", add=True)
    res2 = library_db.search_papers(conn, tag="溶剂热")
    assert [p["paper_id"] for p in res2["papers"]] == ["1"]
    # 未分类计数会随归属变化
    counts = {c["collection_id"]: c["count"] for c in library_db.list_collections(conn)}
    assert counts["col_cof_film"] == 1
    assert counts[""] == 2


# ---------------------------------------------------------------- 集合 / 标签

def test_collection_crud_and_nesting(conn):
    root = library_db.create_collection(conn, "自建集合")
    child = library_db.create_collection(conn, "子集合", parent_id=root["collection_id"])
    cols = library_db.list_collections(conn)
    by_id = {c["collection_id"]: c for c in cols}
    assert by_id[child["collection_id"]]["parent_id"] == root["collection_id"]
    assert library_db.rename_collection(conn, root["collection_id"], "改名后")["name"] == "改名后"
    assert library_db.delete_collection(conn, root["collection_id"]) is True
    assert root["collection_id"] not in {
        c["collection_id"] for c in library_db.list_collections(conn)}
    with pytest.raises(ValueError):
        library_db.create_collection(conn, "   ")


def test_tags_manual_and_auto_counts(conn):
    library_db.set_tag(conn, "1", "PXRD", add=True, type_="auto")
    library_db.set_tag(conn, "2", "PXRD", add=True, type_="manual")
    tags = {t["name"]: t for t in library_db.list_tags(conn)}
    assert tags["PXRD"]["count"] == 2
    library_db.set_tag(conn, "2", "PXRD", add=False)
    tags2 = {t["name"]: t for t in library_db.list_tags(conn)}
    assert tags2["PXRD"]["count"] == 1
    # 标签会进 FTS（可用标签检索到文献）
    assert library_db.search_papers(conn, q="PXRD")["total"] == 1


# ---------------------------------------------------------------- 笔记

def test_notes_crud(conn):
    note = library_db.add_note(conn, "1", "## 精读要点\n- 成膜良好", title="精读",
                               source="llm")
    assert note["note_id"].startswith("note_")
    listing = library_db.list_notes(conn, "1")
    assert len(listing) == 1 and "成膜良好" in listing[0]["content_md"]
    updated = library_db.update_note(conn, note["note_id"], content_md="改后内容")
    assert updated["content_md"] == "改后内容"
    assert library_db.delete_note(conn, note["note_id"]) is True
    assert library_db.list_notes(conn, "1") == []
    with pytest.raises(ValueError):
        library_db.add_note(conn, "9999", "x")


# ---------------------------------------------------------------- 数值检索 / 关系 / 统计

def test_metrics_search(conn):
    allm = library_db.search_metrics(conn)
    assert len(allm) == 2
    hot = library_db.search_metrics(conn, min_value=1000)
    assert [m["name"] for m in hot] == ["比表面积"]
    assert hot[0]["paper_id"] == "1" and hot[0]["value"] == 2105
    by_tech = library_db.search_metrics(conn, technique="BET")
    assert len(by_tech) == 2


def test_relations_idempotent(conn):
    assert library_db.add_relation(conn, "paper:1", "cites", "paper:9",
                                   source="crossref", confidence=1.0) is True
    assert library_db.add_relation(conn, "paper:1", "cites", "paper:9",
                                   source="crossref") is False   # 幂等
    rels = library_db.list_relations(conn, subject="paper:1")
    assert len(rels) == 1 and rels[0]["predicate"] == "cites"


def test_stats(conn):
    s = library_db.stats(conn)
    assert s["papers"] == 3 and s["figures"] == 1 and s["entries"] == 2
    assert s["collections"] >= len(library_db.DEFAULT_COLLECTIONS)
    assert s["size_kb"] > 0


# ---------------------------------------------------------------- API

def test_api_status_and_search(env):
    r = client.get("/api/library/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["stats"]["papers"] == 3
    assert body["verify"]["ok"] is True

    r2 = client.get("/api/library/papers", params={"q": "SPME", "limit": 10})
    assert r2.status_code == 200
    assert r2.json()["total"] == 1
    pid = r2.json()["papers"][0]["paper_id"]

    r3 = client.get(f"/api/library/papers/{pid}")
    assert r3.status_code == 200 and r3.json()["paper_id"] == pid
    assert client.get("/api/library/papers/999999").status_code == 404


def test_api_patch_paper_and_collections(env):
    r = client.patch("/api/library/papers/1", json={
        "journal": "Talanta (revised)", "authors": ["Hong Xu", "Jia Gao"]})
    assert r.status_code == 200, r.text
    assert r.json()["paper"]["journal"] == "Talanta (revised)"
    assert r.json()["paper"]["authors"] == ["Hong Xu", "Jia Gao"]

    created = client.post("/api/library/collections", json={"name": "API 集合"})
    assert created.status_code == 201
    cid = created.json()["collection_id"]
    assert client.post(f"/api/library/papers/1/collections/{cid}").status_code == 200
    lst = client.get("/api/library/papers", params={"collection_id": cid}).json()
    assert lst["total"] == 1
    assert client.delete(f"/api/library/papers/1/collections/{cid}").status_code == 200
    assert client.delete(f"/api/library/collections/{cid}").status_code == 200
    assert client.patch("/api/library/collections/nope", json={"name": "x"}).status_code == 404


def test_api_tags_notes_metrics(env):
    assert client.post("/api/library/papers/1/tags",
                       json={"tag": "COF"}).status_code == 200
    tags = client.get("/api/library/tags").json()["tags"]
    assert any(t["name"] == "COF" for t in tags)

    n = client.post("/api/library/papers/1/notes",
                    json={"content_md": "笔记内容", "title": "T", "source": "user"})
    assert n.status_code == 201
    nid = n.json()["note_id"]
    assert client.patch(f"/api/library/notes/{nid}", json={"title": "T2"}).status_code == 200
    assert client.get("/api/library/papers/1/notes").json()["notes"][0]["title"] == "T2"
    assert client.delete(f"/api/library/notes/{nid}").status_code == 200

    m = client.get("/api/library/metrics", params={"min_value": 1000}).json()["metrics"]
    assert len(m) == 1 and m[0]["name"] == "比表面积"
    assert client.get("/api/library/relations").json()["relations"] == []
