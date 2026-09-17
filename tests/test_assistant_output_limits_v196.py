"""助手工具输出上限（v1.9.6 修复「查看迭代意见时实验记录被截断」）。

背景（用户报）：在科研助手里查看迭代意见/实验记录，卡片里的记录文本被腰斩。根因是
三层上限叠加且过紧、且部分截断**没有提示**：
  1. `registry.summary_of` 600 字 → 前端工具卡片展示的就是这 600 字（主症状）；
  2. `registry.execute` 回填 LLM 的 `_MAX_TEXT` 4000 字；
  3. `tools/graphrag._MAX_TEXT` 3000 字 **静默**截断（迭代意见/证据检索链路）；
  4. `tools/records` 列表工具 流程 1200 / 时间线 4000 / 单段 600；
  5. `context._PROCESS_EXCERPT` 600 字（注入 system prompt 的最近流程摘录）。

本文件锁定放宽后的行为与「截断必有提示」的约定。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from assistant import context as context_mod  # noqa: E402
from assistant import registry  # noqa: E402
from assistant.tools import graphrag, records  # noqa: E402


# ---------------------------------------------------------------- 上限值

def test_limits_are_relaxed():
    assert registry._MAX_SUMMARY == 6000, "前端展示上限应放宽到 6000"
    assert registry._MAX_TEXT == 8000, "回填 LLM 上限应放宽到 8000"
    assert graphrag._MAX_TEXT == 8000, "证据检索工具上限应放宽到 8000"
    assert records._MAX_PROCESS == 2500
    assert records._MAX_SEGMENT == 900
    assert records._MAX_TIMELINE_CHARS == 6000
    assert records._FULL_PROCESS == 12000
    assert context_mod._PROCESS_EXCERPT == 1500


# ---------------------------------------------------------------- summary_of

def test_summary_of_keeps_long_text():
    """600~6000 字的结果应完整展示（原实现在 600 处腰斩）。"""
    text = "实" * 3000
    out = registry.summary_of({"text": text})
    assert out == text
    assert len(out) == 3000


def test_summary_of_marks_truncation_with_counts():
    text = "记" * 9000
    out = registry.summary_of({"text": text})
    assert len(out) < len(text)
    assert out.startswith("记" * 6000)
    assert "结果共 9000 字" in out and "前 6000 字" in out


def test_result_chars_reports_full_length():
    assert registry.result_chars({"text": "x" * 123}) == 123
    assert registry.result_chars({}) == 0


# ---------------------------------------------------------------- execute 截断

def test_execute_truncates_at_new_cap_with_marker(monkeypatch):
    """registry.execute 只在超过 8000 字时截断，并写明原因。"""
    long_text = "A" * 12000

    def fake_handler(args):
        return {"text": long_text, "details": {}}

    monkeypatch.setitem(registry.TOOLS, "fake_long", {
        "schema": {"function": {"name": "fake_long", "description": "test",
                                "parameters": {"properties": {}, "required": []}}},
        "handler": fake_handler,
    })
    res = registry.execute("fake_long", {})
    assert len(res["text"]) < len(long_text)
    assert "已截断" in res["text"]
    assert registry.result_chars(res) == len(res["text"])   # 截断后即真实长度

    # 7000 字（旧上限 4000 会截，新上限不截）
    monkeypatch.setitem(registry.TOOLS, "fake_mid", {
        "schema": {"function": {"name": "fake_mid", "description": "test",
                                "parameters": {"properties": {}, "required": []}}},
        "handler": lambda args: {"text": "B" * 7000, "details": {}},
    })
    res2 = registry.execute("fake_mid", {})
    assert len(res2["text"]) == 7000 and "已截断" not in res2["text"]


# ---------------------------------------------------------------- graphrag

def test_graphrag_truncation_is_announced(monkeypatch):
    """证据检索超过上限时必须注明（原实现静默腰斩）。"""
    monkeypatch.setattr(graphrag, "_bootstrap_bridge_path", lambda: Path("."))
    monkeypatch.setattr(graphrag, "_local_search_block",
                        lambda q: "本地召回：" + "证" * 9000)
    monkeypatch.setattr(graphrag, "_graph_block", lambda q: "")
    res = graphrag.query_graphrag_tool("迭代意见")
    assert res["is_error"] is False
    assert "已截断" in res["text"]
    assert "共" in res["text"]

    # 未超限 → 原样返回、无截断提示
    monkeypatch.setattr(graphrag, "_local_search_block", lambda q: "本地召回：短证据")
    res2 = graphrag.query_graphrag_tool("迭代意见")
    assert res2["text"] == "本地召回：短证据"


# ---------------------------------------------------------------- 实验记录工具

@pytest.fixture()
def fake_records(monkeypatch):
    """两条记录：一条长流程（~3000 字）+ 长描述时间线。

    取数入口是 `records.store.list_records`（工具内惰性 import），
    因此对 records.store 的两个模块实例都打桩。
    """
    long_process = "步骤：" + "反应 12 小时后取样，PXRD 显示结晶性良好。" * 120
    recs = [{
        "record_id": "rec_test_001", "experiment_no": "A6 × TAPT",
        "date": "2026-09-01", "experiment_date": "2026-09-01",
        "date_source": "timeline", "outcome": "film",
        "process_notes": long_process,
        "timeline": [
            {"time_label": "第1天", "description": "投料，" + "溶剂均三甲苯/二氧六环。" * 40},
            {"time_label": "第2天", "description": "升温至 120℃ 保持。" * 40},
            {"time_label": "第3天", "description": "取样表征。"},
        ],
    }]
    patched = []
    for mod_name in ("records.store", "src.records.store"):
        try:
            mod = importlib.import_module(mod_name)   # 惰性导入 → 先加载再打桩
        except Exception:
            continue
        monkeypatch.setattr(mod, "list_records", lambda **kw: list(recs))
        monkeypatch.setattr(mod, "get_record",
                            lambda rid: recs[0] if rid == "rec_test_001" else None)
        patched.append(mod_name)
    assert patched, "未能定位 records.store（测试环境初始化失败）"
    return recs


def test_records_tool_keeps_more_process_text(fake_records):
    """列表工具：3000 字流程不再在 1200 处被切，而是按新上限 2500 摘录并注明。"""
    out = records.read_experiment_records()
    text = out if isinstance(out, str) else str(out.get("text") or "")
    assert "rec_test_001" in text
    assert ("摘录" in text) or ("全文" in text)
    # 新上限 2500 → 命中的流程片段数明显多于旧上限 1200 时的情况
    assert text.count("反应 12 小时后取样") >= 40


def test_single_record_tool_returns_long_timeline(fake_records):
    """单条工具（近全文）应能带回更长的时间点描述。"""
    out = records.read_experiment_record("rec_test_001")
    text = out if isinstance(out, str) else str(out.get("text") or "")
    assert "rec_test_001" in text
    assert "溶剂均三甲苯" in text
    # 单段上限 900（旧 600）：应能看到较长的描述片段
    assert text.count("溶剂均三甲苯") >= 30
