"""文献 PDF → LLM 结构化提取（文献录入第三通道，Crossref 不可达/查不到时用）。

流程：PyMuPDF（fitz）提取全文（截断前 MAX_TEXT_CHARS 字符）→ 走科研助手现有
LLM 配置（src/llm/client.py 门面的配置链 + src/assistant/llm_bridge.py 的
推理模型口径）→ 要求严格 JSON → 解析为与 Crossref lookup 一致的「待审核草稿」
结构（source 标 "pdf-llm"，附带 pdf_filename；existing 标记由路由层补）。

异常语义（路由层据此转 HTTP 错误，不崩溃）：
- PdfNoTextError：PDF 无文本层（扫描件）→ 422；
- LLMNotConfiguredError：LLM 未配置 → 503；
- LLMExtractError：LLM 调用失败 / 返回无法解析 → 502。

测试打桩点：``_llm_configured`` 与 ``_llm_chat`` 两个模块级 wrapper（避免
src.* / 裸名双实例问题，monkeypatch 本模块一处即生效）。
"""

from __future__ import annotations

import json
import logging
import re

try:
    from src.assistant import llm_bridge
    from src.llm import client as llm_client
except ImportError:  # 包路径导入（src/ 直接在 sys.path 上）
    from assistant import llm_bridge  # type: ignore
    from llm import client as llm_client  # type: ignore

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 20 * 1024 * 1024  # 上传上限 20MB
MAX_TEXT_CHARS = 15000            # 送入 LLM 的全文截断长度
MIN_TEXT_CHARS = 50               # 低于此长度视为无文本层（扫描件）
LLM_MAX_TOKENS = 4000             # 推理型模型给足输出预算（同 llm_bridge 口径）


class PdfExtractError(Exception):
    """PDF 读取/解析失败（损坏或非 PDF）→ 400。"""


class PdfNoTextError(Exception):
    """PDF 无文本层（可能是扫描件）→ 422。"""


class LLMNotConfiguredError(Exception):
    """LLM 未配置 → 503。"""


class LLMExtractError(Exception):
    """LLM 调用失败或返回无法解析 → 502。"""


# ---------------------------------------------------------------- LLM 打桩点

def _llm_configured() -> bool:
    """透传门面配置状态（测试 monkeypatch 点）。"""
    return llm_client.is_configured()


def _llm_chat(messages: list) -> str | None:
    """路径 B 纯文本调用（测试 monkeypatch 点）；未配置/失败返回 None。"""
    return llm_bridge.chat_text(messages, max_tokens=LLM_MAX_TOKENS)


# ---------------------------------------------------------------- PDF 文本提取

def extract_text(pdf_bytes: bytes) -> str:
    """fitz 提取全部页文本，拼接并截断到 MAX_TEXT_CHARS。

    打不开/非 PDF 抛 PdfExtractError。
    """
    try:
        import fitz  # PyMuPDF（惰性导入，便于测试与裁剪）
    except ImportError as exc:  # pragma: no cover - 打包漏收时才触发
        raise PdfExtractError("PDF 解析组件不可用（PyMuPDF 未安装）") from exc
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfExtractError(f"文件不是有效的 PDF：{type(exc).__name__}") from exc
    try:
        parts = [page.get_text() for page in doc]
    finally:
        doc.close()
    text = "\n".join(p for p in parts if p)
    return text[:MAX_TEXT_CHARS]


# ---------------------------------------------------------------- 提示词

_PROMPT_TEMPLATE = """\
你是科研文献元数据提取助手。下面是用户上传的一篇文献 PDF 的全文（可能截断）。
请从中提取文献元数据，只输出一个严格 JSON 对象，不要输出任何其他文字、
解释或 markdown 代码围栏。

要求：
- 输出 JSON 结构：{{"title": str, "authors": [str, ...], "journal": str,
  "year": int 或 null, "doi": str, "abstract": str 或 null}}
- title：文献完整标题（保留原文语言）。
- authors：作者列表，每位作者一个字符串（"名 姓" 格式，按原文顺序）。
- journal：期刊/会议全名；无法确定给空字符串。
- year：发表年份（整数）；无法确定给 null。
- doi：DOI 本体（形如 10.xxxx/...，不要带 https://doi.org/ 前缀）；
  文中找不到给空字符串。
- abstract：摘要原文（保留原文语言，不超过 500 字）；找不到给 null。

文献全文：
\"\"\"
{text}
\"\"\"
"""


def build_prompt(text: str) -> list:
    """组装 chat messages（单轮 user 消息）。"""
    return [{"role": "user", "content": _PROMPT_TEMPLATE.format(text=text)}]


# ---------------------------------------------------------------- 响应解析

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_llm_json(content: str) -> dict:
    """解析 LLM 输出为草稿 dict。

    容错：先去 markdown 代码围栏，再退化到首个 { 到末个 } 的子串；
    结构不合要求（非对象 / title 为空）抛 LLMExtractError。
    """
    raw = str(content or "").strip()
    if not raw:
        raise LLMExtractError("LLM 返回为空")
    m = _JSON_BLOCK_RE.search(raw)
    candidate = m.group(1) if m else raw
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start:end + 1]
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError) as exc:
        raise LLMExtractError("LLM 返回不是合法 JSON，无法解析") from exc
    if not isinstance(data, dict):
        raise LLMExtractError("LLM 返回不是 JSON 对象")
    title = str(data.get("title") or "").strip()
    if not title:
        raise LLMExtractError("LLM 未能提取出文献标题")
    authors_raw = data.get("authors")
    authors = []
    if isinstance(authors_raw, list):
        authors = [str(a).strip() for a in authors_raw if str(a).strip()]
    elif isinstance(authors_raw, str) and authors_raw.strip():
        # 模型把作者写成逗号/分号分隔字符串时容错拆分
        authors = [a.strip() for a in re.split(r"[;；,，]\s*", authors_raw)
                   if a.strip()]
    year = data.get("year")
    if not isinstance(year, int):
        try:
            year = int(str(year).strip())
        except (TypeError, ValueError):
            year = None
    doi = str(data.get("doi") or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    abstract = data.get("abstract")
    abstract = str(abstract).strip() if abstract else None
    return {
        "title": title,
        "authors": authors,
        "journal": str(data.get("journal") or "").strip(),
        "year": year,
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else None,
        "abstract": abstract or None,
    }


# ---------------------------------------------------------------- 主流程

def draft_from_pdf(pdf_bytes: bytes, pdf_filename: str = "") -> dict:
    """PDF 字节 → 「待审核草稿」（existing 标记由路由层补）。

    依次可能抛：PdfExtractError（400）→ PdfNoTextError（422）→
    LLMNotConfiguredError（503）→ LLMExtractError（502）。
    """
    text = extract_text(pdf_bytes)
    if len(text.strip()) < MIN_TEXT_CHARS:
        raise PdfNoTextError("该 PDF 无文本层（可能是扫描件），请改用 DOI/标题录入")
    if not _llm_configured():
        raise LLMNotConfiguredError("请先在设置页配置 LLM")
    content = _llm_chat(build_prompt(text))
    if content is None:
        raise LLMExtractError("LLM 调用失败（网络或端点异常），请稍后重试")
    draft = parse_llm_json(content)
    draft["source"] = "pdf-llm"
    draft["pdf_filename"] = pdf_filename
    return draft
