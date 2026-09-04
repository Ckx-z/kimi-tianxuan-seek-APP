"""用户级记忆编译（Memory Compilation）：跨会话长期记忆。

存储：``user_data_root()/assistant/memory.md``，每条一行，格式::

    - [2026-08-24] 用户对苯二胺+均苯三甲醛组合两次成膜失败，倾向换溶剂体系

开关：存 ``llm_settings.local.json`` 的 ``assistant_memory_enabled`` 字段
（与 LLM 配置同款 local json；client.save_settings 已改为保留未知字段，
两处写操作互不覆盖）。默认启用。

两条能力：
- 编译（compile_session）：会话收尾时让 LLM 提炼"值得长期记住的事"
  （用户偏好 / 失败教训 / 正在推进的组合），带日期追加到 memory.md；
  超 MAX_ENTRIES 条时先让 LLM 合并去重再追加（防膨胀）。
- 注入（injection_block）：新会话开局把最近 INJECT_ENTRIES 条放进
  system prompt 的「用户记忆」段（层序 persona > 领域纪律 > 记忆 > 工具说明）。

纪律：LLM 未配置 / 调用失败一律静默跳过（返回 0 / 空串），绝不影响对话；
全部读写落 user_data_root，不进分发包。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

try:
    from src import runtime_config
    from src.assistant import llm_bridge
    from src.llm import client as llm_client
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore
    from assistant import llm_bridge  # type: ignore
    from llm import client as llm_client  # type: ignore

logger = logging.getLogger(__name__)

MEMORY_PATH = runtime_config.user_data_root() / "assistant" / "memory.md"
# 按单体组隔离记忆（v1.6.0 P2）：agent_memory/<pair_key>.md
PAIR_DIR = runtime_config.user_data_root() / "assistant" / "agent_memory"
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
MAX_PAIR_ENTRIES = 60       # 单组记忆条目上限（超出丢弃最旧）
INJECT_PAIR_ENTRIES = 20    # 开局注入该组最近条数

MAX_ENTRIES = 100       # 超过即触发合并去重（下一次编译前）
INJECT_ENTRIES = 30     # 开局注入的最近条数
MERGE_TARGET = 50       # 合并去重后的目标条数上限
_COMPILE_MAX_TOKENS = 1000
_MSG_SNIPPET = 500      # 编译时每条消息带入的字符上限
_MAX_COMPILE_MESSAGES = 80  # 编译时带入的消息条数上限

_ENTRY_RE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\]\s*(.*)$")
_BULLET_RE = re.compile(r"^(?:[-*•]|\d+[.、)])\s*(.*)$")

_COMPILE_PROMPT = (
    "你是记忆提炼助手。从下面的科研对话中提炼「值得长期记住的事」，例如："
    "用户的偏好与习惯、实验失败的教训、正在推进的单体组合或计划、"
    "用户明确做出的决定。规则：\n"
    "- 只输出要点列表，每行以「- 」开头，每条一句话；\n"
    "- 最多 8 条，不要输出任何其他文字（不要标题、不要解释）；\n"
    "- 没有值得长期记住的内容时，只输出一行「- 无」。"
)

_MERGE_PROMPT = (
    "下面是用户的长期记忆列表（可能重复、冗余或过时）。请合并去重、"
    "保留仍有价值的事实，输出不超过 %d 条。规则：\n"
    "- 每行保持「- [YYYY-MM-DD] 内容」格式（日期取该事实最近一次出现的日期）；\n"
    "- 只输出列表，不要任何其他文字。"
) % MERGE_TARGET


# ---------------------------------------------------------------------------
# 开关（存 llm_settings.local.json，与 LLM 配置同文件不同字段）
# ---------------------------------------------------------------------------

def _settings_path() -> Path:
    return llm_client.LOCAL_SETTINGS


def _read_json(path: Path) -> dict:
    try:
        if path.is_file():
            import json
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def is_enabled() -> bool:
    """记忆编译与注入总开关（默认启用）。"""
    return bool(_read_json(_settings_path()).get("assistant_memory_enabled", True))


def set_enabled(enabled: bool) -> None:
    """写开关：读-改-写，保留同文件其他字段（base_url / api_key 等）。"""
    import json
    path = _settings_path()
    data = _read_json(path)
    data["assistant_memory_enabled"] = bool(enabled)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# memory.md 读写
# ---------------------------------------------------------------------------

def read_text() -> str:
    """memory.md 原文；不存在返回空串。"""
    try:
        if MEMORY_PATH.is_file():
            return MEMORY_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("记忆读取失败: %s", exc)
    return ""


def write_text(content: str) -> None:
    """整体覆写 memory.md（设置页简单编辑用）。"""
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(content or "", encoding="utf-8")


def clear() -> None:
    """清空记忆（保留空文件，便于 UI 状态一致）。"""
    write_text("")


def load_entries() -> list[dict]:
    """解析为 [{"date": "YYYY-MM-DD", "text": "..."}]；无法解析的行忽略。"""
    out: list[dict] = []
    for line in read_text().splitlines():
        m = _ENTRY_RE.match(line.strip())
        if m and m.group(2).strip():
            out.append({"date": m.group(1), "text": m.group(2).strip()})
    return out


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def append_entries(texts: list[str]) -> int:
    """带今日日期追加条目，返回追加条数。"""
    items = [t.strip() for t in texts if isinstance(t, str) and t.strip()]
    if not items:
        return 0
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    date = _today()
    existing = read_text()
    block = "".join(f"- [{date}] {t}\n" for t in items)
    sep = "" if not existing or existing.endswith("\n") else "\n"
    MEMORY_PATH.write_text(existing + sep + block, encoding="utf-8")
    return len(items)


# ---------------------------------------------------------------------------
# 开局注入
# ---------------------------------------------------------------------------

def _global_injection_block() -> str:
    """全局 memory.md 的「用户记忆」段（最近 INJECT_ENTRIES 条）。"""
    entries = load_entries()[-INJECT_ENTRIES:]
    if not entries:
        return ""
    lines = [f"- [{e['date']}] {e['text']}" for e in entries]
    return ("# 用户记忆（历史会话提炼，供参考；与本轮指令冲突时以本轮为准）\n"
            + "\n".join(lines))


def injection_block(session: dict | None = None) -> str:
    """system prompt 的记忆段 =（可选）该单体组专属记忆 + 全局用户记忆。

    开关关闭 / 无记忆 / 读取失败 → 空串（不注入）。
    session 提供 context 时优先注入该组的 agent_memory（v1.6.0 P2）。
    """
    if not is_enabled():
        return ""
    blocks: list[str] = []
    if isinstance(session, dict):
        pair = pair_from_context(session.get("context") or {})
        if pair:
            key, label = pair
            content = read_pair_memory(key)
            entries = _parse_dated_lines(content)[-INJECT_PAIR_ENTRIES:]
            if entries:
                lines = [f"- [{e['date']}] {e['text']}" for e in entries]
                blocks.append(
                    f"# 「{label}」单体组专属记忆（历史讨论提炼，供参考）\n"
                    + "\n".join(lines))
    global_block = _global_injection_block()
    if global_block:
        blocks.append(global_block)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 按单体组记忆（v1.6.0 P2）
# ---------------------------------------------------------------------------

def _clean_smiles(smiles: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(smiles or "")).lower()[:40]


def _pair_key(ald_cas: str, amine_cas: str,
              ald_smiles: str, amine_smiles: str) -> str | None:
    """组 key：优先双 CAS（可读可查），否则 SMILES 摘要；非法字符已过滤。"""
    if ald_cas and amine_cas:
        key = f"{ald_cas}_{amine_cas}"
    elif ald_smiles and amine_smiles:
        key = f"smiles_{_clean_smiles(ald_smiles)}_{_clean_smiles(amine_smiles)}"
    else:
        return None
    return key if _KEY_RE.match(key) else None


def pair_from_context(context: dict) -> tuple[str, str] | None:
    """会话 context → (pair_key, 展示名)；无法定位单体组返回 None。

    context 可用 favorite_id（查收藏拿醛/胺）或直接 ald_cas/amine_cas/
    ald_smiles/amine_smiles。任何一步失败静默返回 None（不影响对话）。
    """
    context = context if isinstance(context, dict) else {}
    fav = None
    fid = str(context.get("favorite_id") or "").strip()
    if fid:
        try:
            from src.favorites import store as fav_store
        except ImportError:  # pragma: no cover
            from favorites import store as fav_store  # type: ignore
        try:
            fav = fav_store.get_favorite(fid)
        except Exception as exc:
            logger.warning("按组记忆查收藏失败（已跳过）: %s", exc)
    ald = (fav or {}).get("aldehyde") if isinstance(fav, dict) else {}
    amine = (fav or {}).get("amine") if isinstance(fav, dict) else {}
    if not isinstance(ald, dict):
        ald = {}
    if not isinstance(amine, dict):
        amine = {}
    ald_cas = str(ald.get("cas") or context.get("ald_cas") or "").strip()
    amine_cas = str(amine.get("cas") or context.get("amine_cas") or "").strip()
    ald_smiles = str(ald.get("smiles") or context.get("ald_smiles") or "").strip()
    amine_smiles = str(amine.get("smiles") or
                       context.get("amine_smiles") or "").strip()
    key = _pair_key(ald_cas, amine_cas, ald_smiles, amine_smiles)
    if not key:
        return None
    a = ald.get("name") or ald_cas or "醛"
    b = amine.get("name") or amine_cas or "胺"
    return key, f"{a} + {b}"


def _pair_path(key: str) -> Path:
    return PAIR_DIR / f"{key}.md"


def read_pair_memory(key: str) -> str:
    """读某组记忆原文；key 非法或不存在返回空串。"""
    if not _KEY_RE.match(key or ""):
        return ""
    try:
        p = _pair_path(key)
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except Exception as exc:
        logger.warning("按组记忆读取失败 %s: %s", key, exc)
        return ""


def _parse_dated_lines(content: str) -> list[dict]:
    out: list[dict] = []
    for line in (content or "").splitlines():
        m = _ENTRY_RE.match(line.strip())
        if m and m.group(2).strip():
            out.append({"date": m.group(1), "text": m.group(2).strip()})
    return out


def append_pair_memory(key: str, label: str, texts: list[str]) -> int:
    """向某组记忆追加条目（带日期 + 首行 label 注释），返回追加条数。"""
    if not _KEY_RE.match(key or ""):
        return 0
    items = [t.strip() for t in texts if isinstance(t, str) and t.strip()]
    if not items:
        return 0
    PAIR_DIR.mkdir(parents=True, exist_ok=True)
    path = _pair_path(key)
    existing = read_pair_memory(key)
    entries = _parse_dated_lines(existing)
    date = _today()
    entries += [{"date": date, "text": t} for t in items]
    if len(entries) > MAX_PAIR_ENTRIES:
        entries = entries[-MAX_PAIR_ENTRIES:]  # 丢弃最旧
    header = f"# label: {label}\n" if label else ""
    body = header + "".join(f"- [{e['date']}] {e['text']}\n" for e in entries)
    path.write_text(body, encoding="utf-8")
    return len(items)


def list_pair_memories() -> list[dict]:
    """全部按组记忆清单 [{key, label, updated_at, entries}]（新→旧）。"""
    out: list[dict] = []
    if not PAIR_DIR.is_dir():
        return out
    for p in PAIR_DIR.glob("*.md"):
        if not _KEY_RE.match(p.stem):
            continue
        content = read_pair_memory(p.stem)
        entries = _parse_dated_lines(content)
        label = ""
        for line in content.splitlines()[:3]:
            m = re.match(r"^#\s*label:\s*(.*)$", line.strip())
            if m:
                label = m.group(1).strip()
                break
        try:
            updated = datetime.fromtimestamp(p.stat().st_mtime).astimezone() \
                .isoformat(timespec="seconds")
        except OSError:
            updated = ""
        out.append({"key": p.stem, "label": label or p.stem,
                    "updated_at": updated, "entries": len(entries)})
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def clear_pair_memory(key: str) -> bool:
    """删除某组记忆文件；key 非法/不存在返回 False。"""
    if not _KEY_RE.match(key or ""):
        return False
    p = _pair_path(key)
    if not p.is_file():
        return False
    p.unlink()
    return True


# ---------------------------------------------------------------------------
# 编译
# ---------------------------------------------------------------------------

def _parse_bullets(text: str) -> list[str]:
    """从 LLM 输出提取要点行（兼容 - / * / • / 数字编号），去「无」去重。"""
    out: list[str] = []
    for line in (text or "").splitlines():
        m = _BULLET_RE.match(line.strip())
        if not m:
            continue
        item = m.group(1).strip()
        if not item or item in ("无", "无。", "none", "None"):
            continue
        if item not in out:
            out.append(item)
    return out


def _parse_dated_bullets(text: str, fallback_date: str) -> list[dict]:
    """解析合并去重输出：优先「- [date] 内容」，无日期的行补 fallback_date。"""
    out: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        m = _ENTRY_RE.match(line)
        if m and m.group(2).strip():
            out.append({"date": m.group(1), "text": m.group(2).strip()})
            continue
        b = _BULLET_RE.match(line)
        if b and b.group(1).strip() and b.group(1).strip() not in ("无", "无。"):
            out.append({"date": fallback_date, "text": b.group(1).strip()})
    # 去重（按文本）
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in out:
        if e["text"] not in seen:
            seen.add(e["text"])
            deduped.append(e)
    return deduped[:MERGE_TARGET]


def _merge_entries(entries: list[dict]) -> list[dict] | None:
    """LLM 合并去重；失败返回 None（调用方保留原列表，下次再试）。"""
    raw = "\n".join(f"- [{e['date']}] {e['text']}" for e in entries)
    text = llm_bridge.chat_text(
        [{"role": "system", "content": _MERGE_PROMPT},
         {"role": "user", "content": raw}],
        max_tokens=_COMPILE_MAX_TOKENS)
    if not text or not text.strip():
        return None
    merged = _parse_dated_bullets(text, fallback_date=_today())
    return merged or None


def _transcript(messages: list[dict]) -> str:
    lines: list[str] = []
    for m in messages[-_MAX_COMPILE_MESSAGES:]:
        role = "用户" if m.get("role") == "user" else "助手"
        content = str(m.get("content") or "").strip()
        if content:
            lines.append(f"{role}：{content[:_MSG_SNIPPET]}")
    return "\n".join(lines)


def compile_session(session: dict, *, force: bool = False) -> int:
    """从会话提炼长期记忆追加到 memory.md，返回追加条数。

    开关关闭（且未 force）/ 会话为空 / LLM 未配置或失败 → 静默返回 0。
    现有条目超 MAX_ENTRIES 时先让 LLM 合并去重（失败则保留原列表直接追加，
    合并留给下次编译重试）。
    """
    try:
        if not force and not is_enabled():
            return 0
        messages = [m for m in (session.get("messages") or [])
                    if m.get("role") in ("user", "assistant")
                    and str(m.get("content") or "").strip()]
        if not messages:
            return 0
        text = llm_bridge.chat_text(
            [{"role": "system", "content": _COMPILE_PROMPT},
             {"role": "user", "content": _transcript(messages)}],
            max_tokens=_COMPILE_MAX_TOKENS)
        if not text or not text.strip():
            return 0
        items = _parse_bullets(text)
        if not items:
            return 0

        entries = load_entries()
        if len(entries) > MAX_ENTRIES:
            merged = _merge_entries(entries)
            if merged is not None:
                write_text("".join(
                    f"- [{e['date']}] {e['text']}\n" for e in merged))
                logger.info("记忆合并去重：%d → %d 条", len(entries), len(merged))
        appended = append_entries(items)
        # v1.6.0 P2：同批要点按会话所在单体组镜像一份（组专属记忆）
        pair = pair_from_context(session.get("context") or {})
        if appended and pair:
            key, label = pair
            try:
                append_pair_memory(key, label, items)
            except Exception as exc:
                logger.warning("按组记忆追加失败（已跳过）: %s", exc)
        return appended
    except Exception as exc:  # 兜底：编译绝不拖垮对话
        logger.warning("记忆编译失败（已跳过）: %s", exc)
        return 0
