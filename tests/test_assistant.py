"""科研助手 Agent 后端测试：接口契约形状 / 工具调用循环 / 双路径降级 /
LLM 未配置友好错误 / 会话持久化。

LLM 全部打桩（src/llm/client.chat_completion 与
src/assistant/llm_bridge.chat_completion_with_tools），不依赖真实网络；
会话目录与 LLM 配置路径全部隔离到 tmp_path，不碰真实数据。
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
from src.assistant import llm_bridge, sessions  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """隔离：会话目录到 tmp；LLM 配置链全部置空（默认未配置）。"""
    monkeypatch.setattr(sessions, "SESSIONS_DIR",
                        tmp_path / "assistant" / "sessions")
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    monkeypatch.setattr(llm_client, "MINIMAX_SECRETS",
                        tmp_path / "secrets.local.json")
    monkeypatch.setattr(llm_client, "CACHE_DIR", tmp_path / "llm_cache")
    for var in ("COF_LLM_BASE_URL", "COF_LLM_API_KEY", "COF_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture()
def llm_on(monkeypatch):
    """假装 LLM 已配置：is_configured=True + chat_completion 桩。"""
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    return llm_client


def _sse_events(resp) -> list[dict]:
    """解析 SSE 响应体为事件 dict 列表。"""
    events = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# 契约形状：status / sessions CRUD
# ---------------------------------------------------------------------------

def test_status_disabled_when_llm_unconfigured(client):
    r = client.get("/api/assistant/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert "未配置" in body["reason"]


def test_status_enabled(client, llm_on):
    r = client.get("/api/assistant/status")
    assert r.json() == {"enabled": True, "reason": ""}


def test_session_crud_shape(client):
    r = client.post("/api/assistant/sessions", json={
        "title": "TAPT 体系讨论",
        "context": {"favorite_id": "fav_x", "suggestion_ids": []},
    })
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"session_id", "title"}
    sid = body["session_id"]
    assert sid.startswith("sess_")
    assert body["title"] == "TAPT 体系讨论"

    r = client.get("/api/assistant/sessions")
    assert r.status_code == 200
    lst = r.json()["sessions"]
    assert len(lst) == 1
    item = lst[0]
    assert set(item) == {"session_id", "title", "updated_at", "message_count"}
    assert item["session_id"] == sid and item["message_count"] == 0

    r = client.get(f"/api/assistant/sessions/{sid}")
    assert r.status_code == 200
    detail = r.json()
    assert set(detail) == {"session_id", "title", "context", "messages",
                           "report"}  # v1.7.0：一对话一报告指针
    assert detail["context"]["favorite_id"] == "fav_x"
    assert detail["messages"] == []
    assert detail["report"] is None


def test_get_session_404(client):
    r = client.get("/api/assistant/sessions/sess_000000000000")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 会话重命名（v1.5.4）
# ---------------------------------------------------------------------------

def test_rename_session_success(client):
    created = client.post("/api/assistant/sessions",
                          json={"title": "旧标题"}).json()
    sid = created["session_id"]
    r = client.patch(f"/api/assistant/sessions/{sid}/title",
                     json={"title": "COF 结合能讨论"})
    assert r.status_code == 200
    assert r.json() == {"session_id": sid, "title": "COF 结合能讨论"}
    detail = client.get(f"/api/assistant/sessions/{sid}").json()
    assert detail["title"] == "COF 结合能讨论"
    assert detail["messages"] == []  # 重命名不影响消息内容
    lst = client.get("/api/assistant/sessions").json()["sessions"]
    assert lst[0]["title"] == "COF 结合能讨论"


def test_rename_session_validation(client):
    created = client.post("/api/assistant/sessions",
                          json={"title": "旧标题"}).json()
    sid = created["session_id"]
    # 空标题 / 超长标题 → 400
    assert client.patch(f"/api/assistant/sessions/{sid}/title",
                        json={"title": "   "}).status_code == 400
    assert client.patch(f"/api/assistant/sessions/{sid}/title",
                        json={"title": "x" * 81}).status_code == 400
    # 会话不存在 → 404
    assert client.patch("/api/assistant/sessions/sess_000000000000/title",
                        json={"title": "新"}).status_code == 404
    # 原标题保持不变（失败不落盘）
    detail = client.get(f"/api/assistant/sessions/{sid}").json()
    assert detail["title"] == "旧标题"


# ---------------------------------------------------------------------------
# chat：LLM 未配置时的友好错误（error 事件，非 500）
# ---------------------------------------------------------------------------

def test_chat_unconfigured_emits_error_event(client):
    r = client.post("/api/assistant/chat",
                    json={"message": "你好", "stream": True})
    assert r.status_code == 200  # 不能裸 500
    events = _sse_events(r)
    assert events[0]["type"] == "error"
    assert "未配置" in events[0]["message"]
    assert all(e["type"] != "done" for e in events)


def test_chat_unknown_session_error_event(client, llm_on):
    r = client.post("/api/assistant/chat", json={
        "session_id": "sess_000000000000", "message": "hi", "stream": True})
    events = _sse_events(r)
    assert events[0]["type"] == "error"
    assert "会话不存在" in events[0]["message"]


# ---------------------------------------------------------------------------
# 降级路径（两段式计划-执行）：假 LLM 返回工具指令
# ---------------------------------------------------------------------------

def _force_fallback(monkeypatch):
    """让路径 A 判定为不支持，强制走两段式。"""
    def _raise(*a, **kw):
        raise llm_bridge.FunctionCallingUnsupported("模拟端点不支持 tools")
    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", _raise)


def test_plan_execute_tool_loop(client, llm_on, monkeypatch):
    """假 LLM：第一轮要 read_experiment_records，第二轮给最终回答。"""
    _force_fallback(monkeypatch)
    script = [
        json.dumps({"tool": "read_experiment_records",
                    "args": {"favorite_id": "fav_none"}},
                    ensure_ascii=False),
        json.dumps({"reply": "系统内未查到该组实验记录。"}, ensure_ascii=False),
    ]
    calls = []

    def fake_chat(messages, **kw):
        calls.append(messages)
        return script[len(calls) - 1] if len(calls) <= len(script) else script[-1]

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)

    r = client.post("/api/assistant/chat",
                    json={"message": "这组做过实验吗", "stream": True})
    assert r.status_code == 200
    events = _sse_events(r)
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types
    tc = next(e for e in events if e["type"] == "tool_call")
    assert tc["name"] == "read_experiment_records"
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["is_error"] is False
    assert "未查到" in tr["summary"]
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert reply == "系统内未查到该组实验记录。"
    assert events[-1]["type"] == "done"
    assert events[-1]["session_id"].startswith("sess_")


def test_plan_execute_bad_json_retry_then_reply(client, llm_on, monkeypatch):
    """第一段输出乱格式 → 重问一次 → 输出合法 reply。"""
    _force_fallback(monkeypatch)
    script = ["我觉得大概也许可能吧（无 JSON）",
              json.dumps({"reply": "正式回答。"}, ensure_ascii=False)]
    calls = []

    def fake_chat(messages, **kw):
        calls.append(messages)
        return script[len(calls) - 1] if len(calls) <= len(script) else script[-1]

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)
    r = client.post("/api/assistant/chat", json={"message": "q", "stream": True})
    events = _sse_events(r)
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert reply == "正式回答。"
    assert events[-1]["type"] == "done"
    # 重问提示确实发出
    assert any("格式错误" in (m.get("content") or "")
               for m in calls[1] if m.get("role") == "user")


def test_plan_execute_tool_round_cap(client, llm_on, monkeypatch):
    """假 LLM 永远要调工具 → 最多 5 轮后强制收尾，不死循环。"""
    _force_fallback(monkeypatch)
    directive = json.dumps({"tool": "query_graphrag", "args": {"question": "x"}},
                           ensure_ascii=False)
    answers = {"n": 0}

    def fake_chat(messages, **kw):
        answers["n"] += 1
        last = str(messages[-1].get("content", ""))
        if "工具调用轮次已用完" in last:
            return "基于已获取信息的收尾回答。"
        return directive

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)
    # query_graphrag 工具不打真实检索，桩掉
    monkeypatch.setattr(
        "src.assistant.registry.TOOLS",
        {"query_graphrag": {
            "handler": lambda args: {"text": "系统内未查到。",
                                     "details": {}, "is_error": False},
            "schema": {"type": "function", "function": {
                "name": "query_graphrag", "description": "d",
                "parameters": {"type": "object",
                               "properties": {"question": {"type": "string"}},
                               "required": ["question"]}}},
        }})
    r = client.post("/api/assistant/chat", json={"message": "q", "stream": True})
    events = _sse_events(r)
    assert sum(1 for e in events if e["type"] == "tool_call") == 5  # 轮次上限
    assert events[-1]["type"] == "done"
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert "收尾回答" in reply


# ---------------------------------------------------------------------------
# 路径 A（function calling）：桩 chat_completion_with_tools
# ---------------------------------------------------------------------------

def test_function_calling_path(client, llm_on, monkeypatch):
    """假端点：第一轮返回 tool_calls，第二轮返回 content。"""
    responses = [
        {"content": None, "tool_calls": [
            {"id": "call_0", "name": "predict_film",
             "args": {"ald_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccccc1"}}]},
        {"content": "主分数 0.65，树模型路由 both_seen。", "tool_calls": []},
    ]
    calls = []

    def fake_fc(messages, tools, **kw):
        calls.append((messages, tools))
        return responses[len(calls) - 1]

    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", fake_fc)
    monkeypatch.setattr(
        "src.assistant.registry.TOOLS",
        {"predict_film": {
            "handler": lambda args: {
                "text": "主分数（max(树, GNN) 口径）：0.650",
                "details": {"score": 0.65}, "is_error": False},
            "schema": {"type": "function", "function": {
                "name": "predict_film", "description": "d",
                "parameters": {"type": "object", "properties": {
                    "ald_smiles": {"type": "string"},
                    "amine_smiles": {"type": "string"}},
                    "required": ["ald_smiles", "amine_smiles"]}}},
        }})

    r = client.post("/api/assistant/chat",
                    json={"message": "打个分", "stream": True})
    events = _sse_events(r)
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types
    reply = "".join(e["text"] for e in events if e["type"] == "token")
    assert "0.65" in reply
    assert events[-1]["type"] == "done"
    # 第一轮确实带了 tools schema
    assert calls[0][1] and calls[0][1][0]["type"] == "function"


def test_function_calling_llm_error_event(client, llm_on, monkeypatch):
    """路径 A 抛 LLMCallError（超时等）→ error 事件收尾，不降级不 500。"""
    def _raise(*a, **kw):
        raise llm_bridge.LLMCallError("LLM 连接失败: Timeout")
    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", _raise)
    r = client.post("/api/assistant/chat", json={"message": "q", "stream": True})
    events = _sse_events(r)
    assert events[0]["type"] == "error"
    assert "LLM 调用失败" in events[0]["message"]


# ---------------------------------------------------------------------------
# 会话持久化：chat 落盘后 GET 可取回（含 tool_events 与 context 合并）
# ---------------------------------------------------------------------------

def test_chat_persists_messages(client, llm_on, monkeypatch):
    _force_fallback(monkeypatch)
    script = [
        json.dumps({"tool": "read_experiment_records", "args": {}},
                   ensure_ascii=False),
        json.dumps({"reply": "查完了，没有记录。"}, ensure_ascii=False),
    ]
    calls = {"n": 0}

    def fake_chat(messages, **kw):
        calls["n"] += 1
        return script[min(calls["n"], len(script)) - 1]

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)

    r = client.post("/api/assistant/chat", json={
        "message": "帮我查一下实验记录",
        "context": {"favorite_id": "fav_persist"},
        "stream": True,
    })
    events = _sse_events(r)
    sid = events[-1]["session_id"]

    r = client.get(f"/api/assistant/sessions/{sid}")
    detail = r.json()
    assert detail["context"]["favorite_id"] == "fav_persist"
    msgs = detail["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "帮我查一下实验记录"
    assert msgs[1]["content"] == "查完了，没有记录。"
    assert msgs[0]["created_at"] and msgs[1]["created_at"]
    # 工具过程挂在 assistant 消息上
    tool_types = [e["type"] for e in msgs[1].get("tool_events", [])]
    assert tool_types == ["tool_call", "tool_result"]

    # 列表页 message_count 与排序
    lst = client.get("/api/assistant/sessions").json()["sessions"]
    assert lst[0]["session_id"] == sid
    assert lst[0]["message_count"] == 2


def test_chat_existing_session_history_carried(client, llm_on, monkeypatch):
    """对同一 session 再聊：历史消息进入 LLM messages。"""
    _force_fallback(monkeypatch)
    seen = []

    def fake_chat(messages, **kw):
        seen.append(list(messages))
        return json.dumps({"reply": "好的。"}, ensure_ascii=False)

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)

    r1 = client.post("/api/assistant/chat",
                     json={"message": "第一句", "stream": True})
    sid = _sse_events(r1)[-1]["session_id"]
    r2 = client.post("/api/assistant/chat", json={
        "session_id": sid, "message": "第二句", "stream": True})
    assert _sse_events(r2)[-1]["type"] == "done"
    # 第二次调用的 messages 里包含第一轮 user/assistant，且新消息在历史之后
    roles_contents = [(m["role"], m["content"]) for m in seen[1]]
    assert ("user", "第一句") in roles_contents
    assert ("assistant", "好的。") in roles_contents
    assert ("user", "第二句") in roles_contents
    assert roles_contents.index(("user", "第二句")) > \
        roles_contents.index(("assistant", "好的。"))


# ---------------------------------------------------------------------------
# 工具层单测：异常兜底与空数据口径
# ---------------------------------------------------------------------------

def test_tool_predict_film_param_missing():
    from src.assistant.tools.predict import predict_film
    r = predict_film("", "Nc1ccccc1")
    assert r["is_error"] is True and "参数缺失" in r["text"]


def test_tool_read_records_empty():
    from src.assistant.tools.records import read_experiment_records
    r = read_experiment_records("fav_nonexistent")
    assert r["is_error"] is False
    assert "未查到" in r["text"]
    assert r["details"]["count"] == 0


def test_registry_unknown_tool():
    from src.assistant import registry
    r = registry.execute("no_such_tool", {})
    assert r["is_error"] is True and "未知工具" in r["text"]


def test_registry_handler_exception_becomes_is_error(monkeypatch):
    from src.assistant import registry
    def _boom(args):
        raise RuntimeError("boom")
    monkeypatch.setitem(registry.TOOLS, "boom_tool", {
        "handler": _boom,
        "schema": {"type": "function", "function": {
            "name": "boom_tool", "description": "",
            "parameters": {"type": "object", "properties": {}}}},
    })
    r = registry.execute("boom_tool", {"x": 1})
    assert r["is_error"] is True and "boom" in r["text"]


def test_persona_system_prompt_contains_rules():
    from src.assistant import persona
    prompt = persona.build_system_prompt("## 当前单体组\n醛：TP / 胺：TAPT")
    assert "引用" in prompt or "编造" in prompt  # 领域纪律在场
    assert "当前单体组" in prompt                 # 上下文块注入
    assert "ming" in prompt                       # ming 身份卡渲染
