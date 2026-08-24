"""科研助手附件测试：上传落盘 / 文本提取（txt/docx/pdf）/ vision data URL /
会话持久化 / chat 注入与降级提示。

上传目录与会话目录 monkeypatch 到 tmp_path；LLM 全部打桩，不依赖真实网络。
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
from src.assistant import attachments, llm_bridge, sessions  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """隔离：上传目录 / 会话目录到 tmp；LLM 配置链全部置空。"""
    monkeypatch.setattr(attachments, "UPLOADS_DIR",
                        tmp_path / "assistant" / "uploads")
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
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    return llm_client


def _sse_events(resp) -> list[dict]:
    events = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# 上传落盘与校验
# ---------------------------------------------------------------------------

def test_save_upload_txt_roundtrip():
    meta = attachments.save_upload("笔记.txt", "界面法，120 °C 陈化 3 天".encode("utf-8"))
    assert meta["kind"] == "document"
    assert meta["ext"] == ".txt"
    assert meta["upload_id"].startswith("u_")
    assert attachments.file_path_of(meta).is_file()
    # 元信息可取回
    got = attachments.get_meta(meta["upload_id"])
    assert got is not None and got["filename"] == "笔记.txt"
    # 文本提取
    assert "120 °C" in attachments.extract_text(got)


def test_save_upload_rejects_bad_ext_and_size():
    with pytest.raises(attachments.AttachmentError, match="不支持的附件类型"):
        attachments.save_upload("evil.exe", b"MZ")
    with pytest.raises(attachments.AttachmentError, match="大小限制"):
        attachments.save_upload("big.png", b"x" * (attachments.MAX_BYTES + 1))
    with pytest.raises(attachments.AttachmentError, match="内容为空"):
        attachments.save_upload("empty.txt", b"")


def test_get_meta_invalid_id_returns_none():
    assert attachments.get_meta("u_000000000000") is None
    assert attachments.get_meta("../etc/passwd") is None


def test_extract_docx(tmp_path):
    import docx
    doc = docx.Document()
    doc.add_paragraph("COF 界面法：均三甲苯/二氧六环，120 °C。")
    doc.save(str(tmp_path / "方案.docx"))
    meta = attachments.save_upload("方案.docx", (tmp_path / "方案.docx").read_bytes())
    text = attachments.extract_text(meta)
    assert "界面法" in text and "120" in text


def test_extract_pdf(tmp_path):
    fitz = pytest.importorskip("fitz", reason="PyMuPDF 不可用则跳过 pdf 用例")
    src = tmp_path / "文献.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "COF interfacial synthesis at 120 C")
        doc.save(str(src))
    meta = attachments.save_upload("文献.pdf", src.read_bytes())
    text = attachments.extract_text(meta)
    assert "interfacial" in text


def test_image_data_url():
    meta = attachments.save_upload("照片.png", b"\x89PNG\r\n\x1a\nfake")
    assert meta["kind"] == "image"
    url = attachments.image_data_url(meta)
    assert url.startswith("data:image/png;base64,")
    # 文档不能取 data URL
    with pytest.raises(attachments.AttachmentError):
        attachments.image_data_url({**meta, "ext": ".txt"})


# ---------------------------------------------------------------------------
# 上传端点
# ---------------------------------------------------------------------------

def test_upload_endpoint_ok_and_400(client):
    r = client.post("/api/assistant/uploads",
                    files={"file": ("条件.csv", "溶剂,温度\n二氧六环,120\n".encode("utf-8"),
                                    "text/csv")})
    assert r.status_code == 201
    meta = r.json()
    assert meta["kind"] == "document" and meta["upload_id"].startswith("u_")

    r = client.post("/api/assistant/uploads",
                    files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    assert "不支持" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 会话持久化：附件元信息随消息落盘
# ---------------------------------------------------------------------------

def test_session_message_attachments_persisted():
    sess = sessions.create_session(title="t")
    sid = sess["session_id"]
    atts = [{"upload_id": "u_abc123abc123", "filename": "a.pdf",
             "ext": ".pdf", "kind": "document", "size": 10}]
    sessions.append_message(sid, "user", "看这个", attachments=atts)
    loaded = sessions.load_session(sid)
    assert loaded["messages"][0]["attachments"] == atts
    # meta 更新后附件仍保留
    sessions.update_meta(sid, title="新标题")
    loaded = sessions.load_session(sid)
    assert loaded["messages"][0]["attachments"] == atts


# ---------------------------------------------------------------------------
# chat：文档注入上下文 / 图片 vision 与降级
# ---------------------------------------------------------------------------

def test_chat_doc_attachment_injects_text(client, llm_on, monkeypatch):
    meta = attachments.save_upload("方案.txt", "界面法 120 °C 陈化 72h".encode("utf-8"))
    captured = []

    def fake_fc(messages, tools, **kw):
        captured.append(list(messages))
        return {"content": "已阅读附件。", "tool_calls": []}

    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", fake_fc)
    r = client.post("/api/assistant/chat", json={
        "message": "参考附件给个建议",
        "attachments": [meta["upload_id"], "u_ffffffffffff"],  # 无效 id 跳过
        "stream": True,
    })
    assert r.status_code == 200
    events = _sse_events(r)
    assert events[-1]["type"] == "done"
    # 文档文本注入了发给 LLM 的用户消息
    user_msg = captured[0][-1]
    assert user_msg["role"] == "user"
    assert "【附件 方案.txt 内容】" in user_msg["content"]
    assert "陈化 72h" in user_msg["content"]
    # 会话落盘：user 消息带附件元信息
    sid = events[-1]["session_id"]
    detail = client.get(f"/api/assistant/sessions/{sid}").json()
    assert detail["messages"][0]["attachments"][0]["filename"] == "方案.txt"


def test_chat_image_vision_format(client, llm_on, monkeypatch):
    """图片以 OpenAI vision 分片格式发出（base64 data URL）。"""
    meta = attachments.save_upload("电镜图.png", b"\x89PNG\r\n\x1a\nfake")
    captured = []

    def fake_fc(messages, tools, **kw):
        captured.append(list(messages))
        return {"content": "图上可以看到……", "tool_calls": []}

    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", fake_fc)
    r = client.post("/api/assistant/chat", json={
        "message": "看形貌如何",
        "attachments": [meta["upload_id"]],
        "stream": True,
    })
    assert _sse_events(r)[-1]["type"] == "done"
    content = captured[0][-1]["content"]
    assert isinstance(content, list)  # vision 分片
    assert content[0]["type"] == "text" and "电镜图.png" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_chat_image_fallback_plain_text(client, llm_on, monkeypatch):
    """端点不支持 vision/tools（4xx）→ 降级路径 B：图片替换为不支持看图提示。"""
    meta = attachments.save_upload("形貌.webp", b"RIFFfake")

    def _raise(*a, **kw):
        raise llm_bridge.FunctionCallingUnsupported("模拟端点拒绝（HTTP 400）")

    monkeypatch.setattr(llm_bridge, "chat_completion_with_tools", _raise)
    seen = []

    def fake_chat(messages, **kw):
        seen.append(list(messages))
        return json.dumps({"reply": "当前模型不支持看图。"}, ensure_ascii=False)

    monkeypatch.setattr(llm_client, "chat_completion", fake_chat)
    r = client.post("/api/assistant/chat", json={
        "message": "分析一下",
        "attachments": [meta["upload_id"]],
        "stream": True,
    })
    events = _sse_events(r)
    assert events[-1]["type"] == "done"
    user_contents = [m["content"] for m in seen[0] if m.get("role") == "user"]
    assert any(isinstance(c, str) and "不支持看图" in c for c in user_contents)
    # 降级后 content 全部为纯文本（无分片列表泄漏给纯文本端点）
    assert all(isinstance(m["content"], str) for m in seen[0])


def test_chat_attachments_only_no_text(client, llm_on, monkeypatch):
    """只发附件不填文字：用兜底文案，正常完成。"""
    meta = attachments.save_upload("数据.json", b'{"solvent": "mesitylene"}')
    monkeypatch.setattr(
        llm_bridge, "chat_completion_with_tools",
        lambda messages, tools, **kw: {"content": "好的。", "tool_calls": []})
    r = client.post("/api/assistant/chat", json={
        "message": "",
        "attachments": [meta["upload_id"]],
        "stream": True,
    })
    events = _sse_events(r)
    assert events[-1]["type"] == "done"
    sid = events[-1]["session_id"]
    detail = client.get(f"/api/assistant/sessions/{sid}").json()
    assert detail["messages"][0]["content"]  # 兜底文案非空
