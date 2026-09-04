"""引用核验器测试（v1.6.0 P0）：collect_refs / check_answer / loop 集成。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.assistant import loop, verify  # noqa: E402


# ---------------------------------------------------------------- collect_refs

def test_collect_refs_scans_text_and_details():
    results = [
        {"text": "文献 CAS 443922-06-3 与 341-58-2；DOI: 10.1021/jacs.1c00001；"
                 "链接 https://arxiv.org/abs/2501.1",
         "details": {"papers": [
             {"doi": "10.1039/d0cs00001a",
              "url": "https://example.com/p"}]}},
    ]
    refs = verify.collect_refs(results)
    assert "443922-06-3" in refs["cas"]
    assert "10.1021/jacs.1c00001" in refs["doi"]
    assert "10.1039/d0cs00001a" in refs["doi"]
    assert "https://arxiv.org/abs/2501.1" in refs["url"]
    assert "https://example.com/p" in refs["url"]


# ---------------------------------------------------------------- check_answer

def _refs(cas=(), doi=(), url=()):
    return {"cas": set(cas), "doi": set(doi), "url": set(url)}


def test_check_answer_clean():
    refs = _refs(cas={"443922-06-3"}, doi={"10.1021/jacs.1c00001"},
                 url={"https://arxiv.org/abs/2501.1"})
    ans = ("该体系（CAS 443922-06-3）在文献 DOI: 10.1021/jacs.1c00001 "
           "与 https://arxiv.org/abs/2501.1 中均有报道。")
    assert verify.check_answer(ans, refs) == []


def test_check_answer_unverified_cas_and_doi():
    refs = _refs(cas={"443922-06-3"})
    ans = "文献 10.1021/jacs.9c99999 报道 CAS 341-58-2 与 443922-06-3 的组合。"
    v = verify.check_answer(ans, refs)
    kinds = {x["kind"]: x["value"] for x in v}
    assert kinds.get("cas") == "341-58-2"        # 不在 refs → 违规
    assert kinds.get("doi") == "10.1021/jacs.9c99999"
    assert "443922-06-3" not in kinds.values()   # 在 refs → 不违规


def test_check_answer_doi_prefix_normalization():
    refs = _refs(doi={"10.1021/jacs.1c00001"})
    ans = "见 https://doi.org/10.1021/jacs.1c00001（带前缀应能命中）。"
    assert verify.check_answer(ans, refs) == []
    ans2 = "见 DOI: 10.1021/JACS.1C00001（大小写不敏感）。"
    assert verify.check_answer(ans2, refs) == []


def test_check_answer_url_trailing_punct():
    refs = _refs(url={"https://example.com/p"})
    assert verify.check_answer("来源 https://example.com/p。", refs) == []
    v = verify.check_answer("来源 https://evil.com/x。", refs)
    assert v and v[0]["kind"] == "url"


def test_check_answer_no_refs_everything_suspicious():
    ans = "推荐 CAS 123-45-6，详见 DOI: 10.1021/abc.1c00001。"
    v = verify.check_answer(ans, _refs())
    assert len(v) == 2


def test_describe_violations():
    v = [{"kind": "cas", "value": "123-45-6"},
         {"kind": "doi", "value": "10.1021/x"}]
    desc = verify.describe_violations(v)
    assert "123-45-6" in desc and "10.1021/x" in desc


# ---------------------------------------------------------------- loop 集成

def _events(gen):
    return list(gen)


def test_emit_verified_passes_clean_answer():
    events = _events(loop._emit_verified(
        [{"role": "user", "content": "q"}], "没问题", []))
    assert [e["type"] for e in events] == ["token"]


def test_emit_verified_rewrites_on_violation(monkeypatch):
    """违规 → critic_note + 打回重写一轮（重写干净 → 流式重写文本）。"""
    calls: list[list] = []
    monkeypatch.setattr(
        loop.llm_bridge, "chat_text",
        lambda msgs: calls.append(msgs) or "已删除可疑引用后的回答")
    refs = verify.collect_refs([{"text": "文献 CAS 443922-06-3",
                                 "details": {}}])
    # 构造一个带违规引用的回答，并伪造工作消息
    work = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "见 CAS 999-99-9。"}]
    events = _events(loop._emit_verified(work, "见 CAS 999-99-9。", [
        {"text": "文献 CAS 443922-06-3", "details": {}}]))
    types = [e["type"] for e in events]
    assert "critic_note" in types
    # 重写后的文本被流式输出
    token_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "已删除可疑引用" in token_text
    # 打回提示里包含违规描述
    assert len(calls) == 1
    assert "999-99-9" in calls[0][-1]["content"]


def test_emit_verified_rewrite_still_bad_warns(monkeypatch):
    """重写仍含违规 → 输出重写文本 + 显式警示。"""
    monkeypatch.setattr(loop.llm_bridge, "chat_text",
                        lambda msgs: "还是引用 CAS 999-99-9。")
    events = _events(loop._emit_verified(
        [{"role": "user", "content": "q"}], "见 CAS 999-99-9。",
        [{"text": "文献 CAS 443922-06-3", "details": {}}]))
    token_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "谨慎采信" in token_text


def test_emit_verified_rewrite_llm_fail_warns(monkeypatch):
    """LLM 重写失败 → 原回答 + 警示（不丢内容）。"""
    monkeypatch.setattr(loop.llm_bridge, "chat_text", lambda msgs: None)
    events = _events(loop._emit_verified(
        [{"role": "user", "content": "q"}], "见 CAS 999-99-9。",
        [{"text": "文献 CAS 443922-06-3", "details": {}}]))
    token_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "见 CAS 999-99-9" in token_text
    assert "谨慎采信" in token_text
