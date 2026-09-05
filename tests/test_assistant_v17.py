"""科研助手 v1.7.0（需求二）测试：话题删除 + 一对话一报告（生成/增量更新）。

LLM 全部打桩；会话目录/报告目录隔离到 tmp_path，不碰真实数据。
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

import src.llm.client as llm_client  # noqa: E402
from src.assistant import llm_bridge, research, sessions  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR",
                        tmp_path / "assistant" / "sessions")
    monkeypatch.setattr(research, "REPORTS_DIR", tmp_path / "research")
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    monkeypatch.setattr(llm_client, "MINIMAX_SECRETS",
                        tmp_path / "secrets.local.json")
    monkeypatch.setattr(llm_client, "CACHE_DIR", tmp_path / "llm_cache")
    for var in ("COF_LLM_BASE_URL", "COF_LLM_API_KEY", "COF_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


def _sse_events(resp) -> list[dict]:
    events = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


class _Queue:
    """按顺序吐 LLM 回复的桩；耗尽返回 None。"""

    def __init__(self, replies):
        self._replies = list(replies)

    def __call__(self, messages, max_tokens=None):
        if not self._replies:
            return None
        return self._replies.pop(0)


# ---------------------------------------------------------------- 删除

def test_delete_session_endpoint(client):
    sid = client.post("/api/assistant/sessions",
                      json={"title": "待删会话"}).json()["session_id"]
    assert client.get(f"/api/assistant/sessions/{sid}").status_code == 200
    r = client.delete(f"/api/assistant/sessions/{sid}")
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "session_id": sid}
    assert client.get(f"/api/assistant/sessions/{sid}").status_code == 404
    assert client.get("/api/assistant/sessions").json()["sessions"] == []
    # 重复删除 / 非法 id → 404
    assert client.delete(f"/api/assistant/sessions/{sid}").status_code == 404
    assert client.delete("/api/assistant/sessions/not-a-session").status_code == 404


def test_delete_session_unit(tmp_path):
    sid = sessions.create_session(title="单元测试")["session_id"]
    assert sessions.delete_session(sid) is True
    assert sessions.load_session(sid) is None
    assert sessions.delete_session(sid) is False          # 不存在
    assert sessions.delete_session("bad-id") is False      # 非法 id


# ---------------------------------------------------------------- 一对话一报告

def _make_session_with_messages():
    sid = sessions.create_session(title="TFPT 成膜讨论")["session_id"]
    sessions.append_message(sid, "user", "TFPT 和 B5 能成膜吗？")
    sessions.append_message(
        sid, "assistant", "可以。文献证实 TFPT 三醛与联苯类二胺可成膜。",
        tool_events=[{
            "type": "tool_call", "name": "academic_search",
            "args": {"query": "TFPT COF film"},
        }, {
            "type": "tool_result", "name": "academic_search",
            "summary": "Found paper: DOI 10.1000/xyz123 (TFPT film)",
            "is_error": False,
        }])
    return sid


def test_build_session_report_generate_and_update(monkeypatch):
    monkeypatch.setattr(llm_bridge, "is_configured", lambda: True)
    sid = _make_session_with_messages()

    # 第一版
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue([
        "# TFPT 成膜性综述\n## 研究背景\n背景。\n## 核心发现\n"
        "发现：TFPT 可成膜（DOI: 10.1000/xyz123）。\n"
        "## 详细分析\n分析。\n## 结论与建议\n结论。\n## 参考文献\n"
        "- 10.1000/xyz123\n## 附录：对话时间线\n- 问：TFPT 能成膜吗？\n"]))
    sess = sessions.load_session(sid)
    events = list(research.build_session_report(sess))
    rep_ev = next(e for e in events if e["type"] == "report")
    assert rep_ev["version"] == 1
    assert rep_ev["session_id"] == sid
    assert any(e["type"] == "done" for e in events)
    tokens = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert "TFPT 成膜性综述" in tokens

    rec = research.load_report(rep_ev["report_id"])
    assert rec["kind"] == "session" and rec["session_id"] == sid
    assert any("10.1000/xyz123" in (r.get("doi") or "") for r in rec["refs"])

    # 指针已写入会话 meta
    sess2 = sessions.load_session(sid)
    assert sess2["report"]["report_id"] == rep_ev["report_id"]
    assert sess2["report"]["version"] == 1

    # 第二版：新增消息 → 增量更新（v2，保留 v1 文件）
    sessions.append_message(sid, "user", "B5 的溶解性怎么解决？")
    sessions.append_message(sid, "assistant", "加氯仿助溶。")
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue([
        "# TFPT 成膜性综述（更新）\n## 研究背景\n背景。\n## 核心发现\n"
        "发现：TFPT 可成膜（DOI: 10.1000/xyz123）。\n## 详细分析\n"
        "B5 可加氯仿助溶。\n## 结论与建议\n结论。\n## 参考文献\n"
        "- 10.1000/xyz123\n## 附录：对话时间线\n- 问：TFPT 能成膜吗？\n"]))
    events2 = list(research.build_session_report(sessions.load_session(sid)))
    rep_ev2 = next(e for e in events2 if e["type"] == "report")
    assert rep_ev2["version"] == 2
    assert rep_ev2["report_id"] != rep_ev["report_id"]
    # v1 历史文件保留，指针切到 v2
    assert research.load_report(rep_ev["report_id"]) is not None
    assert sessions.load_session(sid)["report"]["version"] == 2


def test_session_report_endpoint_via_api(client, monkeypatch):
    monkeypatch.setattr(llm_bridge, "is_configured", lambda: True)
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue([
        "# 会话报告\n## 研究背景\nx\n## 核心发现\ny\n## 详细分析\nz\n"
        "## 结论与建议\nw\n## 参考文献\n本次对话未产生外部引用。\n"
        "## 附录：对话时间线\n- 问\n"]))
    sid = _make_session_with_messages()
    resp = client.post(f"/api/assistant/sessions/{sid}/report")
    assert resp.status_code == 200
    events = _sse_events(resp)
    rep_ev = next(e for e in events if e["type"] == "report")
    assert rep_ev["session_id"] == sid and rep_ev["version"] == 1
    assert any(e["type"] == "done" for e in events)
    # 会话详情透出报告指针
    detail = client.get(f"/api/assistant/sessions/{sid}").json()
    assert detail["report"]["report_id"] == rep_ev["report_id"]
    # 报告列表含会话报告（kind=session）
    lst = client.get("/api/assistant/research/reports").json()["reports"]
    row = next(r for r in lst if r["report_id"] == rep_ev["report_id"])
    assert row["kind"] == "session" and row["session_id"] == sid
    # 会话报告可删除（delete_report 兼容 sessrpt_ id）
    dr = client.delete(f"/api/assistant/research/reports/{rep_ev['report_id']}")
    assert dr.status_code == 200
    lst2 = client.get("/api/assistant/research/reports").json()["reports"]
    assert all(r["report_id"] != rep_ev["report_id"] for r in lst2)


def test_session_report_404(client, monkeypatch):
    monkeypatch.setattr(llm_bridge, "is_configured", lambda: True)
    resp = client.post("/api/assistant/sessions/sess_000000000000/report")
    events = _sse_events(resp)
    assert any(e["type"] == "error" and "会话不存在" in e["message"]
               for e in events)


def test_question_report_linked_to_session(monkeypatch):
    """run_research 带 session_id：报告落盘关联会话 + 会话内留完成消息。"""
    monkeypatch.setattr(llm_bridge, "is_configured", lambda: True)
    sid = _make_session_with_messages()

    monkeypatch.setattr(research, "plan_steps", lambda q: {
        "summary": "调研", "steps": [{"title": "t", "query": "q", "note": "n"}]})
    monkeypatch.setattr(research, "_execute_step",
                        lambda i, step, tools: iter([
                            {"type": "_result",
                             "result": {"text": "evidence DOI 10.1000/xyz123",
                                        "details": {}, "is_error": False}}]))
    monkeypatch.setattr(research, "_critic_gaps", lambda plan: [])
    monkeypatch.setattr(llm_bridge, "chat_text", _Queue([
        "# 单问报告\n## 背景\nx（DOI: 10.1000/xyz123）\n"]))

    events = list(research.run_research("TFPT 能成膜吗？", session_id=sid))
    rep_ev = next(e for e in events if e["type"] == "report")
    assert rep_ev["session_id"] == sid
    rec = research.load_report(rep_ev["report_id"])
    assert rec["kind"] == "question" and rec["session_id"] == sid
    # 会话内已留「深度研究完成」消息
    sess = sessions.load_session(sid)
    assert any("深度研究完成" in (m.get("content") or "")
               for m in sess["messages"])
    # 单问报告会被会话综合报告收集
    assert research._question_reports_of(sid)[0]["report_id"] == rep_ev["report_id"]
