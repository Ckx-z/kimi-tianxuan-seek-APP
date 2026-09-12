"""文献图自动抽取（v1.9.4 方案 A）测试。

覆盖：
- 内嵌位图抽取 + 图注识别 + 类型归类（PXRD → spectra、Scheme → structure）；
- 矢量图页渲染兜底（有图注但无位图的页 → page_render + structure）；
- 过滤：过小图丢弃、重复图去重、超大图丢弃；
- 暂存 → 入库（figures 存储）→ 条目↔图关联（evidence 提到同一图号）；
- API：parse 响应带 figures、暂存图预览、import 端点、丢弃端点。
不依赖网络与 LLM（解析走正则降级）。
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

import fitz  # noqa: E402
from literature import figures, graph_ingest, knowledge, pdf_figures  # noqa: E402
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"
B5 = "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(figures, "FIGURES_DIR", tmp_path / "literature" / "figures")
    monkeypatch.setattr(figures, "INDEX_PATH",
                        tmp_path / "literature" / "figures_index.json")
    # figures 有模块级缓存：不打桩会读到上个测试文件的记录（跨文件污染）
    monkeypatch.setattr(figures, "_cache", None)
    monkeypatch.setattr(pdf_figures, "STAGING_DIR",
                        tmp_path / "literature" / "figure_staging")
    monkeypatch.setattr(knowledge, "ENTRIES_PATH",
                        tmp_path / "literature" / "knowledge_entries.jsonl")
    monkeypatch.setattr(graph_ingest, "_app_root", lambda: tmp_path)
    patched = []
    for mod_name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(mod_name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", tmp_path / "paper_titles.json")
            patched.append(mod)
    (tmp_path / "paper_titles.json").write_text(json.dumps({
        "1": {"title": "TPD-DMTP-COF 文献", "doi": "10.1038/nchem.2352"},
    }, ensure_ascii=False), encoding="utf-8")
    for mod in patched:
        mod.reload()
    yield tmp_path
    for mod in patched:
        mod.reload()


def _png(width: int = 300, height: int = 220, color=(0.2, 0.4, 0.8)) -> bytes:
    """造一张真实 PNG（画矩形+文字后渲染）。"""
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(fitz.Rect(4, 4, max(6, width - 4), max(6, height - 4)),
                   color=color, fill=(0.9, 0.95, 1.0), width=2)
    if width > 60:
        page.draw_line(fitz.Point(20, height - 40), fitz.Point(width - 20, 30),
                       color=color, width=3)
        page.insert_text((24, 40), "PXRD", fontsize=18)
    blob = page.get_pixmap().tobytes("png")
    doc.close()
    return blob


PARA = ("Covalent organic frameworks (COFs) are crystalline porous polymers built from "
        "organic building units through dynamic covalent bonds, and their film formation "
        "behaviour depends on solvent, modulator and interfacial growth conditions. ")


def _pdf_with_figures(*, small_image: bool = False,
                      duplicate: bool = False,
                      vector_only_page: bool = False) -> bytes:
    doc = fitz.open()
    # 第 1 页：内嵌位图 + 图注（PXRD → spectra）
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), PARA * 2, fontsize=9)      # 文本层（避免被判为扫描件）
    if small_image:
        page.insert_image(fitz.Rect(60, 200, 100, 240), stream=_png(40, 40))
    else:
        page.insert_image(fitz.Rect(60, 200, 360, 420), stream=_png())
    if duplicate:
        page.insert_image(fitz.Rect(60, 430, 360, 650), stream=_png())
    page.insert_text((60, 660), "Figure 1. PXRD patterns of TPD-DMTP-COF.", fontsize=10)
    # 第 2 页：只有矢量绘制 + Scheme 图注 → 触发页渲染兜底（structure）
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((60, 60), PARA * 2, fontsize=9)
    page2.draw_line(fitz.Point(80, 400), fitz.Point(200, 200), color=(0, 0, 0), width=2)
    page2.draw_line(fitz.Point(200, 200), fitz.Point(320, 400), color=(0, 0, 0), width=2)
    page2.insert_text((80, 470), "Scheme 1. Synthesis of TPD-DMTP-COF.", fontsize=10)
    if vector_only_page:
        page2.insert_text((80, 90), "Scheme 1. Synthesis route overview.", fontsize=10)
    blob = doc.tobytes()
    doc.close()
    return blob


# ---------------------------------------------------------------- 归类

@pytest.mark.parametrize("caption,expected", [
    ("Figure 1. PXRD patterns of the COF.", "spectra"),
    ("Fig. 2. FTIR spectra of TPD-DMTP-COF.", "spectra"),
    ("Figure 3. N2 adsorption-desorption isotherms.", "spectra"),
    ("Scheme 1. Synthesis of TPD-DMTP-COF.", "structure"),
    ("Figure 4. Crystal structure of the framework.", "structure"),
    ("Figure 5. Proposed formation mechanism.", "mechanism"),
    ("Table 2. Textural properties.", "mechanism"),
])
def test_classify_figure(caption, expected):
    assert pdf_figures.classify_figure(caption) == expected


def test_caption_and_axis_helpers():
    assert pdf_figures._CAPTION_RE.match("Figure 3. Something")
    assert pdf_figures._CAPTION_RE.match("图 3 不同溶剂下的成膜情况")
    assert not pdf_figures._CAPTION_RE.match("As shown in Figure 3, the ...")


# ---------------------------------------------------------------- 抽取

def test_extract_embedded_and_page_render():
    cands = pdf_figures.extract_candidates(data=_pdf_with_figures())
    kinds = {c["kind"] for c in cands}
    assert "embedded" in kinds and "page_render" in kinds
    embedded = next(c for c in cands if c["kind"] == "embedded")
    assert embedded["figure_type"] == "spectra"
    assert "PXRD" in embedded["caption"]
    assert embedded["width"] >= pdf_figures.MIN_SIDE
    render = next(c for c in cands if c["kind"] == "page_render")
    assert render["figure_type"] == "structure"
    assert render["page"] == 2
    assert render["ext"] == ".png" and render["data"][:8] == b"\x89PNG\r\n\x1a\n"


def test_extract_filters_small_and_dedupes():
    small = pdf_figures.extract_candidates(data=_pdf_with_figures(small_image=True))
    assert not [c for c in small if c["kind"] == "embedded"], "过小图应被过滤"
    dup = pdf_figures.extract_candidates(data=_pdf_with_figures(duplicate=True))
    embedded = [c for c in dup if c["kind"] == "embedded"]
    assert len(embedded) == 1, "同一张图重复出现应去重"


def test_extract_bad_pdf_raises():
    with pytest.raises(pdf_figures.FigureExtractError):
        pdf_figures.extract_candidates(data=b"not a pdf at all")


# ---------------------------------------------------------------- 暂存 + 入库

def test_stage_import_and_link_entries(isolate):
    cands = pdf_figures.extract_candidates(data=_pdf_with_figures())
    staged = pdf_figures.stage_candidates("1", cands)
    assert staged and all(s["staged_id"].startswith("fs_") for s in staged)
    assert (pdf_figures.STAGING_DIR / f"{staged[0]['staged_id']}.json").is_file()
    # 预览取文件
    hit = pdf_figures.get_staged(staged[0]["staged_id"])
    assert hit is not None and hit[0].is_file()

    # 造一条引用了 Figure 1 的条目，验证入库后自动关联
    knowledge.add_entries("1", [{
        "kind": "characterization", "group_id": "G1", "technique": "PXRD",
        "metrics": [{"name": "2theta", "value": 2.76, "unit": "deg"}],
        "evidence": "As shown in Figure 1, the PXRD pattern exhibits a peak at 2.76°.",
    }])

    res = pdf_figures.import_staged("1", [s["staged_id"] for s in staged])
    assert len(res["imported"]) == len(staged)
    stored = figures.list_figures(paper_id="1")
    assert len(stored) == len(staged)
    types = {f["figure_type"] for f in stored}
    assert "spectra" in types
    # 暂存文件入库后被清理
    assert not (pdf_figures.STAGING_DIR / f"{staged[0]['staged_id']}.json").is_file()

    # import 端点的关联逻辑（通过 API 再走一遍）
    staged2 = pdf_figures.stage_candidates("1", cands[:1])
    r = client.post("/api/literature/1/figures/import",
                    json={"staged_ids": [staged2[0]["staged_id"]]})
    assert r.status_code == 201
    body = r.json()
    assert body["count"] == 1
    entries = knowledge.list_entries(paper_id="1")
    linked = [e for e in entries if e.get("figure_ids")]
    assert linked, "evidence 提到 Figure 1 的条目应被关联 figure_ids"


def test_import_staged_missing_id():
    res = pdf_figures.import_staged("1", ["fs_000000000000"])
    assert res["imported"] == [] and res["skipped"]


# ---------------------------------------------------------------- API 全链路

def test_parse_api_returns_figures_and_imports(isolate):
    r = client.post(
        "/api/literature/1/parse",
        files=[("files", ("paper.pdf", _pdf_with_figures(), "application/pdf"))],
        data={"with_meta": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    figs = body.get("figures") or []
    assert figs, "parse 响应应带抽取到的候选图"
    assert body["figure_counts"]["total"] == len(figs)
    assert all(f["url"].startswith("/api/literature/figure-staging/") for f in figs)

    sid = figs[0]["staged_id"]
    file_res = client.get(f"/api/literature/figure-staging/{sid}/file")
    assert file_res.status_code == 200
    assert file_res.content[:4] == b"\x89PNG"

    imp = client.post("/api/literature/1/figures/import",
                      json={"staged_ids": [f["staged_id"] for f in figs]})
    assert imp.status_code == 201
    assert imp.json()["count"] == len(figs)
    assert len(figures.list_figures(paper_id="1")) == len(figs)

    # 丢弃端点
    staged2 = pdf_figures.stage_candidates("1", pdf_figures.extract_candidates(
        data=_pdf_with_figures())[:1])
    d = client.delete(f"/api/literature/figure-staging/{staged2[0]['staged_id']}")
    assert d.status_code == 200
    assert client.get(
        f"/api/literature/figure-staging/{staged2[0]['staged_id']}/file"
    ).status_code == 404


def test_parse_api_can_disable_figure_extraction(isolate):
    r = client.post(
        "/api/literature/1/parse",
        files=[("files", ("paper.pdf", _pdf_with_figures(), "application/pdf"))],
        data={"with_meta": "false", "extract_figures": "false"},
    )
    assert r.status_code == 200
    assert (r.json().get("figures") or []) == []


def test_prune_staging_removes_expired(isolate):
    staged = pdf_figures.stage_candidates("1", pdf_figures.extract_candidates(
        data=_pdf_with_figures())[:1])
    sid = staged[0]["staged_id"]
    removed = pdf_figures.prune_staging(ttl_hours=-1)   # 负数 → 全部视为过期
    assert removed >= 1
    assert pdf_figures.get_staged(sid) is None
