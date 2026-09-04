"""联网搜索工具族测试（v1.6.0 P0）：web_search / academic_search / fetch_page
+ 设置页配置读写 + 注册表动态裁剪。全部 mock 网络，不依赖外网。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.llm.client as llm_client  # noqa: E402
from src.assistant.tools import academic, fetch, web  # noqa: E402


@pytest.fixture()
def ws_cfg(tmp_path, monkeypatch):
    """隔离 llm_settings.local.json，并配置 tavily 可用。"""
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    llm_client.save_search_settings(True, "tavily", "tvly-test-key")
    return tmp_path


# ---------------------------------------------------------------- 配置层

def test_search_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    pub = llm_client.save_search_settings(True, "serper", "sk-123")
    assert pub["enabled"] is True
    assert pub["provider"] == "serper"
    assert pub["configured"] is True
    assert "sk-123" not in pub["api_key_masked"]
    # 空 key 保留旧值
    pub2 = llm_client.save_search_settings(True, "serper", "")
    assert llm_client.get_search_settings()["api_key"] == "sk-123"
    # 非法 provider 回退 tavily
    llm_client.save_search_settings(True, "bing", "")
    assert llm_client.get_search_settings()["provider"] == "tavily"
    # 与 LLM 配置同文件互不覆盖
    llm_client.save_settings("https://api.deepseek.com", "sk-llm", "m")
    assert llm_client.get_search_settings()["api_key"] == "sk-123"


def test_web_search_available_matrix(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    ok, _ = llm_client.web_search_available()
    assert ok is False  # 默认关
    llm_client.save_search_settings(True, "tavily", "")
    ok, _ = llm_client.web_search_available()
    assert ok is False  # 开但无 key
    llm_client.save_search_settings(True, "tavily", "k")
    ok, reason = llm_client.web_search_available()
    assert ok is True and reason == "ok"


# ---------------------------------------------------------------- web_search

def test_web_search_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    r = web.web_search("COF")
    assert r["is_error"] is True
    assert "不可用" in r["text"]


class _Resp:
    def __init__(self, payload=None, status=200, headers=None):
        self._payload = payload or {}
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def json(self):
        return self._payload


def test_web_search_tavily(ws_cfg, monkeypatch):
    monkeypatch.setattr(web.requests, "post", lambda *a, **k: _Resp({
        "results": [
            {"title": "COF 气体分离综述", "url": "https://example.com/a",
             "content": "最新 COF 膜进展摘要…"},
            {"title": "COF membranes", "url": "https://example.com/b",
             "content": "another"},
        ]}))
    r = web.web_search("COF gas separation", n=3)
    assert r["is_error"] is False
    assert "2 条" in r["text"] and "example.com/a" in r["text"]
    assert r["details"]["results"][0]["source"] == "tavily"


def test_web_search_serper(ws_cfg, monkeypatch):
    llm_client.save_search_settings(True, "serper", "sp-key")
    monkeypatch.setattr(web.requests, "post", lambda *a, **k: _Resp({
        "organic": [{"title": "T", "link": "https://example.com/t",
                     "snippet": "S"}]}))
    r = web.web_search("COF")
    assert r["is_error"] is False
    assert r["details"]["results"][0]["source"] == "serper"


def test_web_search_http_401(ws_cfg, monkeypatch):
    monkeypatch.setattr(web.requests, "post", lambda *a, **k: _Resp({}, 401))
    r = web.web_search("COF")
    assert r["is_error"] is True
    assert "401" in r["text"] and "key" in r["text"]


def test_web_search_timeout(ws_cfg, monkeypatch):
    import requests
    def _boom(*a, **k):
        raise requests.Timeout()
    monkeypatch.setattr(web.requests, "post", _boom)
    r = web.web_search("COF")
    assert r["is_error"] is True
    assert "超时" in r["text"]


def test_web_search_empty_query():
    r = web.web_search("  ")
    assert r["is_error"] is True


# ---------------------------------------------------------------- academic

_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>COF membranes for gas separation</title>
    <author><name>Zhang San</name></author>
    <author><name>Li Si</name></author>
    <id>https://arxiv.org/abs/2501.12345</id>
    <published>2025-01-15T00:00:00Z</published>
    <summary>We report COF membranes.</summary>
  </entry>
</feed>"""


def test_academic_search_arxiv(monkeypatch):
    monkeypatch.setattr(academic.requests, "get", lambda *a, **k: _Resp(
        _ARXIV_XML, headers={}))
    # _search_arxiv 用 resp.text
    class _TextResp(_Resp):
        @property
        def text(self):
            return self._payload
    monkeypatch.setattr(academic.requests, "get",
                        lambda *a, **k: _TextResp(_ARXIV_XML))
    r = academic.academic_search("COF membrane", source="arxiv")
    assert r["is_error"] is False
    assert "arxiv.org/abs/2501.12345" in r["text"]
    assert r["details"]["papers"][0]["source"] == "arxiv"


def test_academic_search_crossref_ok(monkeypatch):
    monkeypatch.setattr(academic.lit_crossref, "search_by_title",
                        lambda q, rows=3: [{
                            "title": "COF study", "authors": ["A B"],
                            "journal": "JACS", "year": 2024,
                            "doi": "10.1021/jacs.4c00001",
                            "url": "https://doi.org/10.1021/jacs.4c00001",
                            "abstract": None}])
    r = academic.academic_search("COF", source="crossref")
    assert r["is_error"] is False
    assert "10.1021/jacs.4c00001" in r["text"]


def test_academic_search_invalid_source():
    r = academic.academic_search("COF", source="google")
    assert r["is_error"] is True
    assert "未知来源" in r["text"]


def test_academic_source_normalization(monkeypatch):
    """LLM 输出漂移（大写/空格/别名）归一化：不再白拒。"""
    seen = []
    def _get(url, **kw):
        seen.append((url, kw.get("params")))
        return _Resp({"data": []}, headers={})
    monkeypatch.setattr(academic.requests, "get", _get)
    r = academic.academic_search("COF", source="Semantic Scholar")
    assert r["is_error"] is False  # 归一化到 semanticscholar
    r2 = academic.academic_search("COF", source="Google Scholar")
    assert r2["is_error"] is False  # 别名 → semanticscholar
    r3 = academic.academic_search("COF", source="  ArXiv ")
    assert r3["is_error"] is False  # 大小写/空白归一化


def test_academic_search_empty_query():
    r = academic.academic_search(" ")
    assert r["is_error"] is True


# ---------------------------------------------------------------- fetch_page

def test_fetch_page_rejects_private_hosts():
    for bad in ("http://127.0.0.1/x", "http://10.1.2.3/x",
                "http://192.168.1.10/x", "http://localhost:8000/x",
                "file:///C:/x", "ftp://example.com/x"):
        r = fetch.fetch_page(bad)
        assert r["is_error"] is True, bad
        assert "拒绝" in r["text"] or "仅支持" in r["text"], bad


class _HtmlResp:
    headers = {"Content-Type": "text/html"}
    encoding = None

    def __init__(self, html):
        self._html = html
        self.text = html

    def raise_for_status(self):
        pass

    class raw:
        @staticmethod
        def read(n):
            return b"x" * min(n, 10)


def test_fetch_page_fallback_without_trafilatura(monkeypatch):
    monkeypatch.setattr(fetch.requests, "get",
                        lambda *a, **k: _HtmlResp(
                            "<html><head><title>T</title>"
                            "<script>var x=1;</script></head>"
                            "<body><p>正文内容 here。</p></body></html>"))
    monkeypatch.setitem(sys.modules, "trafilatura", None)  # 模拟未安装
    r = fetch.fetch_page("https://example.com/paper")
    assert r["is_error"] is False
    assert "正文内容" in r["text"]
    assert "var x=1" not in r["text"]  # script 已剥离


# ---------------------------------------------------------------- 注册表裁剪

def test_registry_dynamic_web_search_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    from src.assistant import registry
    names = {t["function"]["name"] for t in registry.list_tool_schemas()}
    assert "web_search" not in names          # 默认关 → 缺席
    assert "academic_search" in names
    assert "fetch_page" in names
    llm_client.save_search_settings(True, "tavily", "k")
    names = {t["function"]["name"] for t in registry.list_tool_schemas()}
    assert "web_search" in names              # 配好 key → 出现
