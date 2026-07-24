"""单体性质卡（P4b-C）：RDKit 确定性事实 + LLM 中文解读。

get_monomer_properties(smiles, name="") -> {
    "facts": {mw, xlogp, tpsa, hbd, hba, aromatic_rings, f_count,
              rotatable_bonds},   # SMILES 解析失败时为 {}
    "narrative": str | None,       # LLM 生成；未配置/失败时 None
    "narrative_source": "llm" | "none",
}

- facts 全部来自 RDKit，零成本零幻觉；RDKit 不可用或解析失败 facts={}。
- narrative 调任务 B 的 src.llm.client（is_configured/chat_completion），
  按规范化 SMILES 缓存于 data/llm_cache/monomer_props/（gitignored），
  同一单体不重复调用；LLM 未配置时优雅降级为 None，不抛异常。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
CACHE_DIR = runtime_config.user_data_root() / "llm_cache" / "monomer_props"

_FACT_KEYS = (
    "mw", "xlogp", "tpsa", "hbd", "hba",
    "aromatic_rings", "f_count", "rotatable_bonds",
)


# ---------------------------------------------------------------- facts

def _mol(smiles: str):
    if not smiles or not isinstance(smiles, str):
        return None
    try:
        from rdkit import Chem

        return Chem.MolFromSmiles(smiles.strip())
    except Exception:
        return None


def compute_facts(smiles: str) -> dict:
    """RDKit 确定性事实；解析失败/RDKit 不可用返回 {}。"""
    mol = _mol(smiles)
    if mol is None:
        return {}
    try:
        from rdkit import Chem
        from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

        ri = mol.GetRingInfo()
        aromatic_rings = sum(
            1
            for ring in ri.AtomRings()
            if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring)
        )
        return {
            "mw": round(Descriptors.MolWt(mol), 2),
            "xlogp": round(Crippen.MolLogP(mol), 2),
            "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 2),
            "hbd": Lipinski.NumHDonors(mol),
            "hba": Lipinski.NumHAcceptors(mol),
            "aromatic_rings": aromatic_rings,
            "f_count": sum(1 for a in mol.GetAtoms() if a.GetSymbol() == "F"),
            "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
        }
    except Exception as exc:
        logger.warning("RDKit 性质计算失败: %s", exc)
        return {}


def canonical_smiles(smiles: str) -> str:
    """规范化 SMILES（缓存键用）；解析失败返回原始 strip 串。"""
    mol = _mol(smiles)
    if mol is None:
        return (smiles or "").strip()
    try:
        from rdkit import Chem

        return Chem.MolToSmiles(mol)
    except Exception:
        return (smiles or "").strip()


# ---------------------------------------------------------------- LLM narrative

def _llm_client():
    """延迟导入任务 B 的客户端；不存在/未配置返回 None。"""
    try:
        from src.llm import client as llm_client
    except Exception:
        return None
    try:
        if not llm_client.is_configured():
            return None
    except Exception:
        return None
    return llm_client


def _build_prompt(smiles: str, name: str, facts: dict) -> list[dict]:
    facts_text = "、".join(f"{k}={facts[k]}" for k in _FACT_KEYS if k in facts) or "（无）"
    label = f"{name}（{smiles}）" if name else smiles
    user = (
        f"你是 COF（共价有机框架）材料合成实验助手。请针对单体 {label} 写一段中文性质解读，"
        "要求 5-8 句，依次覆盖：\n"
        "1) 溶解性预期（结合 XlogP/TPSA 判断在甲苯、氯仿等常用溶剂中的表现）；\n"
        "2) 该单体在 COF 合成中的角色（连接基/节点、可能形成的键型与拓扑）；\n"
        "3) 含氟或特殊官能团的意义（若有）；\n"
        "4) 毒性与实验安全注意事项（吸入/皮肤接触/废液处理等）；\n"
        "5) 结合侯老师界面法方案卡语境（甲苯/氯仿体系、120 °C、苯胺调制剂、6M 乙酸催化）"
        "给出具体操作提示。\n"
        f"已知 RDKit 计算事实：{facts_text}\n"
        "不要编造精确文献数据；不确定处用'通常''可能'等措辞。直接输出解读正文，不要标题。"
    )
    return [
        {"role": "system", "content": "你是严谨的高分子/材料化学实验助手，回答用中文。"},
        {"role": "user", "content": user},
    ]


def _cache_path(canon: str) -> Path:
    return CACHE_DIR / (hashlib.sha256(canon.encode("utf-8")).hexdigest() + ".json")


def _load_cache(canon: str) -> str | None:
    try:
        p = _cache_path(canon)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("smiles") == canon and isinstance(data.get("narrative"), str):
                return data["narrative"]
    except Exception as exc:
        logger.warning("性质卡缓存读取失败: %s", exc)
    return None


def _save_cache(canon: str, narrative: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(canon).write_text(
            json.dumps({"smiles": canon, "narrative": narrative}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("性质卡缓存写入失败: %s", exc)


def generate_narrative(smiles: str, name: str, facts: dict) -> str | None:
    """调 LLM 生成解读；按规范化 SMILES 缓存；未配置/失败返回 None。"""
    canon = canonical_smiles(smiles)
    cached = _load_cache(canon)
    if cached is not None:
        return cached
    llm = _llm_client()
    if llm is None:
        return None
    try:
        # longcat 为推理型模型，推理过程会大量消耗 max_tokens；3000 常被推理
        # 吃光导致 content 为空、性质卡解读生成失败，故给到 8000
        text = llm.chat_completion(
            _build_prompt(canon, name, facts), max_tokens=8000, temperature=0.3
        )
    except Exception as exc:
        logger.warning("LLM 性质解读调用失败: %s", exc)
        return None
    if not text or not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    _save_cache(canon, text)
    return text


# ---------------------------------------------------------------- 主入口

def get_monomer_properties(smiles: str, name: str = "") -> dict:
    """单体性质卡主入口；任何输入都不抛异常。"""
    facts = compute_facts(smiles)
    narrative = generate_narrative(smiles, name, facts)
    return {
        "facts": facts,
        "narrative": narrative,
        "narrative_source": "llm" if narrative else "none",
    }
