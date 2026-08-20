"""工具注册表：name → {schema(OpenAI function), handler}。

加工具 = 在 TOOLS 加一行（handler 遵守 {text, details, is_error} 契约）。
execute 统一兜底：未知工具 / handler 抛异常都转 is_error 结果，绝不抛出。
"""

from __future__ import annotations

import logging

from .tools.graphrag import query_graphrag_tool
from .tools.predict import predict_film
from .tools.records import read_experiment_records

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
}


def list_tool_schemas() -> list[dict]:
    """OpenAI tools 参数格式的 schema 列表（路径 A 用）。"""
    return [t["schema"] for t in TOOLS.values()]


def describe_tools() -> str:
    """工具清单的自然语言描述（路径 B 两段式提示词内嵌用）。"""
    lines = []
    for name, t in TOOLS.items():
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
