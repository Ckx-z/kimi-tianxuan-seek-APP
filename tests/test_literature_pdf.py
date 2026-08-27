"""文献 PDF 录入（extract-pdf）测试：fitz 文本提取 / LLM mock 成功草稿 /
无文本层 422 / LLM 未配置 503 / 解析失败 502 / 大小与类型校验。

所有写操作打到 tmp_path（mini_lib 隔离同 test_literature.py 口径）；
LLM 一律 monkeypatch pdf_extract 模块级 wrapper（_llm_configured/_llm_chat），
不依赖真实网络与真实 LLM 配置。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from literature import pdf_extract, resolver  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

MINI_LIB = {
    "1": {"doi": "10.1021/abc", "title": "Paper One"},
}


@pytest.fixture()
def mini_lib(tmp_path, monkeypatch):
    """迷你文献库（existing 标记链路会读文献库；防御双实例同 test_literature）。"""
    p = tmp_path / "paper_titles.json"
    p.write_text(json.dumps(MINI_LIB, ensure_ascii=False), encoding="utf-8")
    patched = []
    for mod_name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(mod_name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", p)
            mod.reload()
            patched.append(mod)
    yield p
    for mod in patched:
        mod.reload()


def _make_pdf(text: str = "") -> bytes:
    """用 PyMuPDF 生成一个简单 PDF；text 为空则生成无文本层的空白页。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


FAKE_LLM_JSON = json.dumps({
    "title": "Covalent Organic Framework Films from PDF",
    "authors": ["Alice Wang", "Bob Li"],
    "journal": "Langmuir",
    "year": 2023,
    "doi": "10.1021/acs.langmuir.3c02095",
    "abstract": "We report a COF film ...",
}, ensure_ascii=False)


def _post_pdf(pdf: bytes, filename: str = "paper.pdf"):
    return client.post(
        "/api/literature/extract-pdf",
        files={"file": (filename, pdf, "application/pdf")},
    )


# ---------------------------------------------------------------- 纯函数

class TestExtractText:
    def test_extract_text_with_text_layer(self):
        pdf = _make_pdf("Hello COF world, this is a text layer.")
        text = pdf_extract.extract_text(pdf)
        assert "Hello COF world" in text

    def test_extract_text_invalid_pdf(self):
        with pytest.raises(pdf_extract.PdfExtractError):
            pdf_extract.extract_text(b"not a pdf at all")


class TestParseLlmJson:
    def test_parse_plain_json(self):
        d = pdf_extract.parse_llm_json(FAKE_LLM_JSON)
        assert d["title"] == "Covalent Organic Framework Films from PDF"
        assert d["authors"] == ["Alice Wang", "Bob Li"]
        assert d["journal"] == "Langmuir" and d["year"] == 2023
        assert d["doi"] == "10.1021/acs.langmuir.3c02095"
        assert d["url"] == "https://doi.org/10.1021/acs.langmuir.3c02095"
        assert d["abstract"] == "We report a COF film ..."

    def test_parse_fenced_json(self):
        d = pdf_extract.parse_llm_json(f"```json\n{FAKE_LLM_JSON}\n```")
        assert d["doi"] == "10.1021/acs.langmuir.3c02095"

    def test_parse_surrounding_prose(self):
        d = pdf_extract.parse_llm_json(f"这是提取结果：\n{FAKE_LLM_JSON}\n以上。")
        assert d["journal"] == "Langmuir"

    def test_parse_doi_prefix_stripped(self):
        payload = json.loads(FAKE_LLM_JSON)
        payload["doi"] = "https://doi.org/10.1021/acs.langmuir.3c02095"
        d = pdf_extract.parse_llm_json(json.dumps(payload))
        assert d["doi"] == "10.1021/acs.langmuir.3c02095"

    def test_parse_missing_title_raises(self):
        with pytest.raises(pdf_extract.LLMExtractError):
            pdf_extract.parse_llm_json('{"authors": ["A"]}')
        with pytest.raises(pdf_extract.LLMExtractError):
            pdf_extract.parse_llm_json("根本不是 JSON")

    def test_parse_authors_string_fallback(self):
        payload = json.loads(FAKE_LLM_JSON)
        payload["authors"] = "Alice Wang; Bob Li"
        d = pdf_extract.parse_llm_json(json.dumps(payload))
        assert d["authors"] == ["Alice Wang", "Bob Li"]


# ---------------------------------------------------------------- 端点

class TestExtractPdfApi:
    def test_success_draft_shape(self, mini_lib, monkeypatch):
        monkeypatch.setattr(pdf_extract, "_llm_configured", lambda: True)
        monkeypatch.setattr(pdf_extract, "_llm_chat", lambda messages: FAKE_LLM_JSON)
        r = _post_pdf(_make_pdf("x " * 100))  # 足够长的文本层
        assert r.status_code == 200
        d = r.json()["draft"]
        assert d["title"] == "Covalent Organic Framework Films from PDF"
        assert d["authors"] == ["Alice Wang", "Bob Li"]
        assert d["source"] == "pdf-llm"
        assert d["pdf_filename"] == "paper.pdf"
        assert d["existing"] is False

    def test_existing_doi_marked(self, mini_lib, monkeypatch):
        monkeypatch.setattr(pdf_extract, "_llm_configured", lambda: True)
        payload = json.loads(FAKE_LLM_JSON)
        payload["doi"] = "10.1021/abc"  # 迷你库已有
        monkeypatch.setattr(pdf_extract, "_llm_chat",
                            lambda messages: json.dumps(payload))
        r = _post_pdf(_make_pdf("x " * 100))
        d = r.json()["draft"]
        assert d["existing"] is True
        assert d["existing_paper_id"] == "1"

    def test_no_text_layer_422(self, mini_lib, monkeypatch):
        monkeypatch.setattr(pdf_extract, "_llm_configured", lambda: True)
        r = _post_pdf(_make_pdf(""))  # 空白页 = 无文本层
        assert r.status_code == 422
        assert "无文本层" in r.json()["detail"]

    def test_llm_not_configured_503(self, mini_lib, monkeypatch):
        monkeypatch.setattr(pdf_extract, "_llm_configured", lambda: False)
        r = _post_pdf(_make_pdf("x " * 100))
        assert r.status_code == 503
        assert "请先在设置页配置 LLM" in r.json()["detail"]

    def test_llm_call_failure_502(self, mini_lib, monkeypatch):
        monkeypatch.setattr(pdf_extract, "_llm_configured", lambda: True)
        monkeypatch.setattr(pdf_extract, "_llm_chat", lambda messages: None)
        r = _post_pdf(_make_pdf("x " * 100))
        assert r.status_code == 502

    def test_llm_invalid_json_502(self, mini_lib, monkeypatch):
        monkeypatch.setattr(pdf_extract, "_llm_configured", lambda: True)
        monkeypatch.setattr(pdf_extract, "_llm_chat",
                            lambda messages: "无法提取，这不是 JSON")
        r = _post_pdf(_make_pdf("x " * 100))
        assert r.status_code == 502
        assert "无法解析" in r.json()["detail"] or "JSON" in r.json()["detail"]

    def test_oversize_413(self, mini_lib, monkeypatch):
        monkeypatch.setattr(pdf_extract, "MAX_PDF_BYTES", 10)
        r = _post_pdf(b"%PDF-fake" + b"0" * 100)
        assert r.status_code == 413

    def test_non_pdf_filename_400(self, mini_lib):
        r = _post_pdf(b"whatever", filename="notes.txt")
        assert r.status_code == 400

    def test_invalid_pdf_400(self, mini_lib):
        r = _post_pdf(b"definitely not a pdf content")
        assert r.status_code == 400

    def test_empty_file_400(self, mini_lib):
        r = _post_pdf(b"")
        assert r.status_code == 400
