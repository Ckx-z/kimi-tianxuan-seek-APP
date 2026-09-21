"""C3 补全测试：OpenAlex/S2 解析 / 只补空 / 指标落库 / 批量节流与幂等 / 失败容忍。

不打真实网络：注入 fetchers/http 打桩。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import library_db, openalex  # noqa: E402

PAPERS = {
    "1": {"title": "No year paper", "doi": "10.1/a", "source": "builtin"},   # 缺 year/journal
    "2": {"title": "Has year", "doi": "10.1/b", "year": 2020, "journal": "JACS",
          "source": "builtin"},
    "3": {"title": "No DOI paper", "source": "builtin"},
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, res = tmp_path / "data", tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
    patched = []
    for name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(name)
    assert patched
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    monkeypatch.setenv("COF_DATA_DIR", str(data))
    library_db.reset_ready_cache()
    yield {"data": data}
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    yield c
    c.close()


OA = {"source": "openalex", "doi": "10.1/a", "title": "T", "year": 2024,
      "journal": "Talanta", "cited_by_count": 42, "references": 55}
S2 = {"source": "semantic_scholar", "doi": "10.1/a", "title": "T", "year": 2023,
      "journal": "Talanta", "cited_by_count": 40, "references": 50}


# ---------------------------------------------------------------- 源解析

def test_fetch_openalex_parses_and_tolerates_failure():
    def fake_http(url):
        assert "api.openalex.org/works/doi:10.1/a" in url and "mailto=" in url
        return {"title": "T", "publication_year": 2024, "cited_by_count": 42,
                "referenced_works": ["w1", "w2"], "primary_location":
                {"source": {"display_name": "Talanta"}}}

    got = openalex.fetch_openalex("10.1/A", http=fake_http)      # 大小写无关
    assert got["year"] == 2024 and got["cited_by_count"] == 42
    assert got["references"] == 2 and got["journal"] == "Talanta"

    def boom(url):
        raise OSError("no net")

    assert "error" in openalex.fetch_openalex("10.1/a", http=boom)
    assert "error" in openalex.fetch_openalex("")


def test_fetch_s2_parses():
    def fake_http(url):
        assert "semanticscholar.org" in url and "fields=" in url
        return {"title": "T", "year": 2023, "citationCount": 40,
                "referenceCount": 50, "venue": "Talanta"}

    got = openalex.fetch_s2("10.1/a", http=fake_http)
    assert got["cited_by_count"] == 40 and got["references"] == 50


# ---------------------------------------------------------------- 单篇补全

def test_enrich_fills_empty_only_and_records_metrics(conn):
    out = openalex.enrich_paper(conn, "1", fetchers={"openalex": lambda d: OA})
    assert out["metrics"]["cited_by_count"] == 42
    assert out["updated"]["year"].startswith("2024")
    row = conn.execute("SELECT year,journal FROM items WHERE paper_id='1'").fetchone()
    assert row["year"] == 2024 and row["journal"] == "Talanta"
    m = conn.execute("SELECT value,computed_at FROM graph_metrics WHERE node_id='paper:1'"
                     " AND metric='cited_by_count'").fetchone()
    assert m["value"] == 42 and "openalex" in m["computed_at"]   # 来源逐字段标注

    # 文献 2 已有 year/journal → **不覆盖**
    out2 = openalex.enrich_paper(conn, "2", fetchers={"openalex": lambda d: OA})
    assert "year" not in out2["updated"] and "journal" not in out2["updated"]
    row2 = conn.execute("SELECT year,journal FROM items WHERE paper_id='2'").fetchone()
    assert row2["year"] == 2020 and row2["journal"] == "JACS"


def test_enrich_idempotent_and_force(conn):
    openalex.enrich_paper(conn, "1", fetchers={"openalex": lambda d: OA})
    again = openalex.enrich_paper(conn, "1", fetchers={"openalex": lambda d: OA})
    assert "已补全过" in again["skipped"]
    forced = openalex.enrich_paper(conn, "1", force=True,
                                   fetchers={"openalex": lambda d: OA})
    assert forced["metrics"]["cited_by_count"] == 42


def test_enrich_skips_without_doi_and_reports_errors(conn):
    assert openalex.enrich_paper(conn, "3")["skipped"] == "无 DOI"
    out = openalex.enrich_paper(
        conn, "1", sources=("openalex", "semantic_scholar"),
        fetchers={"openalex": lambda d: {"error": "429 限速"},
                  "semantic_scholar": lambda d: S2})
    assert any("429" in e for e in out["errors"])
    assert out["metrics"]["cited_by_count"] == 40          # 另一个源仍然生效


# ---------------------------------------------------------------- 批量

def test_enrich_batch_targets_missing_year_and_throttles(conn):
    slept: list[float] = []
    out = openalex.enrich_batch(conn, limit=10, delay=0.5,
                                fetchers={"openalex": lambda d: OA},
                                sleep=lambda s: slept.append(s))
    # 只有文献 1 缺 year（2 有 year，3 无 DOI）
    assert out["targets"] == 1 and out["enriched"] == 1
    assert slept == []                                     # 单条不用等

    # 全部处理后：改成不筛缺 year → 会处理 1、2（3 无 DOI 被跳过）
    out2 = openalex.enrich_batch(conn, limit=10, only_missing_year=False,
                                 delay=0.25,
                                 fetchers={"openalex": lambda d: OA},
                                 sleep=lambda s: slept.append(s))
    assert out2["targets"] == 2
    assert out2["skipped"] >= 1                            # 文献 1 已补过
    assert slept, "多条时应有节流等待"


def test_status_overview(conn):
    openalex.enrich_paper(conn, "1", fetchers={"openalex": lambda d: OA})
    st = openalex.status(conn)
    assert st["papers"] == 3 and st["with_doi"] == 2
    assert st["enriched_cited_by"] == 1
    assert st["top_cited"][0]["paper_id"] == "1"
    assert st["top_cited"][0]["cited_by"] == 42
