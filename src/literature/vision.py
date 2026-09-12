"""视觉读图（v1.9.4 方案 B，可选）：把文献图交给视觉模型读出描述与数值。

定位：**方案 A（本地抽图入库）是保底**——不配视觉模型时功能照常，图仍然会抽出来、
入库、可看图；本模块只在用户在「设置 → 文献解析 LLM → 视觉读图」显式开启且配置了
支持视觉的模型后生效，用于把图里的数值（PXRD 峰位、比表面积、截留率…）转成可入库的
结构化条目，避免只靠正文文字。

配置回退规则：`vision_base_url / vision_api_key / vision_model` 留空时分别回退到
主解析 LLM 的同名字段（同一端点+key 支持视觉时无需重复填写）。

安全与诚实性：
- 提示词明确要求「只读图中确实可见的数值，不确定不要写」；
- 结果带 `confidence`（模型自评 high/medium/low）与 `notes`，入库条目的 evidence 会标注
  「视觉读图」来源，便于人工复核；
- 任何失败（未启用/网络/解析）都返回结构化错误，不抛异常、不影响其它流程。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.request

try:
    from literature import llm_extract          # 路由/测试使用裸名实例（优先）
except ImportError:  # pragma: no cover
    from src.literature import llm_extract  # type: ignore

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024     # 单图上限（base64 后约 6.7MB，多数端点可接受）
DEFAULT_MAX_TOKENS = 16000            # 推理型视觉模型要留足思考预算（同文本解析口径）
MAX_METRICS_PER_FIGURE = 8

_PROMPT = (
    "你是科研文献图表分析助手。请阅读这张来自 COF（共价有机框架）文献的图，"
    "只输出一个 JSON 对象（不要 markdown 围栏、不要多余文字）：\n"
    "{\n"
    '  "figure_type": "spectra|structure|mechanism",\n'
    '  "technique": "PXRD|FTIR|BET|SEM|TEM|AFM|PL|UVVis|NMR|TGA|XPS|contact_angle|'
    'separation_flux|separation_selectivity|mechanical|photocatalysis|electrochem|dft|",\n'
    '  "description": "一句话说明这张图画的是什么（中文，≤120字）",\n'
    '  "metrics": [{"name": "指标名（如 2θ峰位/比表面积/截留率/带隙）", '
    '"value": 数值, "unit": "单位"}],\n'
    '  "confidence": "high|medium|low",\n'
    '  "notes": "读数依据或不确认之处（中文，≤80字）"\n'
    "}\n"
    "规则：1) **只写图中确实可读到的数值**，读不准的宁可不写（metrics 可为空数组）；"
    "2) 不要根据常识补数值；3) 若图中没有定量信息，只给出 description；"
    "4) technique 必须是上面列出的规范写法之一，无法判断就留空字符串。"
)


def resolve_config() -> dict:
    """解析视觉模型配置（vision_* 留空回退主 LLM 同名字段）。"""
    s = llm_extract._read_settings()
    base_url = str(s.get("vision_base_url") or s.get("base_url") or "").strip()
    api_key = str(s.get("vision_api_key") or s.get("api_key") or "").strip()
    model = str(s.get("vision_model") or "").strip()
    return {
        "enabled": bool(s.get("vision_enabled")),
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "inherits_main": not str(s.get("vision_base_url") or "").strip()
        and not str(s.get("vision_api_key") or "").strip(),
    }


def is_enabled() -> bool:
    """视觉读图是否可用（开关 + 端点 + key + 模型齐备）。"""
    cfg = resolve_config()
    return bool(cfg["enabled"] and cfg["base_url"] and cfg["api_key"]
                and cfg["model"])


def status() -> dict:
    """给设置页/前端的状态（不含 key 明文）。"""
    cfg = resolve_config()
    return {
        "enabled": bool(cfg["enabled"]),
        "available": is_enabled(),
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "inherits_main": cfg["inherits_main"],
    }


# ---------------------------------------------------------------- 调用

def _parse_json_object(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _chat_vision(base_url: str, api_key: str, model: str, data_url: str,
                 hint: str, max_tokens: int) -> tuple[str | None, dict]:
    """一次视觉调用；返回 (content|None, info)。content 为空时自动扩大预算重试。"""
    prompt = _PROMPT if not hint else f"{_PROMPT}\n\n补充提示：{hint}"
    budget = int(max_tokens)
    info: dict = {"max_tokens": budget}
    for _ in range(3):
        body = json.dumps({
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            "max_tokens": budget,
            "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
            return None, info
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = str(msg.get("content") or "").strip()
        details = (data.get("usage") or {}).get("completion_tokens_details") or {}
        info.update({"finish_reason": choice.get("finish_reason"),
                     "reasoning_tokens": details.get("reasoning_tokens") or 0,
                     "max_tokens": budget})
        if content:
            return content, info
        if budget >= 32000:
            break
        budget = min(budget * 2, 32000)      # 推理型模型预算吃满 → 扩容重试
    info["error"] = info.get("error") or "模型未返回内容（可能不支持图片输入）"
    return None, info


def analyze_image(data: bytes, mime: str = "image/png", hint: str = "",
                  max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    """单图分析 → {ok, description, technique, metrics, confidence, notes, error}。"""
    if not is_enabled():
        return {"ok": False, "error": "视觉读图未启用或配置不完整", "metrics": [],
                "description": "", "technique": "", "confidence": "", "notes": ""}
    if not data:
        return {"ok": False, "error": "图片为空", "metrics": [], "description": "",
                "technique": "", "confidence": "", "notes": ""}
    if len(data) > MAX_IMAGE_BYTES:
        return {"ok": False, "error": f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB，"
                                     "未送入视觉模型", "metrics": [],
                "description": "", "technique": "", "confidence": "", "notes": ""}
    cfg = resolve_config()
    b64 = base64.b64encode(data).decode("ascii")
    data_url = f"data:{mime or 'image/png'};base64,{b64}"
    content, info = _chat_vision(cfg["base_url"], cfg["api_key"], cfg["model"],
                                 data_url, hint, max_tokens)
    if content is None:
        return {"ok": False, "error": info.get("error") or "视觉调用失败",
                "metrics": [], "description": "", "technique": "",
                "confidence": "", "notes": ""}
    obj = _parse_json_object(content)
    if obj is None:
        return {"ok": False, "error": "视觉模型返回无法解析（非 JSON）",
                "metrics": [], "description": content[:200], "technique": "",
                "confidence": "", "notes": "", "raw": content[:600]}
    metrics: list[dict] = []
    for m in (obj.get("metrics") or [])[:MAX_METRICS_PER_FIGURE]:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        try:
            value = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        metrics.append({"name": name, "value": value,
                        "unit": str(m.get("unit") or "").strip()})
    return {
        "ok": True,
        "error": None,
        "figure_type": str(obj.get("figure_type") or "").strip().lower(),
        "technique": str(obj.get("technique") or "").strip(),
        "description": str(obj.get("description") or "").strip()[:300],
        "metrics": metrics,
        "confidence": str(obj.get("confidence") or "").strip().lower(),
        "notes": str(obj.get("notes") or "").strip()[:200],
        "budget_expanded": bool(info.get("max_tokens", 0) > max_tokens),
    }


# ---------------------------------------------------------------- 转成可入库条目

def propose_entry(figure_meta: dict, analysis: dict) -> dict | None:
    """单图分析结果 → 可入库条目（characterization 优先，退化 conclusion）。

    - technique 规范可识别且有 metrics → characterization
    - 其余（无数值 / 技术不可识别）→ conclusion（保留描述，不丢信息）
    - evidence 明确标注「视觉读图 + 图号」，便于人工复核
    """
    if not analysis.get("ok"):
        return None
    try:
        from literature import knowledge          # 裸名优先（与路由/测试一致）
    except ImportError:  # pragma: no cover
        from src.literature import knowledge  # type: ignore

    label = str(figure_meta.get("caption_label") or "").strip()
    page = figure_meta.get("page")
    caption = str(figure_meta.get("caption") or "").strip()
    desc = str(analysis.get("description") or "").strip()
    notes = str(analysis.get("notes") or "").strip()
    metrics = analysis.get("metrics") or []
    group_id = f"FIG-{label.replace(' ', '')}" if label else f"FIG-p{page or '?'}"
    evidence = "；".join(x for x in [
        f"视觉读图（{label or ('第 %s 页' % page)}）",
        desc,
        notes,
    ] if x)[:400]
    technique = knowledge.normalize_technique(analysis.get("technique") or "")
    if metrics and technique:
        return {
            "kind": "characterization",
            "group_id": group_id,
            "technique": technique,
            "sample": "",
            "metrics": metrics,
            "conclusion": desc,
            "evidence": evidence,
            "source_file": f"[图 {label}]" if label else f"[图 p{page}]",
            "source": "vision_extract",
        }
    if desc:
        return {
            "kind": "conclusion",
            "group_id": group_id,
            "conclusion": desc[:300],
            "evidence": evidence,
            "source_file": f"[图 {label}]" if label else f"[图 p{page}]",
            "source": "vision_extract",
        }
    return None
