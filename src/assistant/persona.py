"""system prompt 三层拼装：ming 人格 + 领域规则 + 动态上下文块。

persona/*.md 为只读资源（随包分发）。加载顺序：模块同目录 persona/ →
runtime_config.resource_root()/src/assistant/persona/（PyInstaller datas
布局兜底）；两处都缺失时降级为内置最小规则串，绝不让 agent 裸奔无纪律。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

_PERSONA_DIR = Path(__file__).resolve().parent / "persona"

# 资源全部缺失时的兜底（宁可简陋也不能没有引用纪律）
_FALLBACK_RULES = (
    "# 领域规则\n"
    "- 凡涉及数据、文献、历史实验的论断必须来自工具返回；工具没查到就说"
    "“系统内未查到”，禁止编造 CAS 号、文献、数字。\n"
    "- OOD=out 的单体组必须显式警告并降低置信度。\n"
)


def _load(name: str) -> str:
    """读 persona 资源文件；失败返回空串（由调用方决定是否兜底）。"""
    candidates = [
        _PERSONA_DIR / name,
        runtime_config.resource_root() / "src" / "assistant" / "persona" / name,
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("persona 资源读取失败 %s: %s", path, exc)
    return ""


def _render_identity(raw: str, agent_name: str = "ming",
                     user_name: str = "用户") -> str:
    """渲染 ming 身份卡占位符（openhanako 模板变量）。"""
    return (raw.replace("{{agentName}}", agent_name)
               .replace("{{userName}}", user_name))


def build_system_prompt(context_block: str = "") -> str:
    """拼装完整 system prompt。

    层序：ming 身份卡 → ming 人格定义 → 领域规则 →（可选）当前上下文块。
    任一资源缺失跳过该层；人格与规则同时缺失时启用内置兜底规则。
    """
    identity = _render_identity(_load("ming_identity.md"))
    ishiki = _load("ming_ishiki.md")
    rules = _load("domain_rules.md")

    parts = [p for p in (identity, ishiki, rules) if p]
    if not rules:
        parts.append(_FALLBACK_RULES)
    if context_block.strip():
        parts.append("# 当前上下文\n\n" + context_block.strip())
    return "\n\n".join(parts)
