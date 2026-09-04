"""SKILLS 机制（v1.6.0 P2）：方法论技能挂接，改 md 即改行为。

技能文件（Markdown + YAML-lite frontmatter）：:

    ---
    name: iterate_methodology
    description: 方案迭代方法论（一句话说明，进元数据）
    default-enabled: true
    ---
    # 正文（注入 system prompt 的「技能」段）

加载优先级（高 → 低）：
1. ``user_app_root()/skills/*.md`` —— 用户自建/覆盖（frozen 也可写，
   **改 md 即生效，无需重装**；同名文件覆盖内置）
2. 内置 ``persona/skills/*.md`` —— 随包分发

开关覆盖：``user_app_root()/config/skills.local.json`` 形如
``{"<name>": {"enabled": false}}``（不写该文件 = 用 frontmatter 默认值）。

纪律：解析失败静默跳过该技能；skills_block 失败返回空串，绝不影响对话。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

_BUILTIN_DIR = Path(__file__).resolve().parent / "persona" / "skills"
_OVERLAY_DIR = runtime_config.user_app_root() / "skills"
_OVERRIDE_PATH = runtime_config.user_app_root() / "config" / "skills.local.json"

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析头部 frontmatter（两行 --- 之间的 key: value），返回 (meta, 正文)。"""
    meta: dict = {}
    body = text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body = "\n".join(lines[i + 1:]).strip()
                break
            m = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", lines[i].strip())
            if m:
                meta[m.group(1)] = m.group(2).strip()
    return meta, body


def _load_skill_file(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("技能文件读取失败 %s: %s", path, exc)
        return None
    meta, body = _parse_frontmatter(text)
    name = str(meta.get("name") or path.stem).strip()
    if not _NAME_RE.match(name):
        return None
    return {
        "name": name,
        "description": str(meta.get("description") or "").strip(),
        "default_enabled": str(meta.get("default-enabled") or
                               "true").strip().lower() != "false",
        "body": body,
        "source": "user" if path.parent == _OVERLAY_DIR else "builtin",
    }


def _override_map() -> dict:
    try:
        if _OVERRIDE_PATH.is_file():
            data = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning("技能开关配置读取失败: %s", exc)
    return {}


def list_skills() -> list[dict]:
    """技能清单 [{name, description, enabled, source}]（同名用户文件覆盖内置）。"""
    skills: dict[str, dict] = {}
    for d in (_BUILTIN_DIR, _OVERLAY_DIR):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            s = _load_skill_file(p)
            if s:
                skills[s["name"]] = s  # 后遍历的 user 覆盖 builtin
    overrides = _override_map()
    out = []
    for s in skills.values():
        enabled = bool(overrides.get(s["name"], {}).get(
            "enabled", s["default_enabled"])) if isinstance(
                overrides.get(s["name"]), dict) else s["default_enabled"]
        out.append({
            "name": s["name"],
            "description": s["description"],
            "enabled": enabled,
            "source": s["source"],
        })
    out.sort(key=lambda x: x["name"])
    return out


def set_enabled(name: str, enabled: bool) -> bool:
    """写某技能开关（user_app_root/config/skills.local.json，读-改-写）。"""
    if not _NAME_RE.match(name or ""):
        return False
    if not any(s["name"] == name for s in list_skills()):
        return False
    try:
        data = _override_map()
        data.setdefault(name, {})["enabled"] = bool(enabled)
        _OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OVERRIDE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("技能开关写入失败: %s", exc)
        return False


def skills_block() -> str:
    """启用技能的正文拼接（system prompt「技能」段）；无启用技能返回空串。"""
    overrides = _override_map()
    parts: list[str] = []
    skills: dict[str, dict] = {}
    for d in (_BUILTIN_DIR, _OVERLAY_DIR):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            s = _load_skill_file(p)
            if s:
                skills[s["name"]] = s
    for s in skills.values():
        o = overrides.get(s["name"])
        enabled = o.get("enabled", s["default_enabled"]) \
            if isinstance(o, dict) else s["default_enabled"]
        if enabled and s["body"]:
            parts.append(f"# 技能：{s['name']}\n{s['body']}")
    if not parts:
        return ""
    return "# 技能（方法论，按此执行）\n\n" + "\n\n".join(parts)
