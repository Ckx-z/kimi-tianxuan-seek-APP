"""文献阅读 LLM（v1.9.8 R2）测试：格式保护 / 术语校正 / 批量翻译与缓存 / 文献问答。

不打真实 LLM：`chat` 注入打桩；配置用 monkeypatch 提供。
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

from literature import library_db, reading_llm  # noqa: E402

PAPERS = {
    "1": {"title": "Fluorinated COF for SPME", "abstract": "A fluorinated COF was grown on fiber.",
          "doi": "10.1021/jacs.1c00001", "journal": "JACS", "year": 2026, "source": "builtin"},
}
ENTRIES = [{"entry_id": "e1", "paper_id": "1", "kind": "characterization",
            "technique": "XRD", "evidence": "PXRD peak at 2.76 degree"}]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, res = tmp_path / "data", tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
    (data / "literature" / "knowledge_entries.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in ENTRIES), encoding="utf-8")
    patched = []
    for name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(name)
    assert patched
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    monkeypatch.setattr(reading_llm, "_config",
                        lambda: ("https://api.deepseek.com/v1", "sk-test", "deepseek-chat"))
    library_db.reset_ready_cache()
    yield {"data": data}
    library_db.reset_ready_cache()


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    yield c
    c.close()


# ---------------------------------------------------------------- 格式保护

def test_protect_and_restore_doi_numbers():
    text = "See 10.1021/jacs.1c00001; peak at 2.76° and BET 2105 m2/g for C3N4."
    protected, mapping = reading_llm.protect(text)
    assert "10.1021/jacs.1c00001" not in protected      # DOI 被占位
    assert "2.76°" not in protected                      # 数字+单位被占位
    assert "⟦" in protected and len(mapping) >= 3
    assert reading_llm.restore(protected, mapping) == text   # 还原无损


def test_apply_glossary_expands_abbreviations_once():
    out = reading_llm.apply_glossary("XRD and BET were used, COFs are porous.")
    assert "XRD（X 射线衍射）" in out and "BET（比表面积）" in out
    assert "COFs（共价有机框架）" in out
    # 已经是带注释的形式 → 不重复追加
    again = reading_llm.apply_glossary(out)
    assert again.count("XRD（X 射线衍射）") == 1


# ---------------------------------------------------------------- 翻译

def test_translate_segments_batch_and_cache(conn):
    calls: list[list[dict]] = []

    def fake_chat(messages, **kw):
        calls.append(messages)
        payload = json.loads(messages[0]["content"].split("\n", 1)[1])
        # 模拟模型翻译：仅把占位符原样保留 + 加前缀
        return json.dumps([{"i": it["i"], "t": "【译】" + it["text"]} for it in payload],
                          ensure_ascii=False)

    segs = ["First paragraph about COF.", "Second paragraph with 10.1021/jacs.1c00001."]
    out = reading_llm.translate_segments(conn, segs, chat=fake_chat)
    assert out["translated"] == 2 and out["cached"] == 0
    assert out["translations"][0].startswith("【译】")
    assert "COF（共价有机框架）" in out["translations"][0]         # 术语校正生效
    assert "10.1021/jacs.1c00001" in out["translations"][1]        # DOI 还原
    assert len(calls) == 1                                          # 批量一次调用

    # 第二次：命中缓存，不再调用模型
    calls.clear()
    out2 = reading_llm.translate_segments(conn, segs, chat=fake_chat)
    assert out2["cached"] == 2 and calls == []
    assert out2["translations"] == out["translations"]


def test_translate_handles_empty_and_failure(conn):
    def bad_chat(messages, **kw):
        raise RuntimeError("boom")

    out = reading_llm.translate_segments(conn, ["", "hello"], chat=bad_chat)
    assert out["translations"][0] == ""            # 空段直接返回空
    assert out["translations"][1] == ""            # 失败不抛，保留空串
    assert out["translated"] == 0


def test_translate_requires_config(conn, monkeypatch):
    monkeypatch.setattr(reading_llm, "_config", lambda: (_ for _ in ()).throw(
        RuntimeError("未配置可用的 LLM（设置页填写 API Key / 模型后重试）")))
    with pytest.raises(RuntimeError):
        reading_llm.translate_segments(conn, ["hi"])


# ---------------------------------------------------------------- 问答

def test_paper_context_includes_abstract_selection_entries(conn):
    ctx = reading_llm.paper_context(conn, "1", selection="selected sentence here")
    assert "【文献】" in ctx and "Fluorinated COF" in ctx
    assert "【摘要】" in ctx
    assert "【用户选中的原文】selected sentence here" in ctx
    assert "【已提取的结构化条目】" in ctx and "PXRD peak" in ctx


def test_ask_uses_context_and_reports(conn):
    seen: list[str] = []

    def fake_chat(messages, **kw):
        seen.append(messages[1]["content"])       # 第二条是文献材料
        return "据摘要，该 COF 用于 SPME [摘要]。"

    out = reading_llm.ask(conn, "1", "这篇文献做了什么？", selection="a fiber was coated",
                          chat=fake_chat)
    assert out["answer"].startswith("据摘要")
    assert out["used_selection"] is True
    assert "a fiber was coated" in seen[0] and "【文献】" in seen[0]
    assert out["context_chars"] > 50


def test_ask_rejects_empty_and_missing(conn):
    with pytest.raises(ValueError):
        reading_llm.ask(conn, "1", "   ", chat=lambda m, **k: "x")
    out = reading_llm.ask(conn, "9999", "问题", chat=lambda m, **k: "x")
    assert out.get("error")


def test_ask_reports_llm_failure(conn):
    def bad(messages, **kw):
        raise TimeoutError("timeout")

    out = reading_llm.ask(conn, "1", "问题", chat=bad)
    assert "调用失败" in out["error"] and "TimeoutError" in out["error"]
