"""query_graphrag 工具：主进程内走 minimax 检索链（只读调用，不改其核心逻辑）。

两路证据（各自独立降级，一路失败不拖垮另一路）：
1. search_local_pdfs 五路召回（反馈库 / 历史方案 / embedding / tianxuan /
   本地 PDF 文件名），format_results_for_prompt 格式化；
2. query_graphrag 图检索（graph_v2 优先 + 用户实验侧车图合并），取 top
   reaction / literature 节点做摘要。
图资产缺失、缺 networkx、embedding 不可用等情况按 minimax 既有语义静默
降级；两路都失败才返回 is_error。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

_MAX_TEXT = 3000  # 工具返回 text 限长（token 成本控制，方案 §8.2）


def _bootstrap_bridge_path() -> Path:
    """把 minimax/bridge 加进 sys.path（与 iterate_suggest.py 的引导一致）。"""
    bridge = runtime_config.resource_root() / "minimax" / "bridge"
    p = str(bridge)
    if p not in sys.path:
        sys.path.insert(0, p)
    return bridge


def _local_search_block(question: str) -> str:
    """五路召回块；整体失败返回空串（由调用方决定是否报错）。"""
    import search_local_pdfs  # minimax/bridge 下，经 _bootstrap_bridge_path

    results = search_local_pdfs.search({
        "aldehyde_cas": None,
        "amine_cas": None,
        "keywords": [question],
        "query_text": question,
        "max_pdf_results": 5,
        "top_k_embedding": 5,
    })
    return search_local_pdfs.format_results_for_prompt(results)


def _graph_block(question: str) -> str:
    """GraphRAG 图检索块；失败返回空串。"""
    import query_graphrag  # minimax/bridge 下

    G = query_graphrag.load_graph(app_root=str(runtime_config.user_app_root()))
    gres = query_graphrag.query(question, G=G)
    lines: list[str] = []
    reactions = (gres or {}).get("reactions") or []
    literatures = (gres or {}).get("literatures") or []
    if reactions:
        lines.append("## 图谱反应节点命中")
        for hit in reactions[:5]:
            d = hit.get("data") or {}
            desc = " ".join(str(d.get(k, "")) for k in
                            ("aldehyde", "amine", "solvent", "temperature",
                             "product", "outcome")).strip()
            lines.append(f"- [{hit.get('id')}] {desc[:160]}")
    if literatures:
        lines.append("## 图谱文献节点命中")
        for hit in literatures[:5]:
            d = hit.get("data") or {}
            title = d.get("title") or d.get("innovation") or ""
            lines.append(f"- [{hit.get('id')}] {str(title)[:160]}")
    return "\n".join(lines)


def query_graphrag_tool(question: str) -> dict:
    """GraphRAG / 本地证据检索。question 为自由文本问题。"""
    question = (question or "").strip()
    if not question:
        return {"text": "参数缺失：question 不能为空",
                "details": {}, "is_error": True}

    _bootstrap_bridge_path()
    blocks: list[str] = []
    failures: list[str] = []

    try:
        local = _local_search_block(question)
        if local and local != "(无匹配)":
            blocks.append(local)
    except Exception as exc:
        logger.info("query_graphrag 本地召回降级: %s", exc)
        failures.append(f"本地五路召回失败: {type(exc).__name__}: {exc}")

    try:
        graph = _graph_block(question)
        if graph:
            blocks.append(graph)
    except Exception as exc:
        logger.info("query_graphrag 图检索降级: %s", exc)
        failures.append(f"GraphRAG 图检索失败: {type(exc).__name__}: {exc}")

    if not blocks and failures:
        return {"text": "检索失败：" + "；".join(failures),
                "details": {"failures": failures}, "is_error": True}
    if not blocks:
        return {"text": "系统内未查到相关证据（五路召回与图谱检索均无命中）。",
                "details": {"hits": 0}, "is_error": False}

    text = "\n\n".join(blocks)
    return {"text": text[:_MAX_TEXT],
            "details": {"hits": len(blocks), "failures": failures},
            "is_error": False}
