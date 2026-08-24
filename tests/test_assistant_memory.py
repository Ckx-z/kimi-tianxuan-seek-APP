"""科研助手 V2.1 记忆编译测试：会话级压缩 / 用户级记忆编译与注入 /
防膨胀合并 / 开关 / memory 端点。

LLM 全部打桩（llm_bridge.chat_text 或 llm_client.chat_completion），
会话目录 / memory.md / LLM 配置全部隔离到 tmp_path。
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
from src.assistant import llm_bridge, loop, memory, sessions  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """隔离：会话目录 / memory.md / LLM 配置链到 tmp；清压缩缓存。"""
    monkeypatch.setattr(sessions, "SESSIONS_DIR",
                        tmp_path / "assistant" / "sessions")
    monkeypatch.setattr(memory, "MEMORY_PATH", tmp_path / "assistant" / "memory.md")
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    monkeypatch.setattr(llm_client, "MINIMAX_SECRETS",
                        tmp_path / "secrets.local.json")
    monkeypatch.setattr(llm_client, "CACHE_DIR", tmp_path / "llm_cache")
    for var in ("COF_LLM_BASE_URL", "COF_LLM_API_KEY", "COF_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    loop._SUMMARY_CACHE.clear()
    yield
    loop._SUMMARY_CACHE.clear()


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


def _make_session(rounds: int) -> dict:
    """造一个 rounds 轮（2*rounds 条消息）的持久化会话。"""
    created = sessions.create_session(title="压缩测试")
    sid = created["session_id"]
    for i in range(1, rounds + 1):
        sessions.append_message(sid, "user", f"第{i}轮问题")
        sessions.append_message(sid, "assistant", f"第{i}轮回答")
    return sessions.load_session(sid)


# ---------------------------------------------------------------------------
# 1. 会话级记忆压缩
# ---------------------------------------------------------------------------

def test_compress_triggered_over_20_rounds(monkeypatch):
    """21 轮（42 条）→ 早期轮次被纪要替换，只保留最近 10 条 + 新消息。"""
    seen = []

    def fake_chat_text(messages, **kw):
        seen.append(messages)
        return "纪要：第1-16轮讨论了 TAPT 体系打分，主分数 0.65，用户决定换溶剂。"

    monkeypatch.setattr(llm_bridge, "chat_text", fake_chat_text)
    sess = _make_session(21)
    messages = loop.build_messages(sess, "第22轮问题")

    # 摘要 LLM 调用确实发生，且带入的是早期轮次文本
    assert len(seen) == 1
    transcript = seen[0][-1]["content"]
    assert "第1轮问题" in transcript and "第16轮回答" in transcript

    roles = [m["role"] for m in messages]
    # [主 system, 纪要 system, 最近 10 条历史, 本轮 user]
    assert roles[0] == "system" and roles[1] == "system"
    assert "对话纪要" in messages[1]["content"]
    assert "主分数 0.65" in messages[1]["content"]
    history_texts = [m["content"] for m in messages[2:-1]]
    assert len(history_texts) == 10
    assert "第21轮回答" in history_texts[-1]
    # 早期轮次不再逐条出现
    assert all("第1轮问题" not in t and "第5轮回答" not in t
               for t in history_texts)
    assert messages[-1] == {"role": "user", "content": "第22轮问题"}


def test_compress_not_triggered_at_threshold(monkeypatch):
    """恰好 20 条（10 轮）→ 不压缩，全部带入，不调 LLM。"""
    def _boom(*a, **kw):
        raise AssertionError("未超阈值不应调用摘要 LLM")

    monkeypatch.setattr(llm_bridge, "chat_text", _boom)
    sess = _make_session(10)
    messages = loop.build_messages(sess, "新问题")
    history_texts = [m["content"] for m in messages[1:-1]]
    assert len(history_texts) == 20
    assert "第1轮问题" in history_texts[0]


def test_compress_llm_failure_hard_truncate(monkeypatch):
    """摘要 LLM 失败 → 降级硬截断（只留最近 10 条），无纪要、不报错。"""
    monkeypatch.setattr(llm_bridge, "chat_text", lambda *a, **kw: None)
    sess = _make_session(21)
    messages = loop.build_messages(sess, "第22轮问题")
    # 无纪要 system 消息：messages[1] 直接是历史
    assert messages[1]["role"] == "user"
    history_texts = [m["content"] for m in messages[1:-1]]
    assert len(history_texts) == 10
    assert "第21轮回答" in history_texts[-1]
    assert all("第1轮" not in t for t in history_texts)


def test_compress_summary_cached_per_session(monkeypatch):
    """同一覆盖范围不重复调 LLM（进程内缓存）。"""
    calls = {"n": 0}

    def fake_chat_text(messages, **kw):
        calls["n"] += 1
        return "纪要：缓存测试。"

    monkeypatch.setattr(llm_bridge, "chat_text", fake_chat_text)
    sess = _make_session(21)
    loop.build_messages(sess, "q1")
    loop.build_messages(sess, "q2")  # 历史未变 → 命中缓存
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 2. 用户级记忆编译
# ---------------------------------------------------------------------------

def test_compile_appends_dated_entries(monkeypatch):
    """提炼结果带今日日期追加到 memory.md。"""
    monkeypatch.setattr(
        llm_bridge, "chat_text",
        lambda *a, **kw: "- 用户偏好甲苯作溶剂\n- TAPT+TPA 两次成膜失败")
    sess = _make_session(2)
    n = memory.compile_session(sess)
    assert n == 2
    entries = memory.load_entries()
    assert [e["text"] for e in entries] == \
        ["用户偏好甲苯作溶剂", "TAPT+TPA 两次成膜失败"]
    assert all(len(e["date"]) == 10 for e in entries)  # YYYY-MM-DD


def test_compile_llm_failure_silent(monkeypatch):
    """LLM 未配置 / 失败 → 返回 0，不落文件、不抛异常。"""
    monkeypatch.setattr(llm_bridge, "chat_text", lambda *a, **kw: None)
    sess = _make_session(1)
    assert memory.compile_session(sess) == 0
    assert memory.read_text() == ""
    assert memory.load_entries() == []


def test_compile_empty_session_skipped(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("空会话不应调 LLM")

    monkeypatch.setattr(llm_bridge, "chat_text", _boom)
    created = sessions.create_session(title="空")
    sess = sessions.load_session(created["session_id"])
    assert memory.compile_session(sess) == 0


def test_compile_disabled_by_toggle(monkeypatch):
    """开关关闭 → 不编译；force=True（显式收尾钩子）仍执行。"""
    memory.set_enabled(False)
    monkeypatch.setattr(llm_bridge, "chat_text",
                        lambda *a, **kw: "- 一条记忆")
    sess = _make_session(1)
    assert memory.compile_session(sess) == 0
    assert memory.load_entries() == []
    assert memory.compile_session(sess, force=True) == 1


def test_compile_no_worth_nothing(monkeypatch):
    """LLM 判定「无」值得记住的内容 → 不追加。"""
    monkeypatch.setattr(llm_bridge, "chat_text", lambda *a, **kw: "- 无")
    sess = _make_session(1)
    assert memory.compile_session(sess) == 0
    assert memory.load_entries() == []


def test_merge_dedupe_when_over_100(monkeypatch):
    """memory.md 超 100 条 → 编译前先 LLM 合并去重，再追加新条目。"""
    memory.write_text("".join(
        f"- [2026-08-{i % 28 + 1:02d}] 旧记忆{i}\n" for i in range(101)))
    assert len(memory.load_entries()) == 101

    outputs = iter([
        "- 新教训：避免甲醇体系",                       # 第一次调用：提炼
        "- [2026-08-01] 合并记忆A\n- [2026-08-02] 合并记忆B",  # 第二次：合并
    ])
    monkeypatch.setattr(llm_bridge, "chat_text",
                        lambda *a, **kw: next(outputs))
    sess = _make_session(1)
    n = memory.compile_session(sess)
    assert n == 1
    entries = memory.load_entries()
    texts = [e["text"] for e in entries]
    assert "合并记忆A" in texts and "合并记忆B" in texts
    assert "新教训：避免甲醇体系" in texts
    assert all(not t.startswith("旧记忆") for t in texts)  # 旧 101 条被合并替换
    assert len(entries) == 3


def test_merge_failure_falls_back_to_plain_append(monkeypatch):
    """合并 LLM 失败 → 保留原列表直接追加（合并留给下次重试）。"""
    memory.write_text("".join(f"- [2026-08-01] 旧记忆{i}\n" for i in range(101)))
    outputs = iter(["- 新记忆", None])  # 提炼成功，合并失败
    monkeypatch.setattr(llm_bridge, "chat_text",
                        lambda *a, **kw: next(outputs))
    sess = _make_session(1)
    assert memory.compile_session(sess) == 1
    entries = memory.load_entries()
    assert len(entries) == 102
    assert entries[-1]["text"] == "新记忆"


# ---------------------------------------------------------------------------
# 3. 开局注入与分层
# ---------------------------------------------------------------------------

def test_injection_block_recent_30(monkeypatch):
    """注入最近 30 条；开关关闭 → 空串。"""
    memory.write_text("".join(f"- [2026-08-01] 记忆{i}\n" for i in range(40)))
    block = memory.injection_block()
    assert "用户记忆" in block
    lines = [l for l in block.splitlines() if l.startswith("- ")]
    assert len(lines) == 30
    assert "记忆10" in lines[0] and "记忆39" in lines[-1]  # 最近 30 条

    memory.set_enabled(False)
    assert memory.injection_block() == ""


def test_system_prompt_layer_order():
    """persona > 领域纪律 > 记忆 > 当前上下文 的分层顺序。"""
    from src.assistant import persona
    prompt = persona.build_system_prompt(
        "上下文块X", memory_block="# 用户记忆\n- [2026-08-24] 记忆Y")
    i_rules = prompt.find("引用") if "引用" in prompt else prompt.find("编造")
    i_mem = prompt.find("用户记忆")
    i_ctx = prompt.find("上下文块X")
    assert i_rules >= 0 and i_mem > i_rules and i_ctx > i_mem


def test_build_messages_injects_memory(monkeypatch):
    """loop.build_messages 的 system prompt 带用户记忆段。"""
    memory.write_text("- [2026-08-24] 用户在做 TAPT 体系\n")
    sess = _make_session(1)
    messages = loop.build_messages(sess, "继续")
    assert "用户在做 TAPT 体系" in messages[0]["content"]
    assert "用户记忆" in messages[0]["content"]


# ---------------------------------------------------------------------------
# 4. 端点：GET/PUT/DELETE /api/assistant/memory + 显式收尾 + 自动收尾
# ---------------------------------------------------------------------------

def test_memory_endpoints_roundtrip(client):
    r = client.get("/api/assistant/memory")
    assert r.status_code == 200
    body = r.json()
    assert body == {"enabled": True, "content": "", "entries": 0}

    # 覆写内容
    r = client.put("/api/assistant/memory",
                   json={"content": "- [2026-08-24] 手工记忆A\n"})
    assert r.json()["entries"] == 1
    # 开关切换
    r = client.put("/api/assistant/memory", json={"enabled": False})
    assert r.json()["enabled"] is False
    assert r.json()["entries"] == 1  # 内容不受开关影响
    # 清空
    r = client.delete("/api/assistant/memory")
    assert r.json()["cleared"] is True
    r = client.get("/api/assistant/memory")
    assert r.json()["entries"] == 0 and r.json()["enabled"] is False


def test_memory_toggle_preserves_llm_settings(client):
    """开关与 LLM 配置同文件不同字段：互不覆盖。"""
    llm_client.save_settings("https://api.example.com/v1", "sk-test", "m1")
    r = client.put("/api/assistant/memory", json={"enabled": False})
    assert r.json()["enabled"] is False
    cfg = json.loads(llm_client.LOCAL_SETTINGS.read_text(encoding="utf-8"))
    assert cfg["base_url"] == "https://api.example.com/v1"
    assert cfg["api_key"] == "sk-test"
    assert cfg["assistant_memory_enabled"] is False
    # 反向：再存 LLM 配置不掉开关
    llm_client.save_settings("https://api2.example.com/v1", "sk-test2", "m2")
    cfg = json.loads(llm_client.LOCAL_SETTINGS.read_text(encoding="utf-8"))
    assert cfg["assistant_memory_enabled"] is False


def test_compile_memory_hook_endpoint(client, monkeypatch):
    """显式收尾钩子：POST /sessions/{id}/compile-memory（force，忽略开关）。"""
    memory.set_enabled(False)  # force 仍应执行
    monkeypatch.setattr(llm_client, "chat_completion",
                        lambda *a, **kw: "- 显式收尾记忆")
    created = sessions.create_session(title="T")
    sid = created["session_id"]
    sessions.append_message(sid, "user", "讨论内容")
    sessions.append_message(sid, "assistant", "助手回答")

    r = client.post(f"/api/assistant/sessions/{sid}/compile-memory")
    assert r.status_code == 200
    assert r.json() == {"appended": 1}
    assert memory.load_entries()[0]["text"] == "显式收尾记忆"

    r = client.post("/api/assistant/sessions/sess_000000000000/compile-memory")
    assert r.status_code == 404


def test_new_session_finalizes_previous(client, monkeypatch):
    """POST /sessions 建新会话时，自动对上一会话做记忆编译。"""
    monkeypatch.setattr(llm_client, "chat_completion",
                        lambda *a, **kw: "- 用户正在推进 TAPT 组合")
    old = sessions.create_session(title="旧会话")
    sessions.append_message(old["session_id"], "user", "聊聊 TAPT")
    sessions.append_message(old["session_id"], "assistant", "好的")

    r = client.post("/api/assistant/sessions", json={"title": "新会话"})
    assert r.status_code == 200
    entries = memory.load_entries()
    assert len(entries) == 1
    assert "TAPT" in entries[0]["text"]


def test_new_session_finalize_disabled_by_toggle(client, monkeypatch):
    """开关关闭 → 建新会话不编译上一会话。"""
    memory.set_enabled(False)

    def _boom(*a, **kw):
        raise AssertionError("开关关闭不应调 LLM")

    monkeypatch.setattr(llm_client, "chat_completion", _boom)
    old = sessions.create_session(title="旧会话")
    sessions.append_message(old["session_id"], "user", "内容")
    r = client.post("/api/assistant/sessions", json={"title": "新会话"})
    assert r.status_code == 200
    assert memory.load_entries() == []


def test_new_session_finalize_llm_failure_silent(client, monkeypatch):
    """收尾 LLM 失败 → 新会话照常创建，不报错。"""
    monkeypatch.setattr(llm_client, "chat_completion", lambda *a, **kw: None)
    old = sessions.create_session(title="旧会话")
    sessions.append_message(old["session_id"], "user", "内容")
    r = client.post("/api/assistant/sessions", json={"title": "新会话"})
    assert r.status_code == 200
    assert r.json()["session_id"].startswith("sess_")
    assert memory.load_entries() == []
