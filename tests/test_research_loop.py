"""深度研究循环测试（v1.6.0 P1）：plan/execute/critic/report/落盘/docx。

LLM 全部打桩（队列式 chat_text），工具 execute 打桩，不依赖外网。
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

from src.assistant import llm_bridge, research  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "REPORTS_DIR", tmp_path / "research")
    monkeypatch.setattr(llm_bridge, "is_configured", lambda: True)
    return tmp_path


class _Queue:
    """按顺序吐 LLM 回复的桩；耗尽返回 None。"""

    def __init__(self, replies):
        self._replies = list(replies)

    def __call__(self, messages, max_tokens=None):
        if not self._replies:
            return None
        return self._replies.pop(0)


# ---------------------------------------------------------------- plan

def test_plan_steps_parses_json(monkeypatch):
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue([json.dumps({
        "summary": "调研 COF 膜进展",
        "steps": [
            {"title": "文献进展", "query": "COF membrane 2025",
             "note": "近两年文献"},
            {"title": "方法对比", "query": "synthesis methods",
             "note": "方法学"},
        ]})]))
    plan = research.plan_steps("COF 膜最新进展")
    assert plan["summary"] == "调研 COF 膜进展"
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["query"] == "COF membrane 2025"


def test_plan_steps_garbage_falls_back(monkeypatch):
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue(["这不是 JSON"]))
    plan = research.plan_steps("问题")
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["title"] == "整体调研"


def test_plan_steps_llm_fail_falls_back(monkeypatch):
    def _none(*a, **k):
        return None
    monkeypatch.setattr(llm_bridge, "chat_text", _none)
    plan = research.plan_steps("问题")
    assert len(plan["steps"]) == 1


# ---------------------------------------------------------------- execute

def test_execute_step_tool_then_done(monkeypatch):
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue([
        json.dumps({"tool": "academic_search",
                    "args": {"query": "COF"}}),
        json.dumps({"done": "找到 3 篇相关文献"}),
    ]))
    def _exec(name, args):
        assert name == "academic_search"
        return {"text": "命中", "details": {}, "is_error": False}
    monkeypatch.setattr(research.registry, "execute", _exec)
    events = list(research._execute_step(
        0, {"title": "文献", "query": "COF", "note": ""},
        {"academic_search"}))
    types = [e["type"] for e in events]
    assert types == ["step_start", "tool_call", "tool_result",
                     "_result", "step_done"]
    assert events[-1]["summary"] == "找到 3 篇相关文献"


def test_execute_step_unknown_tool_rejected(monkeypatch):
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue([
        json.dumps({"tool": "generate_plan_card", "args": {}}),
        json.dumps({"done": "ok"}),
    ]))
    events = list(research._execute_step(
        0, {"title": "x", "query": "x", "note": ""}, {"academic_search"}))
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["is_error"] is True
    assert "不允许" in tr["summary"]


# ---------------------------------------------------------------- 报告落盘

def test_report_crud(isolate_reports):
    rid = "rpt_" + "a" * 12
    research.save_report(rid, "问题", "标题", "# 标题\n正文",
                         [{"title": "T", "doi": "10.1021/x", "url": "",
                           "source": "crossref"}])
    assert research.load_report(rid)["markdown"] == "# 标题\n正文"
    lst = research.list_reports()
    assert len(lst) == 1 and lst[0]["ref_count"] == 1
    assert research.delete_report(rid) is True
    assert research.load_report(rid) is None
    # 非法 id / 不存在
    assert research.load_report("../../etc") is None
    assert research.delete_report("rpt_" + "b" * 12) is False


def test_report_to_docx(isolate_reports):
    rid = "rpt_" + "c" * 12
    research.save_report(rid, "问题", "标题",
                         "# 标题\n## 小节\n正文段落\n- 要点\n1. 编号项",
                         [{"title": "Paper", "doi": "10.1021/x", "url": "",
                           "source": "crossref"}])
    blob = research.report_to_docx(research.load_report(rid))
    assert blob[:4] == b"PK\x03\x04"  # docx 是 zip
    assert len(blob) > 5000


# ---------------------------------------------------------------- 主循环

def test_run_research_full_flow(monkeypatch, isolate_reports):
    replies = _Queue([
        json.dumps({"summary": "s", "steps": [
            {"title": "文献进展", "query": "COF membrane",
             "note": "近两年"}]}),
        json.dumps({"tool": "academic_search",
                    "args": {"query": "COF membrane"}}),
        json.dumps({"done": "找到关键文献"}),
        json.dumps({"ok": True, "gaps": []}),
        "# COF 膜研究进展\n\n## 文献进展\n基于 DOI: 10.1021/jacs.1c00001 的研究…\n\n"
        "## 参考文献\n[1] Paper. JACS, 2021. DOI: 10.1021/jacs.1c00001"
        "（https://doi.org/10.1021/jacs.1c00001）",
    ])
    monkeypatch.setattr(llm_bridge, "chat_text", replies)

    def _exec(name, args):
        if name == "academic_search":
            return {"text": "命中 1 篇：Paper DOI: 10.1021/jacs.1c00001",
                    "details": {"papers": [
                        {"title": "Paper", "doi": "10.1021/jacs.1c00001",
                         "url": "https://doi.org/10.1021/jacs.1c00001",
                         "source": "crossref"}]},
                    "is_error": False}
        return {"text": "?", "details": {}, "is_error": True}
    monkeypatch.setattr(research.registry, "execute", _exec)

    events = list(research.run_research("COF 膜最新进展", allow_web=False))
    types = [e["type"] for e in events]
    assert types[0] == "plan"
    assert "step_start" in types and "step_done" in types
    assert "tool_call" in types and "tool_result" in types
    assert "token" in types and "report" in types and types[-1] == "done"
    rep_ev = next(e for e in events if e["type"] == "report")
    rep = research.load_report(rep_ev["report_id"])
    assert rep["markdown"].startswith("# COF 膜研究进展")
    assert rep["refs"][0]["doi"] == "10.1021/jacs.1c00001"


def test_run_research_llm_unconfigured(monkeypatch, isolate_reports):
    monkeypatch.setattr(llm_bridge, "is_configured", lambda: False)
    events = list(research.run_research("问题"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "未配置" in events[0]["message"]


def test_run_research_empty_question(isolate_reports):
    events = list(research.run_research("  "))
    assert events[0]["type"] == "error"


def test_run_research_critic_gap_backfills(monkeypatch, isolate_reports):
    """批判轮报缺口 → 补搜一次（无 web 时走 academic_search）。"""
    replies = _Queue([
        json.dumps({"summary": "s", "steps": [
            {"title": "A", "query": "q", "note": ""}]}),
        json.dumps({"done": "初步小结"}),
        json.dumps({"ok": False, "gaps": [
            {"step_index": 1, "query": "COF 2025 review"}]}),
        "# 报告\n正文无引用",
    ])
    monkeypatch.setattr(llm_bridge, "chat_text", replies)
    calls = []

    def _exec(name, args):
        calls.append((name, args))
        return {"text": f"{name} 返回（query={args.get('query')}）",
                "details": {"papers": []}, "is_error": False}
    monkeypatch.setattr(research.registry, "execute", _exec)

    events = list(research.run_research("问题", allow_web=False))
    assert any(e["type"] == "critic_note" for e in events)
    assert ("academic_search", {"query": "COF 2025 review"}) in calls
    assert events[-1]["type"] == "done"
