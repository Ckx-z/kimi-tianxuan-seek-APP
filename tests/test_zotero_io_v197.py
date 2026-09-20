"""Zotero 互操作（v1.9.7 P5）测试：本地 API 导入 + BibTeX / CSL-JSON 导出。

不连真实 Zotero：`fetch_items` 的 HTTP 走打桩；导出为纯函数直接断言。
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import library_db, zotero_io  # noqa: E402

ZOTERO_ITEMS = [
    {
        "key": "ABCD1234",
        "data": {
            "itemType": "journalArticle",
            "title": "Fluorinated COF membranes for SPME",
            "creators": [{"firstName": "Hong", "lastName": "Xu"},
                         {"firstName": "Jia", "lastName": "Gao"},
                         {"name": "COF Consortium"}],
            "DOI": "https://doi.org/10.1016/J.TALANTA.2026.1",
            "publicationTitle": "Talanta",
            "date": "2026-05-01",
            "url": "https://example.org/a",
            "abstractNote": "Fluorinated covalent organic frameworks ...",
            "tags": [{"tag": "COF"}, {"tag": "SPME"}],
        },
    },
    {   # 无标题 → 跳过
        "key": "NOPE0001",
        "data": {"itemType": "journalArticle", "title": "", "DOI": "10.1/x"},
    },
]

EXISTING = {
    "1": {"title": "已在库中的文献", "doi": "10.1000/existing", "source": "builtin"},
    "2": {"title": "重复 DOI 的文献", "doi": "10.1016/j.talanta.2026.1",
          "source": "builtin"},
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    res = tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(EXISTING, ensure_ascii=False), encoding="utf-8")
    patched = []
    for mod_name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(mod_name)
    assert patched
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    library_db.reset_ready_cache()
    yield {"data": data}
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    yield c
    c.close()


# ---------------------------------------------------------------- 映射

def test_to_paper_mapping():
    p = zotero_io.to_paper(ZOTERO_ITEMS[0])
    assert p["doi"] == "10.1016/j.talanta.2026.1"          # DOI 归一化（含 URL 前缀/大写）
    assert p["authors"] == ["Hong Xu", "Jia Gao", "COF Consortium"]
    assert p["journal"] == "Talanta" and p["year"] == 2026
    assert p["source"] == "zotero-import"
    assert p["tags"] == ["COF", "SPME"]
    assert json.loads(p["extra_json"])["zotero_key"] == "ABCD1234"
    assert zotero_io.to_paper(ZOTERO_ITEMS[1]) is None      # 无标题跳过


# ---------------------------------------------------------------- 导入

def test_import_entries_dedup_by_doi(conn):
    # fixture 里 EXISTING["2"] 的 DOI 与 Zotero 条目相同 → 必须判为重复
    dup = zotero_io.import_entries(conn, ZOTERO_ITEMS)
    assert dup["parsed"] == 1 and dup["imported"] == 0 and dup["duplicate_doi"] == 1

    # 新 DOI → 真正写入（含标签、集合归属）
    fresh = [{"key": "NEW00001", "data": {
        "itemType": "journalArticle", "title": "TAPT-A7 film formation study",
        "creators": [{"firstName": "Hong", "lastName": "Xu"}],
        "DOI": "10.1016/j.talanta.2026.NEW", "publicationTitle": "Talanta",
        "date": "2026", "abstractNote": "film formation", "tags": [{"tag": "COF"}]}}]
    out = zotero_io.import_entries(conn, fresh)
    assert out["imported"] == 1
    row = conn.execute("SELECT paper_id,title,source FROM items WHERE doi=?",
                       ("10.1016/j.talanta.2026.new",)).fetchone()
    assert row is not None and row["source"] == "zotero-import"
    tags = [r["tag"] for r in conn.execute("SELECT tag FROM item_tags WHERE paper_id=?",
                                           (row["paper_id"],))]
    assert "COF" in tags
    cols = [r["collection_id"] for r in conn.execute(
        "SELECT collection_id FROM collection_items WHERE paper_id=?", (row["paper_id"],))]
    assert zotero_io.IMPORT_COLLECTION_ID in cols
    # 幂等：再导入一次不重复
    again = zotero_io.import_entries(conn, fresh)
    assert again["imported"] == 0 and again["duplicate_doi"] == 1


def test_import_dry_run(conn):
    out = zotero_io.import_entries(conn, ZOTERO_ITEMS, dry_run=True)
    assert out["dry_run"] is True
    assert out["importable"] == 0 and out["duplicate_doi"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM items WHERE source='zotero-import'"
                        ).fetchone()["c"] == 0


# ---------------------------------------------------------------- 网络关卡提示

def test_fetch_items_reports_clear_blockers(monkeypatch):
    def raise_403(*_a, **_k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(zotero_io.urllib.request, "urlopen", raise_403)
    with pytest.raises(zotero_io.ZoteroError) as e1:
        zotero_io.fetch_items()
    assert "Advanced" in str(e1.value) and "403" not in str(e1.value)

    def raise_conn(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(zotero_io.urllib.request, "urlopen", raise_conn)
    with pytest.raises(zotero_io.ZoteroError) as e2:
        zotero_io.fetch_items()
    msg = str(e2.value)
    assert "23119" in msg and ("Zotero" in msg)
    assert "OSError" in msg                       # 指出具体关卡类型


def test_fetch_items_parses_json(monkeypatch):
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(ZOTERO_ITEMS).encode("utf-8")

    monkeypatch.setattr(zotero_io.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    items = zotero_io.fetch_items(limit=10)
    assert len(items) == 2 and items[0]["key"] == "ABCD1234"


# ---------------------------------------------------------------- 导出

def test_bibtex_export():
    rows = [{"paper_id": "1782", "title": "TAPT-A7 film", "authors": ["Hong Xu"],
             "journal": "Talanta", "year": 2026, "doi": "10.1/a",
             "url": "https://doi.org/10.1/a", "abstract": "abstract text"}]
    bib = zotero_io.to_bibtex(rows)
    assert bib.startswith("@article{1782Xu,")
    assert "title = {TAPT-A7 film}" in bib
    assert "author = {Hong Xu}" in bib
    assert "year = {2026}" in bib and "doi = {10.1/a}" in bib
    assert zotero_io.to_bibtex([]) == ""


def test_csl_json_export():
    rows = [{"paper_id": "1782", "title": "TAPT-A7 film",
             "authors": ["Hong Xu", "Jia Gao"], "journal": "Talanta", "year": 2026,
             "doi": "10.1/a", "url": "", "abstract": "abs"}]
    out = zotero_io.to_csl_json(rows)
    assert isinstance(out, list) and out[0]["type"] == "article-journal"
    assert out[0]["DOI"] == "10.1/a"
    assert out[0]["issued"] == {"date-parts": [[2026]]}
    assert out[0]["author"][0] == {"family": "Xu", "given": "Hong"}
    assert "URL" not in out[0]                    # 空值被剔除
