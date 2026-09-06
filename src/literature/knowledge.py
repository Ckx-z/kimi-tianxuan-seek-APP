"""科研知识库结构化条目（v1.9.0）：文献 LLM 提取 → 分组审核 → 入库检索。

落盘：user_data_root()/literature/knowledge_entries.jsonl（追加式）。

条目 schema（详见 docs/科研知识库文献结构化提取与入图方案.md §2）：
- 所有条目必须挂 group_id（文献内实验组编号）与 evidence（原文依据）；
- kind ∈ monomer/monomer_pair/film_outcome/condition/characterization/
  property/conclusion/dft；
- characterization 带 technique（15 类，含 separation_selectivity 与 dft）
  与 metrics[{name, value, unit}]（数值化，可跨文献检索）；
- film_outcome 含负样本（film_label=0）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

ENTRIES_PATH = runtime_config.user_data_root() / "literature" / "knowledge_entries.jsonl"

ALLOWED_KINDS = {
    "monomer", "monomer_pair", "film_outcome", "condition",
    "characterization", "property", "conclusion", "dft",
}
ALLOWED_TECHNIQUES = {
    "PXRD", "FTIR", "BET", "SEM", "TEM", "AFM", "PL", "UVVis", "NMR",
    "TGA", "XPS", "contact_angle", "separation_flux",
    "separation_selectivity", "mechanical", "photocatalysis",
    "electrochem", "dft",
}
ALLOWED_LABELS = {0.0, 0.5, 1.0}
_ID_RE = re.compile(r"^ke_[0-9a-f]{12}$")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return f"ke_{uuid.uuid4().hex[:12]}"


def _load() -> list[dict]:
    out: list[dict] = []
    if not ENTRIES_PATH.is_file():
        return out
    try:
        for line in ENTRIES_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except Exception as exc:
        logger.warning("条目库读取失败（按空处理）: %s", exc)
    return out


def _save(rows: list[dict]) -> None:
    ENTRIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENTRIES_PATH.with_name(ENTRIES_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(ENTRIES_PATH)


def _clean_str(v) -> str:
    return str(v or "").strip()


def validate_entry(rec: dict) -> dict:
    """逐字段校验并规范化；失败抛 ValueError（中文原因）。"""
    kind = _clean_str(rec.get("kind"))
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"kind 必须是 {sorted(ALLOWED_KINDS)} 之一")
    group_id = _clean_str(rec.get("group_id"))
    if not group_id:
        raise ValueError("group_id（文献内实验组编号）不能为空")
    evidence = _clean_str(rec.get("evidence"))
    if not evidence:
        raise ValueError("evidence（原文依据）不能为空")
    out = {
        "kind": kind,
        "group_id": group_id,
        "experiment": _clean_str(rec.get("experiment")),
        "evidence": evidence,
        "source": _clean_str(rec.get("source")) or "llm_extract",
        "status": "confirmed",
        "graph_indexed": False,
    }
    # 按 kind 校验关键字段
    if kind in ("monomer_pair", "film_outcome"):
        ald = _clean_str(rec.get("ald_smiles"))
        amine = _clean_str(rec.get("amine_smiles"))
        if not ald or not amine:
            raise ValueError(f"{kind} 必须提供 ald_smiles 与 amine_smiles")
        out["ald_smiles"] = ald
        out["amine_smiles"] = amine
        out["stoichiometry"] = _clean_str(rec.get("stoichiometry"))
        out["topology"] = _clean_str(rec.get("topology"))
        out["synthesis_method"] = _clean_str(rec.get("synthesis_method"))
    if kind == "film_outcome":
        try:
            label = float(rec.get("film_label"))
        except (TypeError, ValueError):
            raise ValueError("film_outcome 必须提供 film_label（0/0.5/1）")
        if label not in ALLOWED_LABELS:
            raise ValueError(f"film_label 必须是 {sorted(ALLOWED_LABELS)} 之一")
        out["film_label"] = label
    if kind == "monomer":
        smi = _clean_str(rec.get("monomer_smiles"))
        if not smi:
            raise ValueError("monomer 必须提供 monomer_smiles")
        out["monomer_smiles"] = smi
        out["monomer_role"] = _clean_str(rec.get("monomer_role"))
        out["monomer_cas"] = _clean_str(rec.get("monomer_cas"))
    if kind == "condition":
        cond = rec.get("conditions")
        if not isinstance(cond, dict):
            cond = {}
        out["conditions"] = {
            k: _clean_str(v) for k, v in cond.items() if _clean_str(v)
        }
    if kind == "characterization":
        technique = _clean_str(rec.get("technique")).upper().replace("-", "_")
        if technique not in ALLOWED_TECHNIQUES:
            raise ValueError(
                f"technique 必须是 {sorted(ALLOWED_TECHNIQUES)} 之一")
        out["technique"] = technique
        out["sample"] = _clean_str(rec.get("sample"))
        metrics = rec.get("metrics")
        if not isinstance(metrics, list):
            metrics = []
        cleaned = []
        for m in metrics:
            if not isinstance(m, dict):
                continue
            name = _clean_str(m.get("name"))
            if not name:
                continue
            try:
                value = float(m["value"])
            except (TypeError, ValueError, KeyError):
                continue
            cleaned.append({"name": name, "value": value,
                            "unit": _clean_str(m.get("unit"))})
        if not cleaned:
            raise ValueError("characterization 必须至少一条 metrics[{name,value}]")
        out["metrics"] = cleaned
        out["conclusion"] = _clean_str(rec.get("conclusion"))
    if kind == "property":
        out["property_name"] = _clean_str(rec.get("property_name"))
        out["conclusion"] = _clean_str(rec.get("conclusion"))
        metrics = rec.get("metrics")
        if isinstance(metrics, list):
            cleaned = []
            for m in metrics:
                if not isinstance(m, dict):
                    continue
                name = _clean_str(m.get("name"))
                if not name:
                    continue
                try:
                    value = float(m["value"])
                except (TypeError, ValueError, KeyError):
                    continue
                cleaned.append({"name": name, "value": value,
                                "unit": _clean_str(m.get("unit"))})
            out["metrics"] = cleaned
    if kind == "conclusion":
        out["conclusion"] = _clean_str(rec.get("conclusion"))
    if kind == "dft":
        out["dft_method"] = _clean_str(rec.get("dft_method"))
        out["dft_target"] = _clean_str(rec.get("dft_target"))
        metrics = rec.get("metrics")
        if isinstance(metrics, list):
            cleaned = []
            for m in metrics:
                if not isinstance(m, dict):
                    continue
                name = _clean_str(m.get("name"))
                if not name:
                    continue
                try:
                    value = float(m["value"])
                except (TypeError, ValueError, KeyError):
                    continue
                cleaned.append({"name": name, "value": value,
                                "unit": _clean_str(m.get("unit"))})
            out["metrics"] = cleaned
        out["conclusion"] = _clean_str(rec.get("conclusion"))
    fig_ids = rec.get("figure_ids")
    if isinstance(fig_ids, list):
        out["figure_ids"] = [str(x).strip() for x in fig_ids if str(x).strip()]
    return out


def add_entries(paper_id: str, records: list[dict]) -> list[dict]:
    """批量入库（原子：全部校验通过才写入）。返回带 entry_id 的条目列表。"""
    paper_id = _clean_str(paper_id)
    if not paper_id:
        raise ValueError("paper_id 不能为空")
    validated = [validate_entry(rec) for rec in records]
    if not validated:
        raise ValueError("条目列表为空")
    now = _now()
    with _lock:
        rows = _load()
        for rec in validated:
            rec["entry_id"] = _new_id()
            rec["paper_id"] = paper_id
            rec["created_at"] = now
            rec["updated_at"] = now
            rows.append(rec)
        _save(rows)
    return validated


def get_entry(entry_id: str) -> dict | None:
    if not _ID_RE.match(entry_id or ""):
        return None
    for r in _load():
        if r.get("entry_id") == entry_id:
            return dict(r)
    return None


def list_entries(paper_id: str | None = None, kind: str | None = None,
                 technique: str | None = None, film_label: float | None = None,
                 metric: str | None = None, min_value: float | None = None,
                 max_value: float | None = None) -> list[dict]:
    """跨文献检索（含数值范围）。paper_id 精确过滤，其余按需组合。"""
    out = []
    for r in _load():
        if paper_id and r.get("paper_id") != str(paper_id):
            continue
        if kind and r.get("kind") != kind:
            continue
        if technique and (r.get("technique") or "").upper() != technique.upper():
            continue
        if film_label is not None:
            if r.get("kind") != "film_outcome" \
                    or float(r.get("film_label", -1)) != float(film_label):
                continue
        if metric:
            matched = False
            for m in (r.get("metrics") or []):
                if m.get("name") != metric:
                    continue
                v = float(m.get("value"))
                if min_value is not None and v < min_value:
                    continue
                if max_value is not None and v > max_value:
                    continue
                matched = True
                break
            if not matched:
                continue
        out.append(dict(r))
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def update_entry(entry_id: str, rec: dict) -> dict | None:
    """编辑条目（整体重校验后替换）；不存在返回 None。"""
    merged = {**(get_entry(entry_id) or {}), **{k: v for k, v in rec.items()
                                               if k != "entry_id"}}
    if not merged:
        return None
    validated = validate_entry(merged)
    with _lock:
        rows = _load()
        for i, r in enumerate(rows):
            if r.get("entry_id") == entry_id:
                validated["entry_id"] = entry_id
                validated["paper_id"] = r.get("paper_id")
                validated["created_at"] = r.get("created_at") or _now()
                validated["updated_at"] = _now()
                validated["graph_indexed"] = r.get("graph_indexed", False)
                rows[i] = validated
                _save(rows)
                return dict(validated)
    return None


def delete_entry(entry_id: str) -> dict | None:
    """删除条目（返回被删条目供撤图回调）。"""
    with _lock:
        rows = _load()
        for i, r in enumerate(rows):
            if r.get("entry_id") == entry_id:
                del rows[i]
                _save(rows)
                return dict(r)
    return None


def mark_graph_indexed(entry_id: str, indexed: bool = True) -> bool:
    with _lock:
        rows = _load()
        for r in rows:
            if r.get("entry_id") == entry_id:
                r["graph_indexed"] = bool(indexed)
                r["updated_at"] = _now()
                _save(rows)
                return True
    return False


def group_by(entries: list[dict]) -> dict[str, list[dict]]:
    """按 group_id 分组（保持传入顺序）。"""
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(str(e.get("group_id") or "未分组"), []).append(e)
    return groups


# ---------------------------------------------------------------- 图谱历史导入（v1.9.2）

# 旧图谱 outcome → 成膜三档（film=明确成膜；crystal=结晶但未明确成膜；
# powder=粉末未成膜；unknown=不导入成膜结论）
_OUTCOME_LABEL = {"film": 1.0, "crystal": 0.5, "powder": 0.0}


def _graph_nodes() -> list[dict]:
    """加载随包知识图谱（graph_v2.pkl，不含用户侧车图）的反应节点。"""
    import sys
    from src import runtime_config
    bridge = runtime_config.resource_root() / "minimax" / "bridge"
    if str(bridge) not in sys.path:
        sys.path.insert(0, str(bridge))
    import query_graphrag  # noqa: F401  # 确保 bridge 路径可用
    graph_path = bridge / "graphrag" / "graph_v2.pkl"
    if not graph_path.is_file():
        return []
    import pickle
    with open(graph_path, "rb") as f:
        G = pickle.load(f)
    out = []
    for n, d in G.nodes(data=True):
        if d.get("node_type") != "reaction":
            continue
        rec = dict(d)
        # 大量节点 group_id 属性为空字符串但 id 形如 R-<paper>-<group>，
        # 兜底解析；尾部为空（如 R-aug_848-）则用 id 哈希生成稳定组号
        if not rec.get("group_id"):
            parts = str(n).split("-", 2)
            g = parts[2].strip() if len(parts) == 3 else ""
            rec["group_id"] = g or ("imp-" + hashlib.md5(
                str(n).encode()).hexdigest()[:8])
        if not rec.get("paper_id"):
            parts = str(n).split("-", 2)
            if len(parts) >= 2:
                rec["paper_id"] = parts[1]
        if rec.get("paper_id"):
            out.append(rec)
    return out


def import_from_graph(limit: int | None = None) -> dict:
    """把随包知识图谱的反应节点转换为结构化条目（幂等：可重复执行）。

    - film/crystal/powder → film_outcome（1.0/0.5/0.0），unknown 跳过成膜结论；
    - 每个反应 → monomer_pair（名称/计量比/合成模式）+ condition
      （溶剂/温度/催化剂/界面类型）；
    - 图谱节点自身已在包内图中：导入条目标记 graph_indexed=true，
      不再重复同步侧车图；
    - 去重键 (paper_id, group_id, kind, source=graph_import)。
    返回统计 {graph_nodes, papers, imported, skipped}。
    """
    nodes = _graph_nodes()
    if limit:
        nodes = nodes[:limit]
    existing_keys: set[tuple] = set()
    for r in _load():
        if r.get("source") == "graph_import":
            existing_keys.add((str(r.get("paper_id")), str(r.get("group_id")),
                               str(r.get("kind"))))
    now = _now()
    imported = 0
    new_rows: list[dict] = []
    pending_keys: set[tuple] = set()
    for node in nodes:
        paper_id = str(node.get("paper_id") or "")
        group_id = str(node.get("group_id") or "")
        if not paper_id:
            continue
        ald = str(node.get("aldehyde_smiles") or "").strip()
        amine = str(node.get("amine_smiles") or "").strip()
        a_name = str(node.get("aldehyde_name") or "").strip()
        b_name = str(node.get("amine_name") or "").strip()
        experiment = (f"{a_name} + {b_name}" if a_name or b_name
                      else "图谱导入体系")
        evidence = ("知识图谱历史条目（构建自旧结构化文献库 "
                    f"{node.get('source_db') or ''}；yaml: "
                    f"{node.get('yaml_lid') or ''}）")[:300]
        outcome = str(node.get("outcome") or "unknown").strip()

        def _emit(kind: str, extra: dict) -> None:
            nonlocal imported
            key = (paper_id, group_id, kind)
            if key in existing_keys or key in pending_keys:
                return
            pending_keys.add(key)
            rec = {
                "kind": kind,
                "group_id": group_id,
                "experiment": experiment,
                "evidence": evidence,
                "source": "graph_import",
                "status": "confirmed",
                "graph_indexed": True,
                **extra,
            }
            try:
                validated = validate_entry(rec)
            except ValueError:
                return
            validated["entry_id"] = _new_id()
            validated["paper_id"] = paper_id
            validated["created_at"] = now
            validated["updated_at"] = now
            validated["graph_indexed"] = True  # 图谱节点已在包内图中
            new_rows.append(validated)
            imported += 1

        if ald and amine:
            _emit("monomer_pair", {
                "ald_smiles": ald, "amine_smiles": amine,
                "stoichiometry": str(node.get("stoichiometry") or ""),
                "topology": "",  # 旧图谱无拓扑字段
                "synthesis_method": str(node.get("synthesis_mode") or ""),
            })
            if outcome in _OUTCOME_LABEL:
                _emit("film_outcome", {
                    "ald_smiles": ald, "amine_smiles": amine,
                    "film_label": _OUTCOME_LABEL[outcome],
                    "stoichiometry": str(node.get("stoichiometry") or ""),
                    "synthesis_method": str(node.get("synthesis_mode") or ""),
                })
        conditions = {
            "solvent": str(node.get("solvent") or ""),
            "temperature": str(node.get("temperature") or ""),
            "catalyst": str(node.get("catalyst") or ""),
            "interface": str(node.get("interface_type") or ""),
            "synthesis_mode": str(node.get("synthesis_mode") or ""),
        }
        conditions = {k: v for k, v in conditions.items() if v}
        if conditions:
            _emit("condition", {"conditions": conditions})

    if new_rows:
        with _lock:
            rows = _load()
            rows.extend(new_rows)
            _save(rows)
    return {
        "graph_nodes": len(nodes),
        "papers": len({str(n.get("paper_id")) for n in nodes}),
        "imported": imported,
        "skipped": len(nodes) * 3 - imported,
        "total_entries": len(_load()),
    }
