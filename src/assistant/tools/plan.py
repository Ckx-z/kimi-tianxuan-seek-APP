"""generate_plan_card 工具：按模板生成实验方案卡并落盘方案库（写，需二次确认）。

复用 src/recommend/plan_card 生成管线与 generated_plans 的编号/落盘口径
（plan_YYYYMMDD_NNN，存 user_data_root()/generated_plans/）。幂等：同单体对
+ 同模板已生成过方案卡时直接返回已有 plan_id，不重复落盘。
"""

from __future__ import annotations

import json
from datetime import datetime

try:
    from src.favorites import store as fav_store
    from src.recommend import generated_plans, plan_card, plan_templates
except ImportError:  # pragma: no cover
    from favorites import store as fav_store  # type: ignore
    from recommend import generated_plans, plan_card, plan_templates  # type: ignore


def _canon_pair(ald: str, amine: str) -> tuple[str | None, str | None]:
    return fav_store._canonical(ald), fav_store._canonical(amine)


def _find_existing(canon_ald: str | None, canon_amine: str | None,
                   template_name: str, plans: list[dict]) -> dict | None:
    """按规范化单体对 + 模板名查已有方案卡（幂等去重）。"""
    if not canon_ald or not canon_amine:
        return None
    for plan in plans:
        card = plan.get("plan_card") or {}
        pa = fav_store._canonical(
            str((card.get("aldehyde") or {}).get("smiles") or ""))
        pm = fav_store._canonical(
            str((card.get("amine") or {}).get("smiles") or ""))
        if pa == canon_ald and pm == canon_amine \
                and str(plan.get("template_name") or "") == template_name:
            return plan
    return None


def generate_plan_card_tool(ald_smiles: str, amine_smiles: str,
                            ald_name: str = "", amine_name: str = "",
                            template_id: str = "") -> dict:
    """生成方案卡并保存。返回 text（含 plan_id 与条件要点）+ details。"""
    ald = (ald_smiles or "").strip()
    amine = (amine_smiles or "").strip()
    if not ald or not amine:
        return {"text": "参数缺失：ald_smiles 与 amine_smiles 均不能为空",
                "details": {}, "is_error": True}

    # 模板解析：未指定 → 内置侯老师 v3.9（与 generated_plans 同口径）
    template_id = (template_id or "").strip()
    try:
        if template_id:
            template = plan_templates.get_template(template_id)
            template_name = str(template.get("name") or template_id)
        else:
            try:
                template = plan_templates.get_template(plan_templates.BUILTIN_ID)
                template_name = str(template.get("name") or plan_card.TEMPLATE_NAME)
            except Exception:
                template = None
                template_name = plan_card.TEMPLATE_NAME
    except Exception:
        return {"text": f"模板不存在：{template_id}（可用模板见方案卡页）",
                "details": {}, "is_error": True}

    try:
        plans = generated_plans._load_existing_plans()
        canon_ald, canon_amine = _canon_pair(ald, amine)
        existing = _find_existing(canon_ald, canon_amine, template_name, plans)
        if existing is not None:
            return {"text": f"该单体组 + 模板「{template_name}」的方案卡已存在"
                            f"（{existing.get('plan_id')}），未重复生成。",
                    "details": {"plan_id": existing.get("plan_id"),
                                "template": template_name,
                                "deduplicated": True},
                    "is_error": False}

        card = plan_card.generate_plan_card(
            ald, amine, ald_name=ald_name.strip(),
            amine_name=amine_name.strip(), template=template)

        # 关联收藏（有则记 favorite_id，版本号在其内递增）
        fav = fav_store.find_favorite_by_pair(ald, amine)
        favorite_id = str(fav.get("id")) if fav else None
        date_str = datetime.now().strftime("%Y%m%d")
        plan = {
            "plan_id": generated_plans._next_plan_id(plans, date_str),
            "seq": generated_plans._next_seq(plans, favorite_id),
            "favorite_id": favorite_id,
            "suggestion_id": None,
            "source": "assistant",
            "template_name": template_name,
            "plan_card": card,
            "adjustments_applied": [],
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        generated_plans.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        (generated_plans.PLANS_DIR / f"{plan['plan_id']}.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    except Exception as exc:
        return {"text": f"方案卡生成失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    cond = card.get("conditions") or {}
    cond_bits = [f"{k}={v}" for k, v in list(cond.items())[:6] if str(v).strip()]
    hints = card.get("monomer_hints") or []
    lines = [
        f"已生成实验方案卡 {plan['plan_id']}（模板：{template_name}，"
        f"版本 seq={plan['seq']}），已保存到方案库。",
        f"- 单体：醛 {card.get('aldehyde', {}).get('name') or ald}"
        f" / 胺 {card.get('amine', {}).get('name') or amine}",
    ]
    if cond_bits:
        lines.append("- 默认条件：" + "；".join(cond_bits)
                     + f"（{card.get('defaults_note') or '模板默认值'}）")
    lines.append(f"- 操作步骤 {len(card.get('steps') or [])} 步；"
                 f"防错清单 {len(card.get('checklist') or [])} 项；"
                 f"单体特异提示 {len(hints)} 条")
    if hints:
        for h in hints[:3]:
            lines.append(f"  · {str(h)[:100]}")
    if favorite_id:
        lines.append(f"- 已关联收藏 {favorite_id}")
    return {
        "text": "\n".join(lines),
        "details": {"plan_id": plan["plan_id"], "seq": plan["seq"],
                    "template": template_name, "favorite_id": favorite_id},
        "is_error": False,
    }


def generate_plan_card_impact(args: dict) -> str:
    args = args if isinstance(args, dict) else {}
    tpl = args.get("template_id") or "内置侯老师法 v3.9"
    return (f"将按模板「{tpl}」为该单体组生成实验方案卡并保存到方案库；"
            "同单体组同模板已生成过时不会重复创建。")
