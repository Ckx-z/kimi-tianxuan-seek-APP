"""方案卡「真实用量」生成（v1.9.9 F3.2）。

## 解决什么问题

诊断结论：`plan_card.py` 是**纯模板**（不调 LLM、**没有用量**），真实用量只存在于
`generated_plans.py` 的 `adjustments_applied`，**从未回填到 `conditions`** ——
于是用户看到的方案卡"没有任何作用"。

## 本模块做什么

1. **`apply_dosages(card, amounts, source)`**：把用量（醛/胺比、溶剂、催化剂当量、温度/时间…）
   **写进 `conditions`**，并标注来源 `dosage_source ∈ {discussed, default, literature}`。
   这是本模块的核心 —— 纯函数、可测、不动既有链路。
2. **`propose_dosages(...)`**：用 LLM 在「单体对 + 目标 + **用户已确认的用量**」约束下，
   为每个步骤补齐用量与依据；**JSON 解析失败即降级**（返回 {}，由调用方走模板默认），
   绝不因 LLM 失败而让方案卡生成中断。
3. **`generate_card_with_dosages(...)`**：组合上面两步 → 返回卡片 + `dosage_source`。

设计原则（与文献解析一致）：**用户确认的用量优先、LLM 只提议、失败可降级、来源可追溯**。
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

DOSAGE_KEYS = (
    "aldehyde_amine_ratio", "ald_amine_ratio", "ratio",      # 醛/胺比
    "solvent", "solvent_ratio", "catalyst", "catalyst_eq",   # 溶剂/催化剂当量
    "temperature", "time", "concentration", "modulator",     # 温度/时间/浓度/调节剂
)


def _chat(messages, **kw):  # pragma: no cover - 真实调用在测试中被注入
    try:
        from literature import reading_llm
    except ImportError:
        from src.literature import reading_llm  # type: ignore
    return reading_llm._chat(messages, **kw)


def apply_dosages(card: dict, amounts: dict | None, *,
                  source: str = "discussed") -> dict:
    """把用量写进 `conditions` 并标注来源（**核心修复**）。

    - `amounts` 为空 → 记 `dosage_source='default'`（沿用模板默认），不改动 conditions；
    - 非空 → 逐项合并进 `conditions`（同键覆盖，其余保留），`dosage_source=source`；
    - 同时在卡片顶层写 `dosage_source` 便于前端显示"用量来源"。
    """
    out = dict(card or {})
    conditions = dict(out.get("conditions") or {})
    clean = {str(k): v for k, v in (amounts or {}).items()
             if v is not None and str(v).strip() != ""}
    if clean:
        conditions.update(clean)
        out["conditions"] = conditions
        out["dosage_source"] = source
        out["dosage_fields"] = sorted(clean.keys())
    else:
        out.setdefault("conditions", conditions)
        out["dosage_source"] = "default"
        out["dosage_fields"] = []
    return out


def propose_dosages(monomers: dict | None, goal: str = "", *,
                    amounts: dict | None = None, steps: list | None = None,
                    chat=None, max_tokens: int = 900) -> dict:
    """让 LLM 在"用户已确认用量"的约束下补齐每步用量与依据。

    返回 {"amounts": {...}, "rationale": "...", "ok": bool}；
    **任何失败都返回 ok=False 的空结果**，由调用方降级到模板（不抛异常）。
    """
    chat = chat or _chat
    fixed = {k: v for k, v in (amounts or {}).items() if v not in (None, "")}
    step_brief = []
    for s in (steps or [])[:8]:
        if isinstance(s, dict):
            step_brief.append(str(s.get("title") or s.get("name") or s)[:60])
        else:
            step_brief.append(str(s)[:60])
    prompt = (
        "你是 COF 成膜实验设计助手。请为下面的合成/成膜方案补齐**可执行的用量**，"
        "并对每项给一句依据（可引用文献或经验）。\n\n"
        f"单体信息：{json.dumps(monomers or {}, ensure_ascii=False)}\n"
        f"目标：{goal or '（未指定，按常规成膜优化）'}\n"
        f"步骤：{json.dumps(step_brief, ensure_ascii=False)}\n"
        f"**用户已确认、不得改动的用量**：{json.dumps(fixed, ensure_ascii=False)}\n\n"
        "只输出 JSON：{\"amounts\": {键: 值}, \"rationale\": \"依据说明\"}；"
        "键用英文（如 aldehyde_amine_ratio / solvent / catalyst_eq / temperature / time / "
        "concentration）；值用带单位的字符串（如 \"1:1.2\"、\"90 °C\"、\"72 h\"）。"
        "已有用户确认的项必须原样保留。不要输出任何解释文字。")
    try:
        reply = chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001
        logger.info("用量提议失败（降级为模板默认）：%s", exc)
        return {"amounts": {}, "rationale": "", "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    text = str(reply or "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"amounts": {}, "rationale": "", "ok": False, "error": "返回中未找到 JSON"}
    try:
        data = json.loads(m.group(0))
    except Exception as exc:  # noqa: BLE001
        return {"amounts": {}, "rationale": "", "ok": False,
                "error": f"JSON 解析失败：{exc}"}
    amounts_out = data.get("amounts") if isinstance(data.get("amounts"), dict) else {}
    # 用户确认项**始终胜出**（LLM 不得覆盖）
    merged = {**{k: v for k, v in amounts_out.items() if v not in (None, "")}, **fixed}
    return {"amounts": merged, "rationale": str(data.get("rationale") or "")[:800],
            "ok": bool(merged)}


def generate_card_with_dosages(base_card: dict, *, monomers: dict | None = None,
                               goal: str = "", amounts: dict | None = None,
                               steps: list | None = None, chat=None) -> dict:
    """在模板卡基础上生成**带用量的方案卡**（LLM 失败即降级，不中断）。"""
    card = dict(base_card or {})
    steps = steps if steps is not None else list(card.get("steps") or [])
    if amounts:                                   # 用户已确认 → 直接用，不调 LLM
        out = apply_dosages(card, amounts, source="discussed")
        out["dosage_rationale"] = "用户与助手讨论确认"
        return out
    prop = propose_dosages(monomers, goal, amounts=amounts, steps=steps, chat=chat)
    if prop.get("ok"):
        out = apply_dosages(card, prop["amounts"], source="literature")
        out["dosage_rationale"] = prop.get("rationale") or ""
        return out
    out = apply_dosages(card, None, source="default")
    out["dosage_rationale"] = "未取得模型建议，沿用模板默认（可手动填写）"
    return out
