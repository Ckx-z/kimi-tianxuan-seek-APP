"""助手工具：单体性质 / CAS 解析 / 文献 DOI 查询（v1.6.0，包一层现有模块）。

三件套补齐 openhanako 接入计划 V2 遗留工具（get_monomer_props / cas_resolve），
并把 v1.5.4 文献 DOI 基建开放给助手（lookup_paper_doi）。全部遵守
{text, details, is_error} 契约，不抛异常。
"""

from __future__ import annotations


# ---------------------------------------------------------------- 单体性质

def get_monomer_props(smiles: str, name: str = "") -> dict:
    """单体物化性质卡（RDKit 事实 + LLM 解读，无 LLM 时只有事实）。"""
    smiles = (smiles or "").strip()
    if not smiles:
        return {"text": "get_monomer_props 参数错误：smiles 不能为空",
                "details": {}, "is_error": True}
    try:
        try:
            from src.recommend import monomer_props
        except ImportError:  # pragma: no cover
            from recommend import monomer_props  # type: ignore
        props = monomer_props.get_monomer_properties(
            smiles, name=(name or "").strip())
    except Exception as exc:
        return {"text": f"单体性质计算失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}
    facts = props.get("facts") or {}
    narrative = (props.get("narrative") or "").strip()
    lines = []
    for key, label in (("molecular_weight", "分子量"),
                       ("logp", "LogP"), ("tpsa", "TPSA"),
                       ("hbd", "氢键供体"), ("hba", "氢键受体"),
                       ("rotatable_bonds", "可旋转键"),
                       ("rings", "环数")):
        if facts.get(key) is not None:
            lines.append(f"{label}: {facts[key]}")
    text = f"单体性质（{smiles}）：\n" + "\n".join(lines)
    if narrative:
        text += f"\n解读：{narrative}"
    return {"text": text, "details": {"facts": facts,
                                      "narrative_source": props.get("narrative_source")},
            "is_error": False}


# ---------------------------------------------------------------- CAS 解析

def cas_resolve(cas: str) -> dict:
    """CAS 号 → SMILES/名称（内置库→缓存→PubChem→LLM 四路）。"""
    cas = (cas or "").strip()
    if not cas:
        return {"text": "cas_resolve 参数错误：cas 不能为空",
                "details": {}, "is_error": True}
    try:
        try:
            from src.utils import cas_lookup
        except ImportError:  # pragma: no cover
            from utils import cas_lookup  # type: ignore
        hit = cas_lookup.resolve_cas(cas)
    except Exception as exc:
        return {"text": f"CAS 解析失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}
    if not hit:
        return {"text": f"CAS {cas} 解析失败：内置库/PubChem/LLM 均未命中"
                        "（请确认 CAS 号正确，或改用 SMILES）",
                "details": {"cas": cas}, "is_error": True}
    return {"text": f"CAS {cas} → {hit.get('name') or '（未知名称）'}"
                    f"，SMILES: {hit.get('smiles')}（来源: {hit.get('source')}）",
            "details": {"cas": cas, **hit}, "is_error": False}


# ---------------------------------------------------------------- 文献 DOI

def lookup_paper_doi(doi: str) -> dict:
    """按 DOI 查文献元数据（本库优先，Crossref 兜底）；返回含可点击链接。"""
    doi = (doi or "").strip()
    if not doi:
        return {"text": "lookup_paper_doi 参数错误：doi 不能为空",
                "details": {}, "is_error": True}
    # 去 doi.org 前缀
    prefix = ("https://doi.org/", "http://doi.org/", "doi.org/",
              "https://dx.doi.org/", "http://dx.doi.org/", "dx.doi.org/")
    for p in prefix:
        if doi.lower().startswith(p):
            doi = doi[len(p):]
            break
    try:
        try:
            from src.literature import resolver as lit_resolver
            from src.literature import crossref as lit_crossref
        except ImportError:  # pragma: no cover
            from literature import resolver as lit_resolver  # type: ignore
            from literature import crossref as lit_crossref  # type: ignore
        # 1. 本机文献库
        hit = lit_resolver.find_by_doi(doi)
        if hit:
            _pid, entry = hit
            return {"text": f"文献库已收录：{entry.get('title')}"
                            f"（DOI: {doi}，https://doi.org/{doi}）",
                    "details": {"doi": doi, "in_library": True,
                                "url": f"https://doi.org/{doi}",
                                "entry": entry},
                    "is_error": False}
        # 2. Crossref 在线
        draft = lit_crossref.lookup_doi(doi)
    except lit_crossref.CrossrefNotFound:
        return {"text": f"Crossref 未找到 DOI {doi} 对应的文献"
                        "（请确认 DOI 正确）",
                "details": {"doi": doi}, "is_error": True}
    except lit_crossref.CrossrefError as exc:
        return {"text": f"Crossref 查询失败：{exc}",
                "details": {"doi": doi}, "is_error": True}
    except Exception as exc:
        return {"text": f"文献查询失败：{type(exc).__name__}: {exc}",
                "details": {"doi": doi}, "is_error": True}
    authors = ", ".join(draft.get("authors") or [])[:200]
    text = (f"{draft.get('title') or '（无标题）'}\n"
            f"作者: {authors or '（未知）'} | "
            f"{draft.get('journal') or '（期刊未知）'} | "
            f"{draft.get('year') or '（年份未知）'}\n"
            f"DOI: {doi}（https://doi.org/{doi}）")
    return {"text": text, "details": {"doi": doi, "in_library": False,
                                      "url": f"https://doi.org/{doi}",
                                      "draft": draft},
            "is_error": False}
