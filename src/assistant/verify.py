"""引用核验器（v1.6.0 P0，Critic 第一层）：防幻觉底线。

口径：助手最终回答中出现的每个 CAS 号 / DOI / http(s) URL / 「文献标题」
样式引用，都必须能回溯到**本轮工具返回**（text+details 全文）里出现过
的同款标识；否则视为「未经系统核实」的违规引用，由 loop 打回让模型改述
（改不了就在回答里显式标注）。

实现为纯规则 + 正则，零 LLM 成本；只做「一致性校验」不做真伪判定——
不在本轮检索结果里的引用一律按可疑处理，宁严勿宽。
"""

from __future__ import annotations

import re

_CAS_RE = re.compile(r"(?<![A-Za-z0-9])\d{2,7}-\d{2}-\d(?![A-Za-z0-9])")
# DOI 只匹配 DOI 合法字符集（字母数字 + ._-;()/ ），中英文标点/汉字天然截断，
# 避免「DOI: 10.1021/xxx。随后」把后续文字卷进来
_DOI_RE = re.compile(r"10\.\d{4,9}/[A-Za-z0-9._;()/\-]+")
# URL：排除空白/引号/尖括号与中英文常用标点（，。；;、（）()【】[]{}）作终止符
_URL_RE = re.compile(r"https?://[^\s\"'<>，。；;、（）()【】\[\]{}]+")

_STRIP_DOI_SUFFIX = re.compile(r"[.,;:)\]]+$")
_DOI_PATH_RE = re.compile(r"^https?://(dx\.)?doi\.org/(.+)$", re.IGNORECASE)


def _norm_doi(doi: str) -> str:
    """DOI 规范化：去 doi.org 前缀、去尾部标点、统一小写。"""
    d = doi.strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "doi.org/",
              "https://dx.doi.org/", "http://dx.doi.org/", "dx.doi.org/"):
        if d.startswith(p):
            d = d[len(p):]
            break
    return _STRIP_DOI_SUFFIX.sub("", d)


def _doi_from_url(url: str) -> str:
    """doi.org 链接 → 规范化 DOI；非 doi 链接返回 ""。"""
    m = _DOI_PATH_RE.match((url or "").strip())
    if not m:
        return ""
    return _STRIP_DOI_SUFFIX.sub("", m.group(2).lower())


def collect_refs(tool_results: list[dict]) -> dict:
    """从本轮工具结果中收集可引用标识 → {kind: set(规范值)}。

    tool_results 为 registry.execute 返回的 dict 列表
    （{text, details, is_error}）；details 里的结构化字段（doi/url/cas/
    papers/results）一并扫描，text 全文扫描。
    """
    refs: dict[str, set] = {"cas": set(), "doi": set(), "url": set()}

    def scan(text: str) -> None:
        for m in _CAS_RE.finditer(text or ""):
            refs["cas"].add(m.group(0))
        for m in _DOI_RE.finditer(text or ""):
            refs["doi"].add(_norm_doi(m.group(0)))
        for m in _URL_RE.finditer(text or ""):
            u = m.group(0).rstrip(".,;:)\\]>")
            refs["url"].add(u)
            d = _doi_from_url(u)  # doi.org 链接同时按 DOI 入池
            if d:
                refs["doi"].add(d)

    for r in tool_results or []:
        if not isinstance(r, dict):
            continue
        scan(str(r.get("text") or ""))
        details = r.get("details")
        if isinstance(details, dict):
            # 常见结构化字段
            for key in ("cas", "doi", "url"):
                v = details.get(key)
                if isinstance(v, str) and v:
                    scan(v)
            for item in (details.get("papers") or details.get("results") or []):
                if isinstance(item, dict):
                    scan(" ".join(str(x) for x in item.values()))
        else:
            scan(str(details))
    return refs


def check_answer(answer: str, refs: dict) -> list[dict]:
    """扫描回答中的引用，返回违规列表 [{kind, value}]（可为空）。

    命中规则：回答中出现的 CAS/DOI/URL 若不在 refs 集合中 → 违规。
    DOI 匹配宽容：答案带 doi.org 前缀、refs 只有裸 DOI（或反之）都能对上。
    """
    violations: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def flag(kind: str, value: str) -> None:
        key = (kind, value)
        if key in seen:
            return
        seen.add(key)
        violations.append({"kind": kind, "value": value})

    for m in _CAS_RE.finditer(answer or ""):
        if m.group(0) not in refs.get("cas", set()):
            flag("cas", m.group(0))
    doi_pool = {_norm_doi(x) for x in refs.get("doi", set())}
    for m in _DOI_RE.finditer(answer or ""):
        norm = _norm_doi(m.group(0))
        # 允许裸 DOI 命中 refs 里带前缀的 URL/DOI（宽松归一后再比对一次）
        if norm and norm not in doi_pool and \
                f"https://doi.org/{norm}" not in refs.get("url", set()):
            flag("doi", norm)
    for m in _URL_RE.finditer(answer or ""):
        u = m.group(0).rstrip(".,;:)\\]>")
        if u in refs.get("url", set()):
            continue
        d = _doi_from_url(u)  # doi.org 链接按 DOI 校验（前缀/大小写宽容）
        if d and d in doi_pool:
            continue
        flag("url", u)
    return violations


def describe_violations(violations: list[dict], max_items: int = 5) -> str:
    """违规列表 → 打回提示文本（给 loop 注入用）。"""
    if not violations:
        return ""
    items = [f"{v['kind']}「{v['value']}」" for v in violations[:max_items]]
    more = "" if len(violations) <= max_items else \
        f"（另有 {len(violations) - max_items} 处）"
    return "、".join(items) + more
