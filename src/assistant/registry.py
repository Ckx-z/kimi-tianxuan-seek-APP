"""工具注册表：name → {schema(OpenAI function), handler, confirm?}。

加工具 = 在 TOOLS 加一行（handler 遵守 {text, details, is_error} 契约）。
execute 统一兜底：未知工具 / handler 抛异常都转 is_error 结果，绝不抛出。

写操作二次确认：条目可选 "confirm" 键 —— 字符串（固定影响说明）或
callable(args) -> str | None（动态判定，返回 None 表示本次调用是纯读、
无需确认，如 query_dft 缓存命中）。loop 在执行前调 confirm_impact()，
非 None 则挂起并发 tool_confirm SSE 事件，等用户确认后才真正 execute。
"""

from __future__ import annotations

import logging

from .tools.dft import confirm_impact as _dft_impact
from .tools.dft import query_dft
from .tools.brief import get_daily_brief
from .tools.favorites import list_favorites_tool, manage_favorite, manage_favorite_impact
from .tools.graphrag import query_graphrag_tool
from .tools.history import list_prediction_history
from .tools.plan import generate_plan_card_impact, generate_plan_card_tool
from .tools.predict import predict_film
from .tools.records import (draft_experiment_record,
                            draft_experiment_record_impact,
                            read_experiment_records)
# v1.6.0 P0：联网搜索 / 学术检索 / 网页抓取 / 补齐工具
from .tools.academic import academic_search
from .tools.extra import cas_resolve, get_monomer_props, lookup_paper_doi
from .tools.fetch import fetch_page
from .tools.web import web_search

logger = logging.getLogger(__name__)

_MAX_SUMMARY = 600   # SSE tool_result 摘要限长
_MAX_TEXT = 4000     # 回填给 LLM 的工具结果限长

TOOLS: dict = {
    "predict_film": {
        "handler": lambda args: predict_film(
            args.get("ald_smiles", ""), args.get("amine_smiles", "")),
        "schema": {
            "type": "function",
            "function": {
                "name": "predict_film",
                "description": "对一对醛/胺单体（SMILES）跑成膜打分，返回主分数、"
                               "树/GNN 分量、OOD 标记与打分理由。涉及打分的问题先调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ald_smiles": {"type": "string",
                                       "description": "醛单体 SMILES"},
                        "amine_smiles": {"type": "string",
                                         "description": "胺单体 SMILES"},
                    },
                    "required": ["ald_smiles", "amine_smiles"],
                },
            },
        },
    },
    "query_graphrag": {
        "handler": lambda args: query_graphrag_tool(args.get("question", "")),
        "schema": {
            "type": "function",
            "function": {
                "name": "query_graphrag",
                "description": "在系统知识图谱与本地文献/反馈库中检索证据"
                               "（反应节点、文献节点、历史方案、失败经验）。"
                               "涉及文献、历史经验、概念解释的问题先调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string",
                                     "description": "自由文本检索问题"},
                    },
                    "required": ["question"],
                },
            },
        },
    },
    "read_experiment_records": {
        "handler": lambda args: read_experiment_records(
            args.get("favorite_id") or None),
        "schema": {
            "type": "function",
            "function": {
                "name": "read_experiment_records",
                "description": "读实验记录（时间线、结果、自我总结、本人认为的失误）。"
                               "可传 favorite_id 只看某单体组，不传则看最近记录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "favorite_id": {"type": "string",
                                        "description": "收藏条目 ID（可选）"},
                    },
                    "required": [],
                },
            },
        },
    },
    "list_favorites": {
        "handler": lambda args: list_favorites_tool(
            args.get("folder_id") or None, args.get("limit") or 20),
        "schema": {
            "type": "function",
            "function": {
                "name": "list_favorites",
                "description": "列出收藏夹与收藏条目（可按 folder_id 过滤），"
                               "含最新打分快照与 DFT 快照摘要。涉及“我的收藏"
                               "里有什么”的问题先调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder_id": {"type": "string",
                                      "description": "收藏夹 ID（可选，只看某夹）"},
                        "limit": {"type": "integer",
                                  "description": "最多返回条数（默认 20，上限 30）"},
                    },
                    "required": [],
                },
            },
        },
    },
    "list_prediction_history": {
        "handler": lambda args: list_prediction_history(
            args.get("limit") or 10),
        "schema": {
            "type": "function",
            "function": {
                "name": "list_prediction_history",
                "description": "查询成膜打分历史记录（新→旧），含当时输入单体与"
                               "分数、OOD 标记。涉及“最近打过哪些分”的问题先调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer",
                                  "description": "最多返回条数（默认 10，上限 50）"},
                    },
                    "required": [],
                },
            },
        },
    },
    "manage_favorite": {
        "handler": manage_favorite,
        "confirm": manage_favorite_impact,
        "schema": {
            "type": "function",
            "function": {
                "name": "manage_favorite",
                "description": "【写操作，需用户二次确认】收藏管理：add 把醛/胺"
                               "组合收藏到指定收藏夹（自动附当前打分快照，同组合"
                               "不重复收藏）；move 移动收藏到其他夹；delete 删除"
                               "收藏。调用后系统会向用户弹确认卡，确认后才执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "move", "delete"],
                                   "description": "add=收藏 / move=移夹 / delete=删除"},
                        "ald_smiles": {"type": "string",
                                       "description": "醛单体 SMILES（add 必填）"},
                        "amine_smiles": {"type": "string",
                                         "description": "胺单体 SMILES（add 必填）"},
                        "ald_name": {"type": "string",
                                     "description": "醛单体名称（可选）"},
                        "amine_name": {"type": "string",
                                       "description": "胺单体名称（可选）"},
                        "favorite_id": {"type": "string",
                                        "description": "收藏条目 ID（move/delete 必填）"},
                        "folder_id": {"type": "string",
                                      "description": "目标收藏夹 ID（可选）"},
                        "folder_name": {"type": "string",
                                        "description": "目标收藏夹名称（可选，"
                                                       "不存在时自动新建）"},
                        "notes": {"type": "string",
                                  "description": "收藏备注（add 可选）"},
                    },
                    "required": ["action"],
                },
            },
        },
    },
    "generate_plan_card": {
        "handler": lambda args: generate_plan_card_tool(
            args.get("ald_smiles", ""), args.get("amine_smiles", ""),
            ald_name=args.get("ald_name") or "",
            amine_name=args.get("amine_name") or "",
            template_id=args.get("template_id") or ""),
        "confirm": generate_plan_card_impact,
        "schema": {
            "type": "function",
            "function": {
                "name": "generate_plan_card",
                "description": "【写操作，需用户二次确认】按模板为一对醛/胺单体"
                               "生成实验方案卡（条件、步骤、防错清单、单体提示）"
                               "并保存到方案库。可指定 template_id，缺省用内置"
                               "侯老师法 v3.9。同单体组同模板不重复生成。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ald_smiles": {"type": "string",
                                       "description": "醛单体 SMILES"},
                        "amine_smiles": {"type": "string",
                                         "description": "胺单体 SMILES"},
                        "ald_name": {"type": "string",
                                     "description": "醛单体名称（可选）"},
                        "amine_name": {"type": "string",
                                       "description": "胺单体名称（可选）"},
                        "template_id": {"type": "string",
                                        "description": "方案卡模板 ID（可选）"},
                    },
                    "required": ["ald_smiles", "amine_smiles"],
                },
            },
        },
    },
    "draft_experiment_record": {
        "handler": draft_experiment_record,
        "confirm": draft_experiment_record_impact,
        "schema": {
            "type": "function",
            "function": {
                "name": "draft_experiment_record",
                "description": "【写操作，需用户二次确认】根据对话内容起草实验"
                               "记录，以草稿状态保存（不影响正式统计，用户稍后"
                               "在实验记录页编辑转正）。需要 favorite_id 或醛/胺"
                               "SMILES 至少其一。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "favorite_id": {"type": "string",
                                        "description": "关联的收藏条目 ID（可选）"},
                        "aldehyde_smiles": {"type": "string",
                                            "description": "醛单体 SMILES（游离记录用）"},
                        "amine_smiles": {"type": "string",
                                         "description": "胺单体 SMILES（游离记录用）"},
                        "outcome": {"type": "string",
                                    "enum": ["film", "partial", "failed", ""],
                                    "description": "结果：film 成膜 / partial 部分"
                                                   " / failed 失败，可留空"},
                        "notes": {"type": "string",
                                  "description": "备注 / 现象描述"},
                        "operator": {"type": "string", "description": "操作人"},
                        "experiment_no": {"type": "string",
                                          "description": "实验编号（可留空）"},
                        "conditions": {"type": "object",
                                       "description": "实验条件（solvent_1/"
                                                      "temperature_c 等，可选）"},
                        "self_summary": {"type": "string",
                                         "description": "自我总结（可选）"},
                        "mistakes": {"type": "string",
                                     "description": "本人认为的失误（可选）"},
                    },
                    "required": [],
                },
            },
        },
    },
    "get_daily_brief": {
        "handler": lambda args: get_daily_brief(args.get("date") or None),
        "schema": {
            "type": "function",
            "function": {
                "name": "get_daily_brief",
                "description": "今日科研日报：聚合指定日期（缺省今天）新建/更新的"
                               "实验记录、DFT 计算任务与最佳结合能、新收藏、新录入"
                               "文献。用户问「今天/最近做了什么、今日进展」时先调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string",
                                 "description": "日期 YYYY-MM-DD（可选，缺省今天）"},
                    },
                    "required": [],
                },
            },
        },
    },
    "query_dft": {
        "handler": lambda args: query_dft(
            args.get("smiles_a", ""), args.get("smiles_b", ""),
            args.get("method") or "gfn2"),
        "confirm": _dft_impact,
        "schema": {
            "type": "function",
            "function": {
                "name": "query_dft",
                "description": "查询醛/胺单体缩合二聚体与 X（助手场景固定为"
                               "二聚体自身堆积）的结合能（GFN-FF / GFN2-xTB "
                               "半经验方法，仅供相对比较）。缓存或历史有结果时"
                               "直接返回；否则【写操作，需用户二次确认】提交计算"
                               "任务并等待（gfnff 秒级，gfn2 最长约 60 秒，超时"
                               "转后台并返回任务 ID）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "smiles_a": {"type": "string",
                                     "description": "单体 A SMILES"},
                        "smiles_b": {"type": "string",
                                     "description": "单体 B SMILES"},
                        "method": {"type": "string", "enum": ["gfnff", "gfn2"],
                                   "description": "gfnff 快速 / gfn2 精确（默认 gfn2）"},
                    },
                    "required": ["smiles_a", "smiles_b"],
                },
            },
        },
    },
    # ---------------- v1.6.0 P0：联网 / 学术 / 抓取 / 补齐工具 ----------------
    "web_search": {
        "handler": lambda args: web_search(
            args.get("query", ""), args.get("n") or 5),
        "schema": {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "联网搜索最新公开信息（网页）。涉及“最新进展、"
                               "近两年、新闻、时事”等系统内没有的外部知识时先调它；"
                               "文献类问题优先用 academic_search。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "搜索关键词（英文效果更好）"},
                        "n": {"type": "integer",
                              "description": "返回条数（默认 5，上限 8）"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    "academic_search": {
        "handler": lambda args: academic_search(
            args.get("query", ""), args.get("source") or "all",
            args.get("n") or 5),
        "schema": {
            "type": "function",
            "function": {
                "name": "academic_search",
                "description": "学术文献检索（arXiv / PubMed / Semantic Scholar "
                               "/ Crossref，均免费直连）。涉及论文、文献、方法学"
                               "对比时优先调它；返回带 DOI 的真实文献元数据与"
                               "摘要，可用于进一步 fetch_page 或 lookup_paper_doi。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "检索词（标题/关键词，英文更准）"},
                        "source": {"type": "string",
                                   "enum": ["arxiv", "pubmed",
                                            "semanticscholar", "crossref", "all"],
                                   "description": "检索源（默认 all 聚合）"},
                        "n": {"type": "integer",
                              "description": "每源条数（默认 5，上限 5）"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    "fetch_page": {
        "handler": lambda args: fetch_page(args.get("url", "")),
        "schema": {
            "type": "function",
            "function": {
                "name": "fetch_page",
                "description": "抓取指定网页正文（http/https 公网地址，仅提取"
                               "正文文本）。搜索结果只有摘要不够时，用它读全文；"
                               "纯 JS 渲染页面可能抓不到，可换其他来源。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string",
                                "description": "网页 URL（来自搜索结果等）"},
                    },
                    "required": ["url"],
                },
            },
        },
    },
    "get_monomer_props": {
        "handler": lambda args: get_monomer_props(
            args.get("smiles", ""), args.get("name") or ""),
        "schema": {
            "type": "function",
            "function": {
                "name": "get_monomer_props",
                "description": "单体物化性质（分子量/LogP/TPSA/氢键等 RDKit "
                               "事实 + 可选解读）。涉及单体性质、溶解性推测的"
                               "问题先调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "smiles": {"type": "string", "description": "单体 SMILES"},
                        "name": {"type": "string",
                                 "description": "单体名称（可选）"},
                    },
                    "required": ["smiles"],
                },
            },
        },
    },
    "cas_resolve": {
        "handler": lambda args: cas_resolve(args.get("cas", "")),
        "schema": {
            "type": "function",
            "function": {
                "name": "cas_resolve",
                "description": "CAS 号 → SMILES/名称（内置库→缓存→PubChem→"
                               "LLM 四路）。用户给了 CAS 但没有 SMILES 时先调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cas": {"type": "string", "description": "CAS 号"},
                    },
                    "required": ["cas"],
                },
            },
        },
    },
    "lookup_paper_doi": {
        "handler": lambda args: lookup_paper_doi(args.get("doi", "")),
        "schema": {
            "type": "function",
            "function": {
                "name": "lookup_paper_doi",
                "description": "按 DOI 查文献元数据（本机文献库优先，Crossref "
                               "兜底），返回标题/作者/期刊/年份与可点击 DOI。"
                               "需要核实某篇文献的出处时调它。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doi": {"type": "string",
                                "description": "DOI（形如 10.xxxx/...）"},
                    },
                    "required": ["doi"],
                },
            },
        },
    },
}


def _available_tools() -> dict:
    """按运行环境裁剪工具表：web_search 未开开关/未配 key 时整体缺席。

    其余联网工具（academic_search / fetch_page / lookup_paper_doi）不依赖
    key，始终注册；运行时失败会走 is_error 结果让 LLM 换路。
    """
    tools = dict(TOOLS)
    try:
        from src.llm import client as _llm_client
    except ImportError:  # pragma: no cover
        from llm import client as _llm_client  # type: ignore
    try:
        ok, _reason = _llm_client.web_search_available()
    except Exception:  # 配置读取异常按不可用处理
        ok = False
    if not ok:
        tools.pop("web_search", None)
    return tools


def list_tool_schemas() -> list[dict]:
    """OpenAI tools 参数格式的 schema 列表（路径 A 用；按环境裁剪）。"""
    return [t["schema"] for t in _available_tools().values()]


def describe_tools() -> str:
    """工具清单的自然语言描述（路径 B 两段式提示词内嵌用；按环境裁剪）。"""
    lines = []
    for name, t in _available_tools().items():
        fn = t["schema"]["function"]
        props = fn["parameters"].get("properties", {})
        required = set(fn["parameters"].get("required", []))
        args_desc = ", ".join(
            f"{k}{'（必填）' if k in required else '（可选）'}"
            for k in props) or "无参数"
        lines.append(f"- {name}({args_desc})：{fn['description']}")
    return "\n".join(lines)


def execute(name: str, args: dict | None) -> dict:
    """统一执行入口：参数白名单校验 + 异常兜底，返回 {text, details, is_error}。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"text": f"未知工具：{name}（可用：{'、'.join(TOOLS)}）",
                "details": {}, "is_error": True}
    if not isinstance(args, dict):
        args = {}
    allowed = set(tool["schema"]["function"]["parameters"].get("properties", {}))
    clean_args = {k: v for k, v in args.items() if k in allowed}
    try:
        result = tool["handler"](clean_args)
    except Exception as exc:
        logger.warning("工具 %s 执行异常: %s", name, exc)
        return {"text": f"工具 {name} 执行失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}
    if not isinstance(result, dict) or "text" not in result:
        return {"text": f"工具 {name} 返回格式非法",
                "details": {}, "is_error": True}
    result.setdefault("details", {})
    result["is_error"] = bool(result.get("is_error"))
    if isinstance(result["text"], str) and len(result["text"]) > _MAX_TEXT:
        result["text"] = result["text"][:_MAX_TEXT] + "…（已截断）"
    return result


def summary_of(result: dict) -> str:
    """SSE tool_result 事件的摘要（限长）。"""
    text = str(result.get("text") or "")
    return text if len(text) <= _MAX_SUMMARY else text[:_MAX_SUMMARY] + "…"


def confirm_impact(name: str, args: dict | None) -> str | None:
    """该次工具调用是否需要二次确认；需要时返回影响说明，否则 None。

    "confirm" 键为字符串 → 固定确认；为 callable(args) -> str | None →
    动态判定（None = 本次是纯读路径，不确认）。无 "confirm" 键 → 读工具。
    """
    tool = TOOLS.get(name)
    if tool is None:
        return None
    spec = tool.get("confirm")
    if spec is None:
        return None
    if callable(spec):
        try:
            impact = spec(args if isinstance(args, dict) else {})
        except Exception as exc:
            logger.warning("工具 %s 确认判定异常（按需要确认处理）: %s", name, exc)
            impact = "执行写操作"
        return impact or None
    return str(spec)
