"""文献解析增强（v1.9.3）测试：推理模型 token 预算自适应、元数据提取、
多段分组前缀、文献级字段回填（只补空）、编号从 1781 之后递增、扫描件 422。

背景（实测复现）：文献解析 LLM 配 deepseek 系推理模型时，max_tokens=4000 会被
reasoning 全部吃光（finish_reason=length、content 为空）→ 解析出 0 条并降级，
被误判为「提取失败」。本文件锁定修复后的行为。
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

from literature import llm_extract, resolver  # noqa: E402
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

MINI_LIB = {
    "1780": {"title": "Old A", "doi": "10.1000/a"},
    "1781": {"title": "Old B", "doi": "10.1000/b"},
}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """文献库（含 1781 上限）+ 解析设置路径隔离（双实例都打）。"""
    p = tmp_path / "paper_titles.json"
    p.write_text(json.dumps(MINI_LIB, ensure_ascii=False), encoding="utf-8")
    patched = []
    for mod_name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(mod_name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", p)
            mod.reload()
            patched.append(mod)
    monkeypatch.setattr(llm_extract, "SETTINGS_PATH",
                        tmp_path / "config" / "lit_llm.json")
    monkeypatch.setattr(resolver, "INTAKE_PATH",
                        tmp_path / "literature_intake.jsonl")
    # PATCH /papers 会同步知识图谱文献节点 → 侧车图根必须隔离（防写真实数据）
    for mod_name in ("literature.graph_ingest", "src.literature.graph_ingest"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "_app_root"):
            monkeypatch.setattr(mod, "_app_root", lambda: tmp_path)
    yield tmp_path
    for mod in patched:
        mod.reload()


def _enable_llm(tmp_path: Path) -> None:
    cfg = tmp_path / "config" / "lit_llm.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({
        "enabled": True, "base_url": "https://api.example.com/v1",
        "api_key": "sk-test", "model": "reasoning-model",
    }), encoding="utf-8")


ENTRY_JSON = json.dumps([{
    "group_id": "G1", "kind": "film_outcome", "ald_smiles": "O=Cc1ccccc1",
    "amine_smiles": "Nc1ccccc1", "film_label": 1,
    "evidence": "壁上形成连续薄膜（原文）",
}], ensure_ascii=False)


# ---------------------------------------------------------------- token 预算

def test_chat_ex_expands_budget_when_reasoning_eats_it(monkeypatch):
    """content 为空（预算被 reasoning 吃光）→ 自动翻倍重试并成功。"""
    monkeypatch.setattr(llm_extract, "DEFAULT_MAX_TOKENS", 4000)
    calls: list[int] = []

    def fake_once(base_url, api_key, model, prompt, max_tokens):
        calls.append(max_tokens)
        if max_tokens < 16000:
            # 复现实测：reasoning 吃满、content 为空、finish_reason=length
            return {"content": "", "reasoning_tokens": max_tokens,
                    "finish_reason": "length", "error": None}
        return {"content": ENTRY_JSON, "reasoning_tokens": 9000,
                "finish_reason": "stop", "error": None}

    monkeypatch.setattr(llm_extract, "_chat_once", fake_once)
    content, info = llm_extract._chat_ex("u", "k", "m", "p", 4000)
    assert content == ENTRY_JSON
    assert calls == [4000, 8000, 16000]
    assert info["expanded"] is True
    assert info["max_tokens"] == 16000


def test_chat_ex_does_not_retry_on_transport_error(monkeypatch):
    calls: list[int] = []

    def fake_once(base_url, api_key, model, prompt, max_tokens):
        calls.append(max_tokens)
        return {"content": "", "reasoning_tokens": 0, "finish_reason": "",
                "error": "URLError: timed out"}

    monkeypatch.setattr(llm_extract, "_chat_once", fake_once)
    content, info = llm_extract._chat_ex("u", "k", "m", "p", 4000)
    assert content is None
    assert calls == [4000]
    assert "timed out" in info["error"]


def test_chat_ex_stops_at_ceiling(monkeypatch):
    calls: list[int] = []

    def fake_once(base_url, api_key, model, prompt, max_tokens):
        calls.append(max_tokens)
        return {"content": "", "reasoning_tokens": max_tokens,
                "finish_reason": "length", "error": None}

    monkeypatch.setattr(llm_extract, "_chat_once", fake_once)
    content, _info = llm_extract._chat_ex("u", "k", "m", "p", 16000)
    assert content is None
    assert calls == [16000, 32000]  # 天花板后停止


# ---------------------------------------------------------------- 解析主体

def test_parse_text_reports_segments_and_expansion(isolated, monkeypatch):
    _enable_llm(isolated)
    monkeypatch.setattr(llm_extract, "_chat_ex",
                        lambda *a, **kw: (ENTRY_JSON,
                                          {"max_tokens": 16000,
                                           "expanded": True}))
    out = llm_extract.parse_text("COF 文献正文" * 50)
    assert out["llm_used"] is True
    assert len(out["entries"]) == 1
    assert out["segments"] == {"total": 1, "failed": 0}
    assert "共 1 段" in out["note"]
    assert "token 预算已自动扩容" in out["note"]


def test_parse_text_prefixes_synthetic_group_ids_across_chunks(isolated, monkeypatch):
    """多段时自造 E1/E2… 加段前缀防撞号；文中真实编号 G1 保持原样。"""
    _enable_llm(isolated)
    monkeypatch.setattr(llm_extract, "MAX_TEXT_CHARS", 10)
    payload = json.dumps([
        {"group_id": "E1", "kind": "film_outcome", "evidence": "第一段依据"},
        {"group_id": "G2", "kind": "condition", "evidence": "第一段条件"},
    ], ensure_ascii=False)
    monkeypatch.setattr(llm_extract, "_chat_ex",
                        lambda *a, **kw: (payload, {"max_tokens": 16000}))
    out = llm_extract.parse_text("x" * 25)  # → 3 段
    assert out["segments"]["total"] == 3
    gids = sorted(str(e["group_id"]) for e in out["entries"])
    assert gids == ["C1-E1", "C2-E1", "C3-E1", "G2"]
    assert all("chunk_index" in e for e in out["entries"])


def test_parse_text_falls_back_with_reason_when_all_chunks_fail(isolated, monkeypatch):
    _enable_llm(isolated)
    monkeypatch.setattr(llm_extract, "_chat_ex",
                        lambda *a, **kw: (None, {"error": "URLError: x"}))
    out = llm_extract.parse_text("O=Cc1ccccc1 与 Nc1ccccc1 反应")
    assert out["llm_used"] is True
    assert out["segments"]["failed"] == 1
    assert "LLM 调用失败或返回为空" in out["note"]


def test_parse_text_llm_disabled_uses_regex_fallback(tmp_path):
    out = llm_extract.parse_text("O=Cc1ccc(C=O)cc1 与 Nc1ccc(N)cc1")
    assert out["llm_used"] is False
    assert "未启用" in out["note"]


# ---------------------------------------------------------------- 文献级元数据

def test_extract_paper_meta_normalizes_fields(isolated, monkeypatch):
    _enable_llm(isolated)
    reply = json.dumps({
        "title": "Covalent Organic Framework Membranes",
        "authors": ["Alice", " Bob ", ""],
        "journal": "JACS", "year": "2025",
        "doi": "https://doi.org/10.1021/jacs.5b00001",
        "abstract": "We report ...",
    }, ensure_ascii=False)
    monkeypatch.setattr(llm_extract, "_chat_ex",
                        lambda *a, **kw: (reply, {"max_tokens": 16000}))
    out = llm_extract.extract_paper_meta("标题片段")
    assert out["meta"]["title"].startswith("Covalent")
    assert out["meta"]["authors"] == ["Alice", "Bob"]
    assert out["meta"]["year"] == 2025
    assert out["meta"]["doi"] == "10.1021/jacs.5b00001"
    assert out["llm_used"] is True


def test_extract_paper_meta_ignores_bad_year_and_missing_llm(isolated, monkeypatch):
    _enable_llm(isolated)
    reply = json.dumps({"title": "T", "year": "公元前", "doi": ""})
    monkeypatch.setattr(llm_extract, "_chat_ex",
                        lambda *a, **kw: (reply, {}))
    out = llm_extract.extract_paper_meta("片段")
    assert out["llm_used"] is True
    assert "year" not in out["meta"]
    assert "doi" not in out["meta"]
    # 未启用 LLM：不调用、不报错
    (isolated / "config" / "lit_llm.json").write_text("{}", encoding="utf-8")
    out2 = llm_extract.extract_paper_meta("片段")
    assert out2["meta"] == {} and out2["llm_used"] is False


# ---------------------------------------------------------------- 编号

def test_next_paper_id_continues_after_1781(isolated):
    """用户要求：已有 1781 篇之后继续，新文献从 1782 开始自动递增。"""
    assert resolver.next_paper_id() == "1782"
    pid = resolver.append_paper({"title": "新文献一", "doi": "10.1/new1"})
    assert pid == "1782"
    assert resolver.next_paper_id() == "1783"
    pid2 = resolver.append_paper({"title": "新文献二", "doi": "10.1/new2"})
    assert pid2 == "1783"
    assert titles.resolve_title("1782") == "新文献一"


# ---------------------------------------------------------------- 字段回填

def test_update_paper_fields_only_empty_keeps_existing(isolated):
    res = resolver.update_paper_fields("1780", {
        "title": "LLM 猜的标题", "journal": "JACS",
        "year": 2025, "abstract": "摘要",
    })
    assert res["missing"] is False
    assert "title" in res["skipped"]          # 已有标题，保留
    assert set(res["updated"]) == {"journal", "year", "abstract"}
    entry = titles.resolve_entry("1780")
    assert entry["title"] == "Old A"
    assert entry["journal"] == "JACS"


def test_update_paper_fields_doi_conflict_is_skipped(isolated):
    res = resolver.update_paper_fields("1780", {"doi": "10.1000/b"})
    assert "doi" in res["skipped"]
    assert "doi" not in res["updated"]
    assert titles.resolve_entry("1780")["doi"] == "10.1000/a"


def test_update_paper_fields_missing_paper(isolated):
    res = resolver.update_paper_fields("999999", {"journal": "X"})
    assert res["missing"] is True and res["entry"] is None


def test_patch_paper_endpoint_backfills_only_empty(isolated):
    r = client.patch("/api/literature/papers/1780", json={
        "journal": "Nature", "year": 2024, "title": "不该覆盖",
    })
    assert r.status_code == 200
    body = r.json()
    assert "journal" in body["updated"] and "title" in body["skipped"]
    assert titles.resolve_entry("1780")["title"] == "Old A"
    assert titles.resolve_entry("1780")["journal"] == "Nature"
    assert client.patch("/api/literature/papers/999999",
                        json={"journal": "X"}).status_code == 404


# ---------------------------------------------------------------- 解析端点

def test_parse_endpoint_rejects_scanned_pdf(isolated):
    import fitz
    doc = fitz.open()
    doc.new_page()          # 空白页：无文本层
    data = doc.tobytes()
    doc.close()
    r = client.post("/api/literature/1780/parse",
                    files={"file": ("scan.pdf", data, "application/pdf")})
    assert r.status_code == 422
    assert "文本层" in r.json()["detail"]


def test_parse_endpoint_rejects_oversize_pdf(isolated, monkeypatch):
    from api.routers import literature as lit_router
    monkeypatch.setattr(lit_router, "MAX_PARSE_PDF_BYTES", 10)
    r = client.post("/api/literature/1780/parse",
                    files={"file": ("big.pdf", b"x" * 100, "application/pdf")})
    assert r.status_code == 413


def test_parse_endpoint_text_mode_without_llm(isolated):
    r = client.post("/api/literature/1780/parse",
                    data={"text": "O=Cc1ccc(C=O)cc1 与 Nc1ccc(N)cc1 反应",
                          "with_meta": "false"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_used"] is False
    assert body["paper_id"] == "1780"
    assert body["chars"] > 0


def test_parse_endpoint_missing_paper(isolated):
    r = client.post("/api/literature/999999/parse", data={"text": "x"})
    assert r.status_code == 404
