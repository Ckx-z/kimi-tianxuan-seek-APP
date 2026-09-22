"""P6：助手「带图的那一轮」切到视觉模型的路由测试。

打桩 requests.post 与配置解析，不联网、不读真实用户设置。
覆盖：配了视觉模型就切 / 没配就不切 / 不带图不切 / 配置读取异常不炸。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from assistant import llm_bridge  # noqa: E402

MAIN = {"base_url": "https://main.example.com/v1", "api_key": "sk-main",
        "model": "main-model", "source": "local_settings"}
VIS = {"enabled": True, "base_url": "https://vision.example.com/v1",
       "api_key": "sk-vision", "model": "vision-model", "inherits_main": False}


class _Resp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "好的"}}]}


@pytest.fixture()
def posts(monkeypatch):
    """记录每次 post 的 url/headers/body；配置解析固定为主模型。"""
    calls: list[dict] = []
    monkeypatch.setattr(llm_bridge.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        (calls.append({"url": url, "headers": headers or {},
                                       "json": json or {}}), _Resp())[1])
    monkeypatch.setattr(llm_bridge.llm_client, "_resolve", lambda: dict(MAIN))
    return calls


def _patch_vision(monkeypatch, *, enabled: bool, cfg: dict | None = None,
                  boom: bool = False) -> None:
    """把 vision 模块（可能同时以 literature.vision / src.literature.vision 存在）打桩。"""
    touched = 0
    for name in ("literature.vision", "src.literature.vision"):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        touched += 1
        if boom:
            def _raise(*_a, **_kw):
                raise RuntimeError("设置文件损坏")
            monkeypatch.setattr(mod, "is_enabled", lambda: True)
            monkeypatch.setattr(mod, "resolve_config", _raise)
            continue
        monkeypatch.setattr(mod, "is_enabled", lambda: enabled)
        monkeypatch.setattr(mod, "resolve_config", lambda: dict(cfg or VIS))
    if touched == 0:                      # 模块还没被导入 → 直接引进来再打桩
        from literature import vision as mod  # noqa: PLC0415
        monkeypatch.setattr(mod, "is_enabled", lambda: enabled)
        monkeypatch.setattr(mod, "resolve_config", lambda: dict(cfg or VIS))


def _img_messages():
    return [{"role": "user", "content": [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]}]


def _text_messages():
    return [{"role": "user", "content": "你好"}]


def test_image_turn_switches_to_vision_model(posts, monkeypatch):
    _patch_vision(monkeypatch, enabled=True)
    out = llm_bridge.chat_completion_with_tools(_img_messages(), [])
    assert out["content"] == "好的"
    assert len(posts) == 1
    call = posts[0]
    assert call["json"]["model"] == "vision-model"
    assert call["url"] == "https://vision.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-vision"
    # 图片分片原样保留（没有被降级成纯文本）
    assert any(p.get("type") == "image_url"
               for m in call["json"]["messages"] for p in
               (m.get("content") if isinstance(m.get("content"), list) else []))


def test_text_turn_keeps_main_model(posts, monkeypatch):
    _patch_vision(monkeypatch, enabled=True)
    llm_bridge.chat_completion_with_tools(_text_messages(), [])
    assert posts[0]["json"]["model"] == "main-model"
    assert posts[0]["url"] == "https://main.example.com/v1/chat/completions"
    assert posts[0]["headers"]["Authorization"] == "Bearer sk-main"


def test_image_turn_without_vision_config_keeps_main(posts, monkeypatch):
    """没配视觉模型 → 仍然用对话模型（端点若不支持图片，由 loop 降级提示）。"""
    _patch_vision(monkeypatch, enabled=False)
    llm_bridge.chat_completion_with_tools(_img_messages(), [])
    assert posts[0]["json"]["model"] == "main-model"
    assert posts[0]["headers"]["Authorization"] == "Bearer sk-main"


def test_incomplete_vision_config_keeps_main(posts, monkeypatch):
    """开关开了但字段不齐（缺 key）→ 不切换。"""
    _patch_vision(monkeypatch, enabled=True,
                  cfg={"enabled": True, "base_url": "https://vision.example.com/v1",
                       "api_key": "", "model": "vision-model"})
    llm_bridge.chat_completion_with_tools(_img_messages(), [])
    assert posts[0]["json"]["model"] == "main-model"


def test_broken_vision_settings_do_not_break_chat(posts, monkeypatch):
    """设置文件读崩了也不能让对话挂掉（退回主模型）。"""
    _patch_vision(monkeypatch, enabled=True, boom=True)
    out = llm_bridge.chat_completion_with_tools(_img_messages(), [])
    assert out["content"] == "好的"
    assert posts[0]["json"]["model"] == "main-model"


def test_has_image_parts_detects_only_content_lists(monkeypatch):
    assert llm_bridge._has_image_parts(_img_messages()) is True
    assert llm_bridge._has_image_parts(_text_messages()) is False
    assert llm_bridge._has_image_parts([]) is False
    assert llm_bridge._has_image_parts([{"role": "user", "content": None}]) is False
    # tool 结果里的 image_url 分片（content 为 list）也算
    assert llm_bridge._has_image_parts(
        [{"role": "tool", "content": [{"type": "image_url",
                                       "image_url": {"url": "data:,"}}]}]) is True
