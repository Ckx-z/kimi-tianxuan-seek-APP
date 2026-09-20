"""文献阅读的 LLM 能力（v1.9.8 R2）：**翻译** 与 **对文献提问**。

用户决定：不做离线翻译，统一用 DeepSeek（复用已配置的 OpenAI 兼容端点）。

设计要点
- **不新增依赖**：直接走 OpenAI 兼容 chat/completions（urllib），配置复用
  `literature.vision.resolve_config()`（它本身会回退到主 LLM 配置）；
- **翻译**：
  · 段落级批量翻译（一次多段，省 token），**按段落哈希缓存**（同段不重复翻）；
  · **格式保护**：SMILES / DOI / 数字与单位 / 化学式先替换为占位符，翻完还原，
    避免模型"翻译"掉 `10.1021/jacs.1c00001` 或 `2θ=2.76°`；
  · **术语表强制校正**（XRD→PXRD、BET、TGA…），纠正模型对表征缩写的乱译；
  · 返回 `provider/model/cached` 便于 UI 显示与成本可见。
- **提问**：上下文 = 摘要 + 选中段落 + 条目/图注摘要（限长），要求**只依据给定材料**回答，
  并在句末标注 `[p.页码]`；返回 `answer` 与 `used_context` 统计，便于 UI 提示"依据哪些内容"。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT = 120
MAX_SEGMENTS_PER_CALL = 12
MAX_SEGMENT_CHARS = 1800

SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_cache (
  key TEXT PRIMARY KEY,        -- sha1(目标语言 + 原文)
  source_text TEXT, target_lang TEXT, target_text TEXT,
  provider TEXT, model TEXT, created_at TEXT
);
"""

# 术语表：模型容易乱译的表征缩写与领域词（左：可能出现的写法，右：标准译法）
GLOSSARY: list[tuple[str, str]] = [
    (r"\bXRD\b", "XRD（X 射线衍射）"),
    (r"\bPXRD\b", "PXRD（粉末 X 射线衍射）"),
    (r"\bBET\b", "BET（比表面积）"),
    (r"\bTGA\b", "TGA（热重分析）"),
    (r"\bFTIR\b", "FTIR（傅里叶变换红外）"),
    (r"\bSEM\b", "SEM（扫描电镜）"),
    (r"\bTEM\b", "TEM（透射电镜）"),
    (r"\bXPS\b", "XPS（X 射线光电子能谱）"),
    (r"\bSPME\b", "SPME（固相微萃取）"),
    (r"\bCOF\b", "COF（共价有机框架）"),
    (r"\bCOFs\b", "COFs（共价有机框架）"),
    (r"\bMOF\b", "MOF（金属有机框架）"),
    (r"\bLangmuir surface area\b", "Langmuir 比表面积"),
]

# 需要保护的片段：DOI / URL / SMILES 字符块 / 数字与单位 / 化学式
PROTECT_PATTERNS = [
    r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+",          # DOI
    r"https?://\S+",                              # URL
    r"\b[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*\b",     # 化学式，如 C3N4 / TiO2
    r"[A-Za-z0-9@+\-\[\]\(\)=#$:/\\%]{12,}",      # 长 SMILES 串
    r"\d+(?:\.\d+)?\s*(?:°|%|nm|μm|um|mm|cm|m|mL|L|mg|g|kg|mol|mmol|h|min|s|K|°C|eV|V|mA|A|Hz|MHz|ppm|wt%|m2/g|m²/g|cm−1|cm-1)",
]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now() -> str:
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db._now()


def _config():
    """复用视觉模块的配置解析（它会回退到主 LLM 配置）。"""
    try:
        from literature import vision
    except ImportError:  # pragma: no cover
        from src.literature import vision  # type: ignore
    cfg = vision.resolve_config()
    base = str(cfg.get("base_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    model = str(cfg.get("model") or "")
    if not base or not key or not model:
        raise RuntimeError("未配置可用的 LLM（设置页填写 API Key / 模型后重试）")
    return base, key, model


def _chat(messages: list[dict], *, temperature: float = 0.2,
          max_tokens: int = 2000) -> str:
    base, key, model = _config()
    body = json.dumps({"model": model, "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")


# ---------------------------------------------------------------- 格式保护

def protect(text: str) -> tuple[str, dict[str, str]]:
    """把 DOI/URL/化学式/数字单位替换为占位符，返回 (新文本, 占位符→原串)。"""
    mapping: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        token = f"⟦{len(mapping)}⟧"
        mapping[token] = m.group(0)
        return token

    out = text
    for pat in PROTECT_PATTERNS:
        out = re.sub(pat, repl, out)
    return out, mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text


def apply_glossary(text: str) -> str:
    """术语强制校正（只补中文注释，不改写原文写法）。"""
    for pat, target in GLOSSARY:
        # 已是「XRD（…）」形式则跳过，避免重复追加
        text = re.sub(pat + r"(?!（)", target, text)
    return text


# ---------------------------------------------------------------- 翻译

def _cache_key(text: str, target_lang: str) -> str:
    return hashlib.sha1(f"{target_lang}\x00{text}".encode("utf-8")).hexdigest()


def translate_segments(conn: sqlite3.Connection, segments: list[str], *,
                       target_lang: str = "zh", use_cache: bool = True,
                       chat=None) -> dict:
    """段落级翻译（批量 + 缓存 + 格式保护 + 术语校正）。

    返回 {translations: [...], cached: n, provider, model}
    """
    ensure_schema(conn)
    chat = chat or _chat
    base, _key, model = _config()
    out: list[str | None] = [None] * len(segments)
    todo: list[tuple[int, str, dict[str, str]]] = []
    cached = 0

    for i, raw in enumerate(segments):
        text = (raw or "").strip()
        if not text:
            out[i] = ""
            continue
        if use_cache:
            row = conn.execute(
                "SELECT target_text FROM translation_cache WHERE key=?",
                (_cache_key(text, target_lang),)).fetchone()
            if row is not None:
                out[i] = row["target_text"]
                cached += 1
                continue
        protected, mapping = protect(text)
        todo.append((i, protected[:MAX_SEGMENT_CHARS], mapping))

    for start in range(0, len(todo), MAX_SEGMENTS_PER_CALL):
        batch = todo[start:start + MAX_SEGMENTS_PER_CALL]
        payload = [{"i": idx, "text": text} for idx, text, _m in batch]
        prompt = (
            f"你是材料化学领域的学术翻译。把下面 JSON 数组中每个 text 翻成"
            f"{'简体中文' if target_lang == 'zh' else target_lang}，保持学术语体、"
            "保留占位符 ⟦数字⟧ 原样不动。只输出 JSON 数组，元素为 "
            '{"i": 原索引, "t": 译文}，不要任何解释。\n' + json.dumps(payload, ensure_ascii=False))
        try:
            reply = chat([{"role": "user", "content": prompt}])
            raw_json = reply[reply.find("["): reply.rfind("]") + 1] or "[]"
            items = json.loads(raw_json)
        except Exception as exc:
            logger.warning("翻译批次失败：%s", exc)
            continue
        by_index = {int(it.get("i")): str(it.get("t") or "") for it in items
                    if isinstance(it, dict) and it.get("i") is not None}
        for idx, _text, mapping in batch:
            t = by_index.get(idx)
            if not t:
                continue
            t = apply_glossary(restore(t, mapping))
            out[idx] = t
            if use_cache:
                org = segments[idx].strip()
                conn.execute(
                    "INSERT OR REPLACE INTO translation_cache(key,source_text,"
                    "target_lang,target_text,provider,model,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (_cache_key(org, target_lang), org, target_lang, t,
                     "openai-compatible", model, _now()))
    conn.commit()
    return {"translations": [t or "" for t in out], "cached": cached,
            "provider": base, "model": model,
            "translated": sum(1 for t in out if t)}


# ---------------------------------------------------------------- 对文献提问

def paper_context(conn: sqlite3.Connection, paper_id: str, *,
                  selection: str = "", max_entries: int = 60,
                  max_chars: int = 9000) -> str:
    """组装问答上下文：摘要 + 选中段落 + 条目/图注要点。"""
    pid = str(paper_id)
    row = conn.execute(
        "SELECT title,abstract,journal,year,doi FROM items WHERE paper_id=?",
        (pid,)).fetchone()
    parts: list[str] = []
    if row is not None:
        head = "；".join(x for x in [row["title"] or "", row["journal"] or "",
                                     str(row["year"] or ""), row["doi"] or ""] if x)
        parts.append(f"【文献】{head}")
        if (row["abstract"] or "").strip():
            parts.append(f"【摘要】{row['abstract'].strip()[:2500]}")
    if selection.strip():
        parts.append(f"【用户选中的原文】{selection.strip()[:2500]}")
    ents = conn.execute(
        "SELECT kind,evidence FROM entries WHERE item_id=(SELECT id FROM items"
        " WHERE paper_id=?) AND evidence IS NOT NULL LIMIT ?", (pid, max_entries)).fetchall()
    if ents:
        lines = [f"- ({e['kind']}) {(e['evidence'] or '')[:180]}" for e in ents]
        parts.append("【已提取的结构化条目】\n" + "\n".join(lines))
    figs = conn.execute(
        "SELECT figure_type,caption FROM figures WHERE item_id=(SELECT id FROM items"
        " WHERE paper_id=?) AND caption IS NOT NULL LIMIT 20", (pid,)).fetchall()
    if figs:
        lines = [f"- [{f['figure_type']}] {(f['caption'] or '')[:160]}" for f in figs]
        parts.append("【图表标注】\n" + "\n".join(lines))
    ctx = "\n\n".join(parts)
    return ctx[:max_chars]


def ask(conn: sqlite3.Connection, paper_id: str, question: str, *,
        selection: str = "", history: list[dict] | None = None,
        chat=None) -> dict:
    """针对单篇文献提问（可带选中段落）。回答要求标注依据来源。"""
    q = (question or "").strip()
    if not q:
        raise ValueError("问题不能为空")
    chat = chat or _chat
    ctx = paper_context(conn, paper_id, selection=selection)
    if not ctx:
        return {"answer": "", "error": f"文献不存在或无可用上下文: {paper_id}"}
    system = (
        "你是 COF（共价有机框架）成膜方向的科研助手。只能依据用户提供的文献材料回答；"
        "材料不足以回答时明确说明缺什么，不要编造数据、文献或数值。"
        "凡引用具体结论或数值，在句末用 [材料] 标注来源类别（如 [摘要]/[条目]/[选中]）。")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"文献材料：\n{ctx}"}]
    for h in (history or [])[-6:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": str(h.get("content") or "")[:1500]})
    messages.append({"role": "user", "content": f"问题：{q}"})
    try:
        answer = chat(messages)
    except Exception as exc:
        return {"answer": "", "error": f"调用失败：{type(exc).__name__}: {exc}"}
    return {"paper_id": str(paper_id), "question": q, "answer": answer.strip(),
            "context_chars": len(ctx), "used_selection": bool(selection.strip())}
