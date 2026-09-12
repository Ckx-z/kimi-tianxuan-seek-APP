"""深度研究上传文献（v1.9.3 问题 4.2）测试。

覆盖：
- 附件 → 证据块与伪工具结果（文档取文本；图片/空文本如实标注「未解析」）；
- run_research 把附件证据注入计划 LLM 与每步执行提示词，并进【证据清单】；
- 仅附件无文字时自动使用默认研究问题；
- 附件进入报告 refs（参考文献清单）；
- API 层：POST /research 携带 attachments → 透传 meta；question 为空但有附件时
  不再报错；无附件无问题时仍报 question 不能为空。
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

from src.assistant import llm_bridge, research  # noqa: E402
from src.assistant import attachments as att_mod  # noqa: E402
from src.assistant import registry  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "REPORTS_DIR", tmp_path / "research")
    monkeypatch.setattr(llm_bridge, "is_configured", lambda: True)
    return tmp_path


class _Queue:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages, max_tokens=None):
        self.calls.append(messages)
        if not self._replies:
            return None
        return self._replies.pop(0)


PLAN = json.dumps({"summary": "结合上传文献调研", "steps": [
    {"title": "上传文献要点", "query": "uploaded paper", "note": "读附件"}]})
REPORT = "# 报告\n\n## 核心发现\n附件显示成膜条件稳定。\n\n## 参考文献\n[1] 上传文献.pdf"


def _doc_meta(filename="upload.pdf"):
    return {"upload_id": "up_1", "filename": filename, "ext": "pdf",
            "kind": "document", "size": 1000}


def _image_meta():
    return {"upload_id": "up_2", "filename": "sem.png", "ext": "png",
            "kind": "image", "size": 2000}


# ---------------------------------------------------------------- 证据构造

def test_attachment_evidence_document_and_image(monkeypatch):
    monkeypatch.setattr(att_mod, "extract_text",
                        lambda meta: "本文报道 TFPT+B5 在 120 ℃ 成膜。"
                        if meta["kind"] == "document" else "")
    block, results = research.attachment_evidence([_doc_meta(), _image_meta()])
    assert "upload.pdf" in block and "TFPT+B5" in block
    assert "未做视觉解析" in block          # 图片如实标注，不编造
    assert len(results) == 2
    assert results[0]["details"]["papers"][0]["source"] == "user_attachment"


def test_attachment_evidence_empty():
    assert research.attachment_evidence(None) == ("", [])
    assert research.attachment_evidence([]) == ("", [])


# ---------------------------------------------------------------- 研究主循环

def test_run_research_injects_attachment_evidence(monkeypatch):
    monkeypatch.setattr(att_mod, "extract_text",
                        lambda meta: "附件正文：管壁扩散制膜，24 h 成膜。")
    monkeypatch.setattr(registry, "execute", lambda name, args: {
        "text": "检索结果", "details": {}, "is_error": False})
    monkeypatch.setattr(research, "available_research_tools", lambda: set())
    queue = _Queue([PLAN, json.dumps({"done": "附件显示 24 h 成膜"}),
                    json.dumps({"ok": True, "gaps": []}), REPORT])
    monkeypatch.setattr(llm_bridge, "chat_text", queue)

    events = list(research.run_research(
        "上传文献讲了什么", attachments=[_doc_meta()]))
    types = [e["type"] for e in events]
    assert "plan" in types and "report" in types and types[-1] == "done"
    # 附件事件对前端可见
    assert any(e["type"] == "tool_result" and e["name"] == "attachment"
               for e in events)
    # 计划提示词带附件证据
    plan_call = queue.calls[0]
    assert "管壁扩散制膜" in plan_call[-1]["content"]
    # 每一步执行的 system 提示词也带附件证据
    step_calls = [c for c in queue.calls
                  if any(m["content"].startswith("检索关键词") for m in c)]
    assert step_calls
    assert all("管壁扩散制膜" in m["content"]
               for c in step_calls for m in c if m["role"] == "system")
    # 报告落盘：附件进入 refs（参考文献清单）
    reports = research.list_reports()
    assert reports and reports[0]["ref_count"] >= 1
    saved = research.load_report(reports[0]["report_id"])
    titles = [r.get("title") for r in saved["refs"]]
    assert "upload.pdf" in titles


def test_run_research_default_question_with_attachment_only(monkeypatch):
    monkeypatch.setattr(att_mod, "extract_text", lambda meta: "附件内容")
    monkeypatch.setattr(registry, "execute", lambda name, args: {
        "text": "x", "details": {}, "is_error": False})
    monkeypatch.setattr(research, "available_research_tools", lambda: set())
    queue = _Queue([PLAN, json.dumps({"done": "小结"}),
                    json.dumps({"ok": True, "gaps": []}), REPORT])
    monkeypatch.setattr(llm_bridge, "chat_text", queue)
    events = list(research.run_research("", attachments=[_doc_meta()]))
    assert not any(e["type"] == "error" for e in events)
    assert "请基于我上传的文献" in queue.calls[0][-1]["content"]


def test_run_research_empty_question_without_attachment_errors():
    events = list(research.run_research(""))
    assert events and events[0]["type"] == "error"
    assert "question 不能为空" in events[0]["message"]


# ---------------------------------------------------------------- API 透传

def test_research_api_passes_attachment_metas(monkeypatch):
    captured: dict = {}

    def fake_run(question, allow_web=True, session_id=None, attachments=None):
        captured["question"] = question
        captured["attachments"] = attachments
        yield {"type": "plan", "steps": [], "summary": ""}
        yield {"type": "done"}

    monkeypatch.setattr(research, "run_research", fake_run)
    monkeypatch.setattr(att_mod, "get_meta", lambda uid: _doc_meta()
                        if uid == "up_1" else None)
    r = client.post("/api/assistant/research",
                    json={"question": "看这篇文献", "attachments": ["up_1"]})
    assert r.status_code == 200
    assert "plan" in r.text
    assert captured["attachments"] and \
        captured["attachments"][0]["filename"] == "upload.pdf"


def test_research_api_default_question_when_only_attachments(monkeypatch):
    captured: dict = {}

    def fake_run(question, allow_web=True, session_id=None, attachments=None):
        captured["question"] = question
        yield {"type": "done"}

    monkeypatch.setattr(research, "run_research", fake_run)
    monkeypatch.setattr(att_mod, "get_meta", lambda uid: _doc_meta())
    r = client.post("/api/assistant/research",
                    json={"question": "", "attachments": ["up_1"]})
    assert r.status_code == 200
    assert "请基于我上传的文献" in captured["question"]


def test_research_api_without_question_or_attachment_errors():
    r = client.post("/api/assistant/research", json={"question": ""})
    assert r.status_code == 200
    assert "question 不能为空" in r.text
