"""助手工具：文献图谱查询（v1.9.8 G1+G2）。

## 为什么需要它

系统里现在有**两套图**：
- `query_graphrag`（原有）：**化学反应网络** —— 单体/溶剂/催化剂/成膜结果 + 随包领域图；
- 本工具：**文献关系图** —— `cites`（Crossref 确证引用）/ `similar_to`（向量近邻，算法推荐）
  + 技术/指标/属性实体 + PageRank/社区/桥梁/时间线等分析指标。

用户最看重的"助手在图谱上查询 + RAG 迭代"就落在这层：有了它，助手才能回答
「和 1529 怎么联系上的」「谁用了 PXRD 且比表面积>2000」「被引最多的是哪几篇」。

## 设计

- **只读**：全部走 `literature.graph_query`（白名单算子 + 只读防护，写操作被拒）；
- **两路输入**：优先用结构化参数（对模型更稳），也接受 `dsl` 直传（高级用法）；
- **来源可解释**：返回里保留 `op` 与边的 `source`（crossref=确证引用 / embedding=算法推荐），
  助手据此在回答里区分"真实引用"与"算法推荐"。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MODES = ("neighbors", "path", "by_entity", "by_metric", "cites_of", "top", "analytics")


def _modules():
    try:
        from literature import graph_analytics, graph_query, library_db
    except ImportError:  # pragma: no cover
        from src.literature import graph_analytics, graph_query, library_db  # type: ignore
    return graph_query, graph_analytics, library_db


def _build_dsl(mode: str, a: dict) -> str:
    """把结构化参数拼成 DSL 文本（与 graph_query.parse 的语法一致）。"""
    if mode == "neighbors":
        pid = a.get("paper_id")
        if not pid:
            raise ValueError("neighbors 需要 paper_id")
        parts = [f"neighbors {pid}"]
        if a.get("depth"):
            parts.append(f"depth={int(a['depth'])}")
        if a.get("predicates"):
            parts.append(f"pred={a['predicates']}")
        if a.get("limit"):
            parts.append(f"limit={int(a['limit'])}")
        return " ".join(parts)
    if mode == "path":
        if not (a.get("from_paper") and a.get("to_paper")):
            raise ValueError("path 需要 from_paper 与 to_paper")
        dsl = f"path {a['from_paper']} {a['to_paper']}"
        if a.get("max_depth"):
            dsl += f" max={int(a['max_depth'])}"
        return dsl
    if mode == "by_entity":
        if not (a.get("kind") and a.get("value")):
            raise ValueError("by_entity 需要 kind（technique|metric|property）与 value")
        dsl = f"by_entity {a['kind']} {a['value']}"
        if a.get("limit"):
            dsl += f" limit={int(a['limit'])}"
        return dsl
    if mode == "by_metric":
        if not a.get("name"):
            raise ValueError("by_metric 需要指标名 name")
        dsl = f"by_metric {a['name']}"
        for k in ("min", "max"):
            if a.get(k) is not None:
                dsl += f" {k}={a[k]}"
        if a.get("limit"):
            dsl += f" limit={int(a['limit'])}"
        return dsl
    if mode == "cites_of":
        if not a.get("paper_id"):
            raise ValueError("cites_of 需要 paper_id")
        dsl = f"cites_of {a['paper_id']}"
        if a.get("direction"):
            dsl += f" dir={a['direction']}"
        if a.get("limit"):
            dsl += f" limit={int(a['limit'])}"
        return dsl
    if mode == "top":
        dsl = f"top {a.get('metric') or 'pagerank'}"
        if a.get("limit"):
            dsl += f" limit={int(a['limit'])}"
        return dsl
    raise ValueError(f"不支持的 mode：{mode}；可用 {', '.join(MODES)}")


def lit_graph_tool(args: dict | None = None) -> dict:
    """文献图谱查询（只读）。args: {mode, dsl?, paper_id?, from_paper?, to_paper?,
    kind?, value?, name?, min?, max?, direction?, metric?, limit?, depth?, predicates?}"""
    a = dict(args or {})
    mode = str(a.get("mode") or "").strip().lower()
    graph_query, graph_analytics, library_db = _modules()
    try:
        library_db.ensure_ready()
        conn = library_db.connect()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"文献库不可用：{type(exc).__name__}: {exc}"}
    try:
        # analytics：直接给分析指标（PageRank / 社区 / 桥梁 / 时间线）
        if mode == "analytics":
            kind = str(a.get("kind") or "pagerank").lower()
            top_k = int(a.get("limit") or 10)
            if kind in ("timeline",):
                return {"mode": mode, **graph_analytics.timeline(conn, limit=top_k)}
            fn = getattr(graph_analytics, kind if kind in
                         ("pagerank", "communities", "bridges") else "pagerank")
            out = fn(conn, top_k=top_k)
            return {"mode": mode, "kind": kind, **out}
        dsl = str(a.get("dsl") or "").strip() or _build_dsl(mode, a)
        out = graph_query.execute(conn, dsl)
        return {"mode": mode, "dsl": dsl, **out}
    except ValueError as exc:
        return {"error": str(exc), "mode": mode, "hint":
                "可用 mode：neighbors / path / by_entity / by_metric / cites_of / top / analytics"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("文献图谱工具失败：%s", exc)
        return {"error": f"查询失败：{type(exc).__name__}: {exc}", "mode": mode}
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lit_graph",
        "description": "查询**文献关系图**（与 query_graphrag 的化学反应网络不同）："
                       "谁引用谁（Crossref 确证引用）、向量相似（算法推荐）、"
                       "谁用了某技术/指标/属性、跨文献数值检索、被引权重排行、"
                       "研究簇、桥梁文献、年份时间线。"
                       "凡涉及文献之间的关系、影响力、某个数值出现在哪些文献，优先调它。",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string",
                         "enum": list(MODES),
                         "description": "neighbors=某文献的引用邻域；path=两篇之间的距离与路径；"
                                        "by_entity=用了某技术/指标/属性的文献；"
                                        "by_metric=跨文献数值检索；cites_of=引用/被引清单；"
                                        "top=指标排行；analytics=分析指标"},
                "paper_id": {"type": "string", "description": "文献编号（neighbors/cites_of 用）"},
                "from_paper": {"type": "string", "description": "path 的起点文献编号"},
                "to_paper": {"type": "string", "description": "path 的终点文献编号"},
                "kind": {"type": "string",
                         "description": "by_entity 的实体类型：technique|metric|property；"
                                        "analytics 时表示 pagerank|communities|bridges|timeline"},
                "value": {"type": "string", "description": "by_entity 的实体名（如 PXRD）"},
                "name": {"type": "string", "description": "by_metric 的指标名（如 比表面积）"},
                "min": {"type": "number", "description": "by_metric 的最小值"},
                "max": {"type": "number", "description": "by_metric 的最大值"},
                "direction": {"type": "string", "enum": ["in", "out", "both"],
                              "description": "cites_of 的方向"},
                "metric": {"type": "string",
                           "description": "top 的指标：pagerank|betweenness|cited_by_count|reference_count"},
                "depth": {"type": "integer", "description": "neighbors 的跳数（1-3）"},
                "predicates": {"type": "string", "description": "neighbors 的关系过滤，如 cites,similar_to"},
                "limit": {"type": "integer", "description": "返回条数上限"},
                "dsl": {"type": "string", "description": "高级：直接给 DSL 文本（如 'top pagerank limit=5'）"},
            },
            "required": ["mode"],
        },
    },
}
