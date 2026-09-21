"""受限图查询 DSL（v1.9.8 C4）：白名单算子 + 只读 + 可选自然语言翻译。

## 为什么是「受限 DSL」而不是 Cypher

调研结论明确：**Kuzu 仓库已 archived**（社区在迁移），**Neo4j 对单机桌面是 JVM + 进程 +
GPLv3 三重负担**。所以这里不引入图数据库，而是在 SQLite（递归 CTE / 索引查询）之上提供
**一组白名单算子**，并用极小的查询语法把它们串起来：

| 算子 | 语法 | 作用 |
|---|---|---|
| `neighbors` | `neighbors 1529 depth=2 pred=cites,similar_to` | 某文献的 N 跳邻域 |
| `path` | `path 1529 1781 max=4` | 两篇文献之间的最短关系路径 |
| `by_entity` | `by_entity technique PXRD limit=20` | 用了某技术/指标/属性的文献 |
| `by_metric` | `by_metric 比表面积 min=2000 limit=20` | 跨文献数值检索 |
| `cites_of` | `cites_of 1529 dir=in limit=30` | 引用 / 被引列表 |
| `top` | `top pagerank limit=10` | 指标排行（PageRank / 被引量…） |

**安全**：只读（全部走 SELECT）、算子白名单、参数按类型校验、`depth/limit` 有硬上限；
解析失败给出可操作提示。另提供 `translate_nl()`：**只允许**把自然语言翻成上述算子之一
（要求模型只输出 DSL 一行），非法输出直接拒绝 —— 对应 lit-monitor 的「自然语言→只读查询」。
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

OPS = ("neighbors", "path", "by_entity", "by_metric", "cites_of", "top")
ENTITY_KINDS = ("technique", "metric", "property")
TOP_METRICS = ("pagerank", "betweenness", "cited_by_count", "reference_count")
PRED_ALIASES = {"cite": "cites", "cites": "cites", "sim": "similar_to",
                "similar": "similar_to", "similar_to": "similar_to"}

MAX_DEPTH = 3
MAX_LIMIT = 200
MAX_PATH = 5


def _lib():
    try:
        from literature import library_db
    except ImportError:  # pragma: no cover
        from src.literature import library_db  # type: ignore
    return library_db


# ---------------------------------------------------------------- 解析

def parse(query: str) -> dict:
    """把 DSL 文本解析成 {'op':..., 参数...}；非法输入抛 ValueError（附用法）。"""
    text = (query or "").strip()
    if not text:
        raise ValueError("查询为空。用法示例：neighbors 1529 depth=2")
    parts = text.split()
    op = parts[0].lower()
    if op not in OPS:
        raise ValueError(f"不支持的算子 {op!r}；可用：{', '.join(OPS)}")
    args: dict = {}
    positional: list[str] = []
    for tok in parts[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            args[k.strip().lower()] = v.strip()
        else:
            positional.append(tok)

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        raw = args.pop(key, None)
        if raw is None:
            return default
        try:
            val = int(raw)
        except ValueError:
            raise ValueError(f"{key} 必须是整数，收到 {raw!r}") from None
        return max(lo, min(hi, val))

    out: dict = {"op": op, "raw": text}
    if op == "neighbors":
        if not positional:
            raise ValueError("neighbors 需要 paper_id，例如：neighbors 1529 depth=2")
        out["paper_id"] = positional[0]
        out["depth"] = _int("depth", 1, 1, MAX_DEPTH)
        preds = args.pop("pred", None) or args.pop("predicates", None)
        if preds:
            resolved = []
            for p in re.split(r"[,\s]+", preds):
                if p:
                    if p.lower() not in PRED_ALIASES:
                        raise ValueError(f"未知关系 {p!r}；可用 cites / similar_to")
                    resolved.append(PRED_ALIASES[p.lower()])
            out["predicates"] = tuple(dict.fromkeys(resolved))
        out["limit"] = _int("limit", 50, 1, MAX_LIMIT)
    elif op == "path":
        if len(positional) < 2:
            raise ValueError("path 需要两个 paper_id，例如：path 1529 1781 max=4")
        out["source"], out["target"] = positional[0], positional[1]
        out["max_depth"] = _int("max", 3, 1, MAX_PATH)
    elif op == "by_entity":
        if len(positional) < 2:
            raise ValueError("by_entity 需要 kind 与 value，例如：by_entity technique PXRD")
        kind = positional[0].lower()
        if kind not in ENTITY_KINDS:
            raise ValueError(f"kind 必须是 {list(ENTITY_KINDS)}")
        out["kind"] = kind
        out["value"] = " ".join(positional[1:])
        out["limit"] = _int("limit", 20, 1, MAX_LIMIT)
    elif op == "by_metric":
        name = " ".join(positional) if positional else args.pop("name", "")
        if not name:
            raise ValueError("by_metric 需要指标名，例如：by_metric 比表面积 min=2000")
        out["name"] = name
        out["min"] = float(args.pop("min")) if "min" in args else None
        out["max"] = float(args.pop("max")) if "max" in args else None
        out["limit"] = _int("limit", 20, 1, MAX_LIMIT)
    elif op == "cites_of":
        if not positional:
            raise ValueError("cites_of 需要 paper_id")
        out["paper_id"] = positional[0]
        direction = (args.pop("dir", None) or "out").lower()
        if direction not in ("in", "out", "both"):
            raise ValueError("dir 必须是 in | out | both")
        out["direction"] = direction
        out["limit"] = _int("limit", 30, 1, MAX_LIMIT)
    elif op == "top":
        metric = (positional[0] if positional else args.pop("metric", "pagerank")).lower()
        if metric not in TOP_METRICS:
            raise ValueError(f"metric 必须是 {list(TOP_METRICS)}")
        out["metric"] = metric
        out["limit"] = _int("limit", 10, 1, MAX_LIMIT)
    if args:
        raise ValueError(f"无法识别的参数：{', '.join(args)}")
    return out


# ---------------------------------------------------------------- 执行（只读）

def _papers(conn, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT paper_id,title,journal,year,doi FROM items WHERE paper_id IN ({marks})",
        ids).fetchall()
    return {str(r["paper_id"]): {"title": r["title"] or "", "journal": r["journal"] or "",
                                 "year": r["year"], "doi": r["doi"] or ""} for r in rows}


def execute(conn, query: str | dict) -> dict:
    """执行查询（**只读**）。可传 DSL 文本或已解析的 dict。"""
    plan = parse(query) if isinstance(query, str) else dict(query)
    op = plan.get("op")
    if op not in OPS:
        raise ValueError(f"不支持的算子 {op!r}")

    if op == "neighbors":
        preds = plan.get("predicates") or ("cites", "similar_to")
        marks = ",".join("?" * len(preds))
        frontier = {f"paper:{plan['paper_id']}"}
        seen = set(frontier)
        edges: list[dict] = []
        for _ in range(int(plan.get("depth") or 1)):
            nxt: set[str] = set()
            for node in frontier:
                for r in conn.execute(
                        f"SELECT subject,predicate,object,source,confidence FROM graph_edges"
                        f" WHERE predicate IN ({marks}) AND (subject=? OR object=?)",
                        [*preds, node, node]):
                    s, o = str(r["subject"]), str(r["object"])
                    if s.startswith("paper:") and o.startswith("paper:"):
                        edges.append({"from": s, "to": o, "predicate": r["predicate"],
                                      "source": r["source"], "confidence": r["confidence"]})
                        nxt.add(o if s == node else s)
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
        ids = sorted(x.split(":", 1)[1] for x in seen)[: int(plan.get("limit") or 50)]
        uniq = {(e["from"], e["to"], e["predicate"]): e for e in edges}
        return {"op": op, "seed": plan["paper_id"], "depth": plan.get("depth", 1),
                "predicates": list(preds), "nodes": _papers(conn, ids),
                "edges": list(uniq.values())}

    if op == "path":
        src, dst = f"paper:{plan['source']}", f"paper:{plan['target']}"
        max_depth = int(plan.get("max_depth") or 3)
        # BFS（有界，两向都走）
        from collections import deque
        prev: dict[str, str | None] = {src: None}
        q = deque([(src, 0)])
        found = False
        while q and not found:
            node, d = q.popleft()
            if d >= max_depth:
                continue
            for r in conn.execute(
                    "SELECT subject,object FROM graph_edges WHERE subject=? OR object=?",
                    (node, node)):
                for nb in (str(r["subject"]), str(r["object"])):
                    if not nb.startswith("paper:") or nb in prev:
                        continue
                    prev[nb] = node
                    if nb == dst:
                        found = True
                        break
                    q.append((nb, d + 1))
                if found:
                    break
        if dst not in prev:
            return {"op": op, "found": False, "source": plan["source"],
                    "target": plan["target"], "max_depth": max_depth,
                    "message": f"在 {max_depth} 跳内未找到关系路径（可增大 max=）"}
        chain, cur = [], dst
        while cur is not None:
            chain.append(cur)
            cur = prev[cur]
        chain.reverse()
        ids = [n.split(":", 1)[1] for n in chain]
        return {"op": op, "found": True, "length": len(chain) - 1,
                "path": ids, "nodes": _papers(conn, ids)}

    if op == "by_entity":
        marks = "technique" if plan["kind"] in ("technique",) else plan["kind"]
        rows = conn.execute(
            "SELECT node_id FROM graph_nodes WHERE node_type=? AND name LIKE ? LIMIT ?",
            (marks, f"%{plan['value']}%", int(plan["limit"]))).fetchall()
        ids: list[str] = []
        for r in rows:
            for e in conn.execute(
                    "SELECT DISTINCT subject FROM graph_edges WHERE object=?"
                    " AND subject LIKE 'paper:%' LIMIT ?", (r["node_id"], plan["limit"])):
                pid = str(e["subject"]).split(":", 1)[1]
                if pid not in ids:
                    ids.append(pid)
        return {"op": op, "kind": plan["kind"], "value": plan["value"],
                "matched_nodes": [r["node_id"] for r in rows],
                "papers": _papers(conn, ids[: int(plan["limit"])])}

    if op == "by_metric":
        metrics = _lib().search_metrics(conn, name=plan["name"],
                                        min_value=plan.get("min"),
                                        max_value=plan.get("max"),
                                        limit=int(plan["limit"]))
        return {"op": op, "name": plan["name"], "min": plan.get("min"),
                "max": plan.get("max"), "count": len(metrics), "metrics": metrics}

    if op == "cites_of":
        node = f"paper:{plan['paper_id']}"
        out: list[dict] = []
        if plan["direction"] in ("out", "both"):
            for r in conn.execute(
                    "SELECT object AS n,source,confidence FROM graph_edges WHERE subject=?"
                    " AND predicate='cites' LIMIT ?", (node, int(plan["limit"]))):
                out.append({"direction": "out", "target": r["n"], "source": r["source"],
                            "confidence": r["confidence"]})
        if plan["direction"] in ("in", "both"):
            for r in conn.execute(
                    "SELECT subject AS n,source,confidence FROM graph_edges WHERE object=?"
                    " AND predicate='cites' LIMIT ?", (node, int(plan["limit"]))):
                out.append({"direction": "in", "target": r["n"], "source": r["source"],
                            "confidence": r["confidence"]})
        return {"op": op, "paper_id": plan["paper_id"],
                "direction": plan["direction"], "count": len(out), "relations": out}

    # top
    rows = conn.execute(
        "SELECT node_id,value,rank FROM graph_metrics WHERE metric=?"
        " ORDER BY value DESC LIMIT ?", (plan["metric"], int(plan["limit"]))).fetchall()
    ids = [str(r["node_id"]).split(":", 1)[1] for r in rows]
    meta = _papers(conn, ids)
    return {"op": op, "metric": plan["metric"], "count": len(rows),
            "results": [{"paper_id": pid, "value": r["value"], **meta.get(pid, {})}
                        for pid, r in zip(ids, rows)]}


# ---------------------------------------------------------------- 自然语言 → DSL

NL_SYSTEM = (
    "把用户的自然语言问题翻译成**一行**图查询 DSL（只读）。可用算子与语法：\n"
    "neighbors <paper_id> [depth=1-3] [pred=cites,similar_to] [limit=N]\n"
    "path <paper_id> <paper_id> [max=1-5]\n"
    "by_entity <technique|metric|property> <名称> [limit=N]\n"
    "by_metric <指标名> [min=X] [max=Y] [limit=N]\n"
    "cites_of <paper_id> [dir=in|out|both] [limit=N]\n"
    "top <pagerank|betweenness|cited_by_count|reference_count> [limit=N]\n"
    "只输出这一行，不要解释；无法翻译时只输出：UNSUPPORTED")


def translate_nl(text: str, *, chat=None) -> str:
    """自然语言 → DSL（**只允许白名单算子**；模型输出非法则拒绝）。

    无法翻译或输出不合法时抛 ValueError（附用法），绝不把任意文本当查询执行。
    """
    q = (text or "").strip()
    if not q:
        raise ValueError("问题为空")
    if chat is None:
        try:
            from literature import reading_llm
        except ImportError:  # pragma: no cover
            from src.literature import reading_llm  # type: ignore
        chat = reading_llm._chat
    try:
        reply = chat([{"role": "system", "content": NL_SYSTEM},
                      {"role": "user", "content": q}])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"翻译失败：{type(exc).__name__}: {exc}") from exc
    line = ""
    for raw in str(reply or "").splitlines():
        s = raw.strip().strip("`").strip()
        if s:
            line = s
            break
    if not line or line.upper().startswith("UNSUPPORTED"):
        raise ValueError("该问题无法翻译成受支持的图查询（可用算子：" + ", ".join(OPS) + "）")
    return parse(line)["raw"]              # 解析校验：非法即抛
