"""采纳建议 → 按模板生成编号实验方案卡（页⑤「方案迭代」任务 B 后端）。

流程（adopt_suggestion）：
1. 读 data/rag_export/suggestions/<suggestion_id>.json（Schema 3）
2. 解析单体对：
   - favorite_id 非空 → 读 data/favorites/<fav>.json 取醛/胺单体
   - favorite_id 为空（游离建议）→ 依次尝试：
     a) payload 里的 aldehyde/amine（new_candidate 型建议自带）
     b) evidence_refs 中 experiment_record 反查 data/rag_export/records/
     都查不到则抛 AdoptError
3. 选模板：template_id 指定 → plan_templates.get_template；
   否则用内置侯老师 v3.9（plan_card 默认行为，向后兼容）
4. 直接调 plan_card.generate_plan_card 生成基础卡；建议 payload.adjustments
   原文列表作为 adjustments_applied 附上（LLM 文本不可靠解析，
   不做智能改 conditions，保留原文最安全）
5. 编号：同一 favorite 的方案版本号 seq 递增（扫描已有方案文件计算；
   favorite_id=null 用全局序号）；plan_id 格式 plan_YYYYMMDD_NNN
6. 落盘 data/generated_plans/<plan_id>.json，并回写建议文件
   status="adopted" + adopted_plan_id
7. 返回完整 plan dict
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from src.recommend import plan_card, plan_templates

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
_UD = runtime_config.user_data_root()
SUGGESTIONS_DIR = _UD / "rag_export" / "suggestions"
RECORDS_DIR = _UD / "rag_export" / "records"
FAVORITES_DIR = _UD / "favorites"
PLANS_DIR = _UD / "generated_plans"


class AdoptError(Exception):
    """采纳流程中的可读错误（建议不存在、缺单体、模板不存在等）。"""


# ---------------------------------------------------------------- 工具

def _read_json(path: Path, what: str) -> dict:
    """读 JSON 文件；不存在/解析失败抛 AdoptError。"""
    if not path.exists():
        raise AdoptError(f"{what}不存在: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AdoptError(f"{what}读取失败: {path.name}: {exc}")
    if not isinstance(data, dict):
        raise AdoptError(f"{what}格式非法（应为 JSON 对象）: {path.name}")
    return data


def _monomer_ok(obj) -> bool:
    """单体对象合法性：dict 且含非空 smiles 字符串。"""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("smiles"), str)
        and bool(obj["smiles"].strip())
    )


# ---------------------------------------------------------------- 单体解析

def _monomers_from_favorite(favorite_id: str) -> tuple[dict, dict]:
    """从收藏条目取醛/胺单体对象。"""
    fav = _read_json(FAVORITES_DIR / f"{favorite_id}.json", "收藏条目")
    ald, amine = fav.get("aldehyde"), fav.get("amine")
    if not (_monomer_ok(ald) and _monomer_ok(amine)):
        raise AdoptError(f"收藏条目缺少醛/胺单体信息: {favorite_id}")
    return ald, amine


def _monomers_from_records(suggestion: dict) -> tuple[dict, dict] | None:
    """从建议的 evidence_refs 反查实验记录中的单体对；查不到返回 None。"""
    for ev in suggestion.get("evidence_refs") or []:
        if not isinstance(ev, dict) or ev.get("kind") != "experiment_record":
            continue
        ref = str(ev.get("ref") or "").strip()
        if not ref:
            continue
        path = RECORDS_DIR / f"{ref}.json"
        if not path.exists():
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("实验记录读取失败 %s: %s", ref, exc)
            continue
        ald, amine = rec.get("aldehyde"), rec.get("amine")
        if _monomer_ok(ald) and _monomer_ok(amine):
            return ald, amine
    return None


def _resolve_monomers(suggestion: dict) -> tuple[dict, dict, str | None]:
    """解析建议对应的醛/胺单体。

    返回 (aldehyde, amine, favorite_id)；favorite_id 可能为 None（游离建议）。
    游离建议依次尝试 payload 自带单体、实验记录反查；都失败抛 AdoptError。
    """
    favorite_id = suggestion.get("favorite_id")
    if favorite_id:
        ald, amine = _monomers_from_favorite(str(favorite_id))
        return ald, amine, str(favorite_id)

    # 游离建议（favorite_id=null）：先看 payload 是否自带单体（new_candidate）
    payload = suggestion.get("payload") or {}
    ald, amine = payload.get("aldehyde"), payload.get("amine")
    if _monomer_ok(ald) and _monomer_ok(amine):
        return ald, amine, None

    # 再从 evidence_refs 反查实验记录
    found = _monomers_from_records(suggestion)
    if found:
        ald, amine = found
        return ald, amine, None

    raise AdoptError(
        f"游离建议 {suggestion.get('suggestion_id', '?')} 无法确定单体对："
        "favorite_id 为空，payload 未携带 aldehyde/amine，"
        "evidence_refs 中也反查不到有效实验记录"
    )


# ---------------------------------------------------------------- 模板

def _resolve_template(template_id: str | None) -> tuple[dict | None, str]:
    """选模板，返回 (template dict 或 None, 模板名称)。

    - template_id 指定：plan_templates.get_template（不存在抛 AdoptError）
    - 未指定：内置侯老师 v3.9；优先读 data/plan_templates/builtin_hou_v3_9.json，
      读不到则 template=None 走 plan_card 内置默认（向后兼容）
    """
    if template_id:
        try:
            tpl = plan_templates.get_template(template_id)
        except plan_templates.TemplateError as exc:
            raise AdoptError(str(exc))
        return tpl, tpl["name"]
    try:
        tpl = plan_templates.get_template(plan_templates.BUILTIN_ID)
        return tpl, tpl["name"]
    except Exception as exc:
        logger.warning("内置模板文件不可用，退回 plan_card 默认模板: %s", exc)
        return None, plan_card.TEMPLATE_NAME


# ---------------------------------------------------------------- 编号

_PLAN_ID_RE = re.compile(r"^plan_(\d{8})_(\d{3})$")


def _load_existing_plans() -> list[dict]:
    """扫描已有方案文件（坏文件跳过）。"""
    plans: list[dict] = []
    if not PLANS_DIR.exists():
        return plans
    for p in sorted(PLANS_DIR.glob("plan_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                plans.append(data)
        except Exception as exc:
            logger.warning("跳过非法方案文件 %s: %s", p.name, exc)
    return plans


def _next_seq(plans: list[dict], favorite_id: str | None) -> int:
    """版本号：同一 favorite 内 seq 递增；favorite_id=null 用全局序号。"""
    seqs: list[int] = []
    for plan in plans:
        if favorite_id is None:
            if plan.get("favorite_id") in (None, ""):
                seqs.append(int(plan.get("seq") or 0))
        elif plan.get("favorite_id") == favorite_id:
            seqs.append(int(plan.get("seq") or 0))
    return (max(seqs) + 1) if seqs else 1


def _next_plan_id(plans: list[dict], date_str: str) -> str:
    """plan_id：plan_YYYYMMDD_NNN，当日序号递增（全局扫描，防撞号）。"""
    max_n = 0
    for plan in plans:
        m = _PLAN_ID_RE.match(str(plan.get("plan_id") or ""))
        if m and m.group(1) == date_str:
            max_n = max(max_n, int(m.group(2)))
    return f"plan_{date_str}_{max_n + 1:03d}"


# ---------------------------------------------------------------- 调整提取

def _extract_adjustments(suggestion: dict) -> list[dict]:
    """取建议 payload.adjustments 原文列表（不可靠解析，原样保留）。

    统一规范为 dict 列表；非 dict 项包成 {"note": 原文}。
    """
    payload = suggestion.get("payload") or {}
    raw = payload.get("adjustments") or []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(dict(item))
        else:
            out.append({"note": str(item)})
    return out


# ---------------------------------------------------------------- 主入口

def adopt_suggestion(suggestion_id: str, template_id: str | None = None) -> dict:
    """采纳一条迭代建议，按模板生成编号实验方案卡。

    参数：
        suggestion_id: 建议主键（sug_YYYYMMDD_NNN）
        template_id: 模板 id；None 用内置侯老师 v3.9 默认

    返回 plan dict：{plan_id, seq, favorite_id, suggestion_id, template_name,
    plan_card, adjustments_applied, created_at}；同时落盘
    data/generated_plans/<plan_id>.json 并回写建议 status="adopted"。
    """
    suggestion_id = (suggestion_id or "").strip()
    if not suggestion_id:
        raise AdoptError("suggestion_id 不能为空")
    safe_id = re.sub(r"[^A-Za-z0-9_\-]", "_", suggestion_id)
    suggestion = _read_json(SUGGESTIONS_DIR / f"{safe_id}.json", "建议")

    # 幂等保护：已采纳过的建议直接返回已有 plan，不重复生成
    if suggestion.get("status") == "adopted":
        old_plan_id = str(suggestion.get("adopted_plan_id") or "").strip()
        if old_plan_id:
            safe_plan_id = re.sub(r"[^A-Za-z0-9_\-]", "_", old_plan_id)
            old_path = PLANS_DIR / f"{safe_plan_id}.json"
            if old_path.exists():
                try:
                    plan = json.loads(old_path.read_text(encoding="utf-8"))
                    if isinstance(plan, dict):
                        logger.info(
                            "建议 %s 已采纳，直接返回已有方案 %s",
                            suggestion_id, old_plan_id,
                        )
                        return plan
                except Exception as exc:
                    # 旧 plan 文件损坏：降级为重新生成
                    logger.warning(
                        "已有方案 %s 读取失败，改为重新生成: %s",
                        old_plan_id, exc,
                    )
            else:
                # 找不到旧 plan 文件：重新生成（仍会回写 adopted_plan_id）
                logger.warning(
                    "建议 %s 标记为已采纳但方案文件 %s 不存在，重新生成",
                    suggestion_id, old_plan_id,
                )

    ald, amine, favorite_id = _resolve_monomers(suggestion)
    template, template_name = _resolve_template(template_id)

    # 生成基础方案卡（不智能改 conditions，adjustments 原文另附）
    card = plan_card.generate_plan_card(
        aldehyde_smiles=ald["smiles"],
        amine_smiles=amine["smiles"],
        ald_name=str(ald.get("name") or ""),
        amine_name=str(amine.get("name") or ""),
        template=template,
    )

    existing = _load_existing_plans()
    now = datetime.now().astimezone()
    plan = {
        "plan_id": _next_plan_id(existing, now.strftime("%Y%m%d")),
        "seq": _next_seq(existing, favorite_id),
        "favorite_id": favorite_id,
        "suggestion_id": suggestion.get("suggestion_id") or suggestion_id,
        "template_name": template_name,
        "plan_card": card,
        "adjustments_applied": _extract_adjustments(suggestion),
        "created_at": now.isoformat(timespec="seconds"),
    }

    # 落盘方案
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    (PLANS_DIR / f"{plan['plan_id']}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 回写建议状态（App 侧唯一允许改 suggestions 的场景）
    suggestion["status"] = "adopted"
    suggestion["adopted_plan_id"] = plan["plan_id"]
    (SUGGESTIONS_DIR / f"{safe_id}.json").write_text(
        json.dumps(suggestion, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "采纳建议 %s → 方案 %s（seq=%d, favorite=%s）",
        suggestion_id, plan["plan_id"], plan["seq"], favorite_id,
    )
    return plan
