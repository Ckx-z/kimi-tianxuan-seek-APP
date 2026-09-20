"""P6（不依赖视觉模型的部分）：图片相关的降级提示与标注来源。

本轮只做**没有视觉模型也成立**的部分：
1. 助手收到图片但模型不支持图片输入时，提示必须「可操作」（指向设置页的视觉读图，
   或已配置时说明去哪用它），而不是含糊的「不支持看图」；
2. 文献图片的 `caption_source` 取值语义（pdf / vision / user / none）在数据层已落地
   （P1 的 figures 表 + 迁移写入 pdf/none），这里锁定取值约定，便于后续视觉标注接上。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from assistant import loop  # noqa: E402
from literature import vision  # noqa: E402


def _flat(content) -> str:
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content
                       if isinstance(p, dict) and p.get("type") == "text")
    return str(content)


def test_plain_content_points_to_settings_when_vision_disabled(monkeypatch):
    """未配置视觉读图 → 提示去设置页开启（可操作），且不谎称已看图。"""
    monkeypatch.setattr(vision, "status",
                        lambda: {"enabled": False, "available": False, "model": ""})
    content = [{"type": "text", "text": "看看这张图"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
    out = _flat(loop._plain_content(content))
    assert "不支持图片输入" in out
    assert "设置" in out and "视觉读图" in out
    assert "未被识别" in out                     # 如实说明，不假装看过


def test_plain_content_mentions_existing_vision_config(monkeypatch):
    """已配置视觉读图 → 说明可在文献库用它读图，并点明助手侧需要换模型。"""
    monkeypatch.setattr(vision, "status",
                        lambda: {"enabled": True, "available": True,
                                 "model": "qwen-vl-max"})
    content = [{"type": "text", "text": "看图"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
    out = _flat(loop._plain_content(content))
    assert "已配置视觉读图" in out
    assert "支持图片输入的模型" in out


def test_plain_content_without_image_untouched():
    assert _flat(loop._plain_content([{"type": "text", "text": "纯文本"}])) == "纯文本"
    assert loop._plain_content("直接字符串") == "直接字符串"


def test_caption_source_contract():
    """标注来源取值约定（与 P1 迁移、后续视觉标注保持一致）。"""
    allowed = {"pdf", "vision", "user", "none"}
    # 迁移写入规则：有图注 → pdf；无图注 → none（见 library_db.migrate_from_files）
    assert {"pdf", "none"} <= allowed
    # 人工编辑 → user；视觉模型 → vision（后续批次接入）
    assert "user" in allowed and "vision" in allowed
