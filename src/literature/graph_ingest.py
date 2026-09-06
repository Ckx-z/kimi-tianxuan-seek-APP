"""科研知识库条目入图（v1.9.0）：文献结构化条目 → 用户侧车图增量。

复用 minimax/bridge/user_graph.py 的侧车图（graph_user.pkl）与节点 ID
方案：单体 M-<md5(SMILES)> 与包内图对齐；文献按「文献 + 实验组」建
LIT-<paper_id>-<group_id> reaction 节点（source='literature'），
characterization/property/dft/conclusion 作为节点属性挂载；
film_outcome 挂 O-film/O-partial/O-failed 边（负样本 O-failed）。

入图是**组级同步**（幂等）：sync_group(paper_id, group_id) 按当前条目
重建该组节点（无条目则删除节点），更新/删除条目后调用即保持一致。
检索侧无需额外接线：助手 query_graphrag 与方案迭代的 load_graph
均已 merge 侧车图（src/assistant/tools/graphrag.py / iterate_suggest.py）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

_BRIDGE = runtime_config.resource_root() / "minimax" / "bridge"
if str(_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_BRIDGE))

import user_graph  # noqa: E402  (bridge 模块按裸名 import，同 adapters 口径)

_SEP_CHARS = '/、;；,，'
_OUTCOME_NODE = {1.0: "O-film", 0.5: "O-partial", 0.0: "O-failed"}


def _app_root() -> Path:
    """侧车图所属应用根（frozen 时 %APPDATA%，源码态项目根；测试可打桩）。"""
    return runtime_config.user_app_root()


def lit_node_id(paper_id: str, group_id: str) -> str:
    return f"LIT-{paper_id}-{group_id}"


def _md5_id(prefix: str, text: str) -> str:
    return prefix + "-" + hashlib.md5(text.encode()).hexdigest()[:12]


def _split_components(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    parts = [text]
    for ch in _SEP_CHARS:
        parts = [p2 for p in parts for p2 in p.split(ch)]
    return [p.strip() for p in parts if p.strip()]


def _group_attrs(paper_id: str, group_id: str, entries: list[dict]) -> dict:
    """一组条目 → LIT 节点属性（聚合单体对/条件/表征/结论）。"""
    ald = amine = None
    film_label = None
    experiment = ""
    stoichiometry = topology = method = ""
    conditions: dict = {}
    characterizations: list[dict] = []
    properties: list[dict] = []
    dfts: list[dict] = []
    conclusions: list[str] = []
    evidences: list[str] = []
    for e in entries:
        experiment = experiment or str(e.get("experiment") or "")
        evidences.append(str(e.get("evidence") or "")[:200])
        if e.get("ald_smiles"):
            ald, amine = e.get("ald_smiles"), e.get("amine_smiles")
        if e.get("stoichiometry"):
            stoichiometry = e["stoichiometry"]
        if e.get("topology"):
            topology = e["topology"]
        if e.get("synthesis_method"):
            method = e["synthesis_method"]
        if e.get("kind") == "film_outcome":
            film_label = float(e.get("film_label"))
        if e.get("kind") == "condition":
            for k, v in (e.get("conditions") or {}).items():
                conditions[k] = v
        if e.get("kind") == "characterization":
            characterizations.append({
                "technique": e.get("technique"),
                "sample": e.get("sample") or "",
                "metrics": e.get("metrics") or [],
                "conclusion": e.get("conclusion") or "",
            })
        if e.get("kind") == "property":
            properties.append({
                "property_name": e.get("property_name") or "",
                "metrics": e.get("metrics") or [],
                "conclusion": e.get("conclusion") or "",
            })
        if e.get("kind") == "dft":
            dfts.append({
                "dft_method": e.get("dft_method") or "",
                "dft_target": e.get("dft_target") or "",
                "metrics": e.get("metrics") or [],
                "conclusion": e.get("conclusion") or "",
            })
        if e.get("kind") == "conclusion" and e.get("conclusion"):
            conclusions.append(e["conclusion"])
    attrs = {
        "node_type": "reaction",
        "source": "literature",
        "paper_id": paper_id,
        "group_id": group_id,
        "experiment": experiment,
        "stoichiometry": stoichiometry,
        "topology": topology,
        "synthesis_method": method,
        "film_label": film_label,
        "conditions": json.dumps(conditions, ensure_ascii=False),
        "characterizations": json.dumps(characterizations, ensure_ascii=False),
        "properties": json.dumps(properties, ensure_ascii=False),
        "dft": json.dumps(dfts, ensure_ascii=False),
        "conclusions": json.dumps(conclusions, ensure_ascii=False),
        "evidence": "；".join(evidences)[:800],
    }
    return attrs, ald, amine, film_label, conditions


def _load_graph() -> "object":
    import networkx as nx
    G = user_graph.load_user_graph(app_root=_app_root())
    return G if G is not None else nx.MultiDiGraph()


def _save_graph(G, n_lit: int) -> None:
    fp = user_graph.user_graph_path(app_root=_app_root())
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "wb") as f:
        import pickle
        pickle.dump(G, f, pickle.HIGHEST_PROTOCOL)
    meta = {
        "updated_at": __import__("datetime").datetime.now()
        .isoformat(timespec="seconds"),
        "n_nodes": G.number_of_nodes(),
        "n_edges": G.number_of_edges(),
        "n_literature_nodes": n_lit,
    }
    user_graph.user_meta_path(app_root=_app_root()).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def sync_group(paper_id: str, group_id: str,
               entries: list[dict] | None = None) -> bool:
    """组级同步（幂等）：按当前条目重建/删除 LIT 节点。返回是否发生写入。

    entries=None 时从 knowledge 库读取该组当前条目。
    """
    if entries is None:
        from literature import knowledge
        entries = [e for e in knowledge.list_entries(paper_id=paper_id)
                   if str(e.get("group_id")) == str(group_id)]
    nid = lit_node_id(paper_id, group_id)
    G = _load_graph()
    changed = False
    if nid in G:
        G.remove_node(nid)
        changed = True
    if not entries:
        if changed:
            _save_graph(G, _count_lit(G))
        return changed

    attrs, ald, amine, film_label, conditions = _group_attrs(
        paper_id, group_id, entries)
    G.add_node(nid, **attrs)
    edges: list[tuple[str, str, str]] = []
    for smi, role in ((ald, "aldehyde"), (amine, "amine")):
        if not smi:
            continue
        mid = _md5_id("M", smi)
        if mid not in G:
            G.add_node(mid, node_type="monomer", source="literature",
                       smiles=smi, role=role, best_name="")
        edges.append((nid, mid,
                      f"reaction_uses_{role}"))
    for s in _split_components(str(conditions.get("solvent") or "")):
        sid = _md5_id("S", s.lower())
        if sid not in G:
            G.add_node(sid, node_type="solvent", name=s, source="literature")
        edges.append((nid, sid, "reaction_uses_solvent"))
    for c in _split_components(str(conditions.get("catalyst") or "")
                               + " " + str(conditions.get("modulator") or "")):
        cid = _md5_id("C", c.lower())
        if cid not in G:
            G.add_node(cid, node_type="catalyst", name=c, source="literature")
        edges.append((nid, cid, "reaction_uses_catalyst"))
    oid = _OUTCOME_NODE.get(film_label)
    if oid:
        if oid not in G:
            G.add_node(oid, node_type="outcome",
                       name=str(film_label), source="literature")
        edges.append((nid, oid, "reaction_produces"))
    for u, v, etype in edges:
        existing = {d.get("edge_type")
                    for d in G.get_edge_data(u, v, default={}).values()}
        if etype not in existing:
            G.add_edge(u, v, edge_type=etype)
    _save_graph(G, _count_lit(G))
    return True


def sync_groups(entries: list[dict]) -> int:
    """批量入库后：按 (paper_id, group_id) 分组同步。返回同步的组数。"""
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in entries:
        key = (str(e.get("paper_id") or ""), str(e.get("group_id") or ""))
        if key[0]:
            groups.setdefault(key, []).append(e)
    n = 0
    for (paper_id, group_id), rows in groups.items():
        try:
            if sync_group(paper_id, group_id, entries=rows):
                n += 1
        except Exception as exc:  # 入图失败不阻塞入库
            logger.warning("组 %s/%s 入图失败: %s", paper_id, group_id, exc)
    return n


def _count_lit(G) -> int:
    return sum(1 for _, d in G.nodes(data=True)
               if d.get("source") == "literature"
               and d.get("node_type") == "reaction")
