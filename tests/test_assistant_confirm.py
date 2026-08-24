"""写操作二次确认（tool_confirm）流程测试：发起 → 确认/取消 → 续跑。

- chat 命中写类工具 → SSE 发 tool_confirm（令牌 + 影响说明），工具不执行
- POST /api/assistant/chat/confirm：确认 → 执行 + 续跑；取消 → 注入拒绝
- 令牌纪律：一次性 / 绑定会话 / 5 分钟过期 / 参数篡改拒绝
- SSE 事件序列集成（降级路径 B 与 function calling 路径 A 都覆盖）

LLM 全部打桩；会话目录与确认表隔离；假写工具挂进 registry.TOOLS
（monkeypatch.setitem，测试间互不影响）。
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
from src.assistant import confirm, llm_bridge, registry, sessions  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """隔离：会话目录到 tmp；LLM 配置链置空；确认表清空。"""
    monkeypatch.setattr(sessions, "SESSIONS_DIR",
                        tmp_path / "assistant" / "sessions")
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    monkeypatch.setattr(llm_client, "MINIMAX_SECRETS",
                        tmp_path / "secrets.local.json")
    monkeypatch.setattr(llm_client, "CACHE_DIR", tmp_path / "llm_cache")
    for var in ("COF_LLM_BASE_URL", "COF_LLM_API_KEY", "COF_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with confirm._LOCK:
        confirm._PENDING.clear()
    yield


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture()
def llm_on(monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    return llm_client


def _sse_events(resp) -> list[dict]:
    events = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _force_fallback(monkeypatch):
    """让路径 A 判定为不支持，强制走两段式（路径 B）。"""
    def _raise(*a, **kw):
        raise llm_bridge.FunctionCallingUnsupported("模拟端点不支持 tools")
    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", _raise)


@pytest.fixture()
def write_tool(monkeypatch):
    """注册假写工具 fake_write（记录真实调用），返回调用记录列表。"""
    calls: list[dict] = []

    def handler(args):
        calls.append(dict(args))
        return {"text": f"已写入 {args.get('value')}",
                "details": {"value": args.get("value")}, "is_error": False}

    monkeypatch.setitem(registry.TOOLS, "fake_write", {
        "handler": handler,
        "confirm": lambda args: f"将写入测试数据 {args.get('value')}（演示用）",
        "schema": {"type": "function", "function": {
            "name": "fake_write",
            "description": "测试写工具",
            "parameters": {"type": "object",
                           "properties": {"value": {"type": "string"}},
                           "required": ["value"]}}},
    })
    return calls


def _start_chat_with_write(client, monkeypatch, reply_after="已完成写入。"):
    """发起一轮 chat：假 LLM 第一轮要调 fake_write。返回 (events, token, sid)。"""
    _force_fallback(monkeypatch)
    script = [
        json.dumps({"tool": "fake_write", "args": {"value": "X"}},
                   ensure_ascii=False),
        json.dumps({"reply": reply_after}, ensure_ascii=False),
    ]
    calls = {"n": 0}

    def fake_chat(messages, **kw):
        calls["n"] += 1
        return script[min(calls["n"], len(script)) - 1]

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)
    r = client.post("/api/assistant/chat",
                    json={"message": "帮我写一下", "stream": True})
    assert r.status_code == 200
    events = _sse_events(r)
    token = next((e["confirm_token"] for e in events
                  if e["type"] == "tool_confirm"), None)
    sid = next((e["session_id"] for e in events if e["type"] == "done"), None)
    return events, token, sid


# ---------------------------------------------------------------------------
# 发起：tool_confirm 事件 + 工具未执行
# ---------------------------------------------------------------------------

def test_chat_emits_tool_confirm_without_executing(client, llm_on,
                                                   monkeypatch, write_tool):
    events, token, sid = _start_chat_with_write(client, monkeypatch)
    assert token and token.startswith("cfm_")
    gate = next(e for e in events if e["type"] == "tool_confirm")
    assert gate["name"] == "fake_write"
    assert "将写入测试数据 X" in gate["impact"]
    assert gate["expires_in"] == confirm.TTL_SECONDS
    assert "value" in gate["args_summary"]
    # tool_call 在确认事件之前；全程没有 tool_result（未执行）
    types = [e["type"] for e in events]
    assert types.index("tool_call") < types.index("tool_confirm")
    assert "tool_result" not in types
    assert write_tool == []  # 未确认不执行
    assert types[-1] == "done"
    # tool_confirm 随会话持久化
    detail = client.get(f"/api/assistant/sessions/{sid}").json()
    te = detail["messages"][1]["tool_events"]
    assert [e["type"] for e in te] == ["tool_call", "tool_confirm"]


# ---------------------------------------------------------------------------
# 确认 → 执行成功 + 续跑回复
# ---------------------------------------------------------------------------

def test_confirm_executes_and_resumes(client, llm_on, monkeypatch, write_tool):
    _events, token, sid = _start_chat_with_write(client, monkeypatch)
    r = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm"})
    assert r.status_code == 200
    events = _sse_events(r)
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["name"] == "fake_write" and tr["is_error"] is False
    assert "已写入 X" in tr["summary"]
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert reply == "已完成写入。"
    assert events[-1]["type"] == "done"
    assert write_tool == [{"value": "X"}]  # 恰好执行一次
    # 续跑的 assistant 消息落盘（tool_result 挂在上面）
    detail = client.get(f"/api/assistant/sessions/{sid}").json()
    assert detail["messages"][-1]["content"] == "已完成写入。"
    assert [e["type"] for e in detail["messages"][-1]["tool_events"]] == \
        ["tool_result"]


def test_cancel_skips_execution(client, llm_on, monkeypatch, write_tool):
    _events, token, sid = _start_chat_with_write(
        client, monkeypatch, reply_after="好的，已取消，没有写入任何数据。")
    r = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "cancel"})
    events = _sse_events(r)
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr.get("cancelled") is True and "取消" in tr["summary"]
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert "已取消" in reply
    assert write_tool == []  # 取消不执行


# ---------------------------------------------------------------------------
# 令牌纪律：一次性 / 过期 / 会话绑定 / 参数篡改
# ---------------------------------------------------------------------------

def test_token_single_use(client, llm_on, monkeypatch, write_tool):
    _events, token, sid = _start_chat_with_write(client, monkeypatch)
    r1 = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm"})
    assert any(e["type"] == "done" for e in _sse_events(r1))
    r2 = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm"})
    events2 = _sse_events(r2)
    assert events2[0]["type"] == "error"
    assert "不存在或已被使用" in events2[0]["message"]
    assert write_tool == [{"value": "X"}]  # 仍只执行一次


def test_token_expired(client, llm_on, monkeypatch, write_tool):
    _events, token, sid = _start_chat_with_write(client, monkeypatch)
    with confirm._LOCK:
        confirm._PENDING[token]["created_at"] -= confirm.TTL_SECONDS + 10
    r = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm"})
    events = _sse_events(r)
    assert events[0]["type"] == "error" and "过期" in events[0]["message"]
    assert write_tool == []


def test_token_bound_to_session(client, llm_on, monkeypatch, write_tool):
    _events, token, sid = _start_chat_with_write(client, monkeypatch)
    other = client.post("/api/assistant/sessions", json={"title": "别的会话"})
    other_sid = other.json()["session_id"]
    r = client.post("/api/assistant/chat/confirm", json={
        "session_id": other_sid, "confirm_token": token, "decision": "confirm"})
    events = _sse_events(r)
    assert events[0]["type"] == "error" and "不属于当前会话" in events[0]["message"]
    assert write_tool == []
    # 令牌未被销毁：合法会话仍可确认
    r2 = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm"})
    assert any(e["type"] == "done" for e in _sse_events(r2))
    assert write_tool == [{"value": "X"}]


def test_args_tampering_rejected(client, llm_on, monkeypatch, write_tool):
    _events, token, sid = _start_chat_with_write(client, monkeypatch)
    r = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm",
        "args": {"value": "Y"}})  # 篡改：发起时是 X
    events = _sse_events(r)
    assert events[0]["type"] == "error" and "不一致" in events[0]["message"]
    assert write_tool == []
    # 令牌保留：不带 args（以服务端存档为准）可正常确认
    r2 = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm"})
    assert any(e["type"] == "done" for e in _sse_events(r2))
    assert write_tool == [{"value": "X"}]  # 执行的是存档参数 X 而非 Y


def test_confirm_unknown_session(client, llm_on, monkeypatch, write_tool):
    _events, token, _sid = _start_chat_with_write(client, monkeypatch)
    r = client.post("/api/assistant/chat/confirm", json={
        "session_id": "sess_000000000000", "confirm_token": token,
        "decision": "confirm"})
    events = _sse_events(r)
    assert events[0]["type"] == "error" and "会话不存在" in events[0]["message"]
    assert write_tool == []


def test_confirm_llm_unconfigured(client, monkeypatch, write_tool):
    """LLM 未配置：confirm 直接 error 事件（不消耗令牌的场景外防御）。"""
    r = client.post("/api/assistant/chat/confirm", json={
        "session_id": "sess_x", "confirm_token": "cfm_x",
        "decision": "confirm"})
    events = _sse_events(r)
    assert events[0]["type"] == "error" and "未配置" in events[0]["message"]


# ---------------------------------------------------------------------------
# 路径 A（function calling）命中写工具 → 挂起 → 确认续跑
# ---------------------------------------------------------------------------

def test_function_calling_confirm_flow(client, llm_on, monkeypatch,
                                       write_tool):
    responses = [
        {"content": None, "tool_calls": [
            {"id": "call_0", "name": "fake_write", "args": {"value": "X"}}]},
        {"content": "已经帮你写入完成。", "tool_calls": []},
    ]
    calls = {"n": 0}

    def fake_fc(messages, tools, **kw):
        calls["n"] += 1
        return responses[min(calls["n"], len(responses)) - 1]

    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", fake_fc)

    r = client.post("/api/assistant/chat",
                    json={"message": "写一下", "stream": True})
    events = _sse_events(r)
    types = [e["type"] for e in events]
    assert "tool_confirm" in types and "tool_result" not in types
    assert write_tool == []
    token = next(e["confirm_token"] for e in events
                 if e["type"] == "tool_confirm")
    sid = events[-1]["session_id"]

    r2 = client.post("/api/assistant/chat/confirm", json={
        "session_id": sid, "confirm_token": token, "decision": "confirm"})
    events2 = _sse_events(r2)
    tr = next(e for e in events2 if e["type"] == "tool_result")
    assert tr["is_error"] is False
    reply = "".join(e["text"] for e in events2 if e["type"] == "token")
    assert "写入完成" in reply
    assert write_tool == [{"value": "X"}]
    # 续跑走的是路径 A：chat 首轮 1 次 + confirm 续跑 1 次
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# 读工具不触发确认（回归：读路径无 tool_confirm）
# ---------------------------------------------------------------------------

def test_read_tool_never_confirms(client, llm_on, monkeypatch):
    _force_fallback(monkeypatch)
    script = [
        json.dumps({"tool": "read_experiment_records",
                    "args": {"favorite_id": "fav_none"}}, ensure_ascii=False),
        json.dumps({"reply": "系统内未查到。"}, ensure_ascii=False),
    ]
    calls = {"n": 0}

    def fake_chat(messages, **kw):
        calls["n"] += 1
        return script[min(calls["n"], len(script)) - 1]

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)
    r = client.post("/api/assistant/chat",
                    json={"message": "查记录", "stream": True})
    events = _sse_events(r)
    types = [e["type"] for e in events]
    assert "tool_confirm" not in types
    assert "tool_result" in types  # 读工具直接执行
    assert confirm.pending_count() == 0
