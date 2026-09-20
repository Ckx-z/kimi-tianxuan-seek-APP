"""解析后元数据自动回填（v1.9.7 修复「补解析完仍显示暂无摘要」）。

用户报：补解析上传主文 PDF 与 SI 后，文献仍显示「该文献暂无摘要」。
实测根因：解析只在响应里返回 `paper_meta` 供预览，**回填必须用户手动点按钮**；
文献 1529 已成功解析（抽出 22 张图）但 `abstract` 仍为空即此因。

本文件锁定：解析成功后自动回填（只补空字段、绝不覆盖），可关闭，且幂等。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import graph_ingest, llm_extract  # noqa: E402
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

META = {
    "title": "Rational design of fluorinated COF coatings",
    "authors": ["Hong Xu", "Jia Gao"],
    "journal": "Talanta",
    "year": 2026,
    "doi": "10.1016/j.talanta.2026.129813",
    "abstract": "Fluorinated covalent organic frameworks were grown as coatings for "
                "SPME of isoxazoline veterinary drugs, showing high enrichment.",
}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(titles, "TITLES_PATH", tmp_path / "paper_titles.json")
    patched = []
    for name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", tmp_path / "paper_titles.json")
            patched.append(mod)
    monkeypatch.setattr(graph_ingest, "_app_root", lambda: tmp_path)
    (tmp_path / "paper_titles.json").write_text("{}", encoding="utf-8")
    for mod in patched:
        mod.reload()
    yield tmp_path
    for mod in patched:
        mod.reload()


def _pdf(text: str | None = None) -> bytes:
    """合成 PDF：每页需 ≥50 字，否则会被判为「扫描件」跳过（MIN_PDF_CHARS_PER_PAGE）。"""
    body = text or ("Fluorinated covalent organic frameworks were grown on fibers as "
                    "coatings for solid-phase microextraction of veterinary drugs; "
                    "the abstract summarises synthesis and performance. ")
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 60), body, fontsize=9)
    blob = doc.tobytes()
    doc.close()
    return blob


def _stub_meta(monkeypatch, meta: dict):
    monkeypatch.setattr(
        llm_extract, "extract_paper_meta",
        lambda text: {"meta": dict(meta), "note": "stub"})


def _create_paper(doi: str) -> str:
    # confirm 端点要求标题非空（标题为空会被拒），故给一个占位标题；
    # 自动回填测试关心的是「空字段被补上」，占位标题不影响断言（会被 skipped）。
    r = client.post("/api/literature/confirm",
                    json={"title": f"占位标题 {doi}", "doi": doi, "folder": "收藏夹1"})
    assert r.status_code in (200, 201), r.text
    return str(r.json()["paper_id"])


def _parse(pid: str, *, auto_backfill: bool | None = None):
    data = {"with_meta": "true"}
    if auto_backfill is not None:
        data["auto_backfill"] = "true" if auto_backfill else "false"
    return client.post(f"/api/literature/{pid}/parse",
                       files=[("files", ("paper.pdf", _pdf(), "application/pdf"))],
                       data=data)


def test_parse_auto_backfills_abstract_and_meta(isolate, monkeypatch):
    pid = _create_paper("10.1000/auto-backfill-1")
    _stub_meta(monkeypatch, META)
    res = _parse(pid)
    assert res.status_code == 200, res.text
    body = res.json()
    back = body.get("auto_backfill") or {}
    assert "abstract" in (back.get("updated") or []), back
    # confirm 时已带占位标题与 DOI → 这两个必须被跳过（only_empty 语义）
    assert "title" in (back.get("skipped") or []), back
    assert "doi" in (back.get("skipped") or []), back
    assert {"authors", "journal", "year"} <= set(back.get("updated") or []), back

    detail = client.get(f"/api/literature/papers/{pid}").json()
    assert detail["abstract"].startswith("Fluorinated covalent organic frameworks")
    assert detail["journal"] == "Talanta"
    # 占位标题未被 LLM 结果覆盖
    assert detail["title"].startswith("占位标题")


def test_auto_backfill_never_overwrites_existing(isolate, monkeypatch):
    """已有值不覆盖（Crossref 入库的 title 等必须保留），只补空字段。"""
    pid = _create_paper("10.1000/auto-backfill-2")
    # 先写入一个「已有标题」
    r = client.patch(f"/api/literature/papers/{pid}",
                     json={"title": "Crossref 原标题", "only_empty": False})
    assert r.status_code == 200
    _stub_meta(monkeypatch, META)
    body = _parse(pid).json()
    back = body.get("auto_backfill") or {}
    assert "title" in (back.get("skipped") or []), back
    assert "abstract" in (back.get("updated") or []), back
    detail = client.get(f"/api/literature/papers/{pid}").json()
    assert detail["title"] == "Crossref 原标题"
    assert detail["abstract"]


def test_auto_backfill_can_be_disabled(isolate, monkeypatch):
    pid = _create_paper("10.1000/auto-backfill-3")
    _stub_meta(monkeypatch, META)
    body = _parse(pid, auto_backfill=False).json()
    assert "auto_backfill" not in body or not (body.get("auto_backfill") or {}).get("updated")
    detail = client.get(f"/api/literature/papers/{pid}").json()
    assert not (detail.get("abstract") or "")


def test_auto_backfill_is_idempotent(isolate, monkeypatch):
    pid = _create_paper("10.1000/auto-backfill-4")
    _stub_meta(monkeypatch, META)
    first = _parse(pid).json().get("auto_backfill") or {}
    assert first.get("updated")
    second = _parse(pid).json().get("auto_backfill") or {}
    # 第二次全部跳过（字段已存在），不产生改动
    assert second.get("updated") == []
    assert set(second.get("skipped") or []) >= {"abstract", "title"}


def test_parse_response_still_carries_preview_meta(isolate, monkeypatch):
    """预览仍返回 paper_meta（前端需要展示摘要），且带 auto_backfill 结果。"""
    pid = _create_paper("10.1000/auto-backfill-5")
    _stub_meta(monkeypatch, META)
    body = _parse(pid).json()
    assert body["paper_meta"]["abstract"].startswith("Fluorinated")
    assert "auto_backfill" in body
