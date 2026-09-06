"""文献解析 LLM（v1.9.0）：独立配置（与助手 LLM 隔离）+ 全维度结构化提取。

设置：user_app_root()/config/literature_llm_settings.local.json
{
  "enabled": true,                      // 总开关（默认关 → 降级正则扫描）
  "base_url": "https://.../v1",        // OpenAI 兼容端点
  "api_key": "...",                    // 只落本机配置文件（gitignored）
  "model": "...",
  "embedding_provider": "off|local|online",   // 见 src/literature/embedding.py
  "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
  "embedding_api_key": "..."           // online 提供方 key（可选）
}

解析（parse_text）：prompt 强制「先识别文献中共有几组实验 → 逐组输出」，
每条必须带 group_id 与 evidence；输出超长分段解析再合并；失败重试 1 次；
未启用/失败 → 降级 RDKit 正则扫描（monomer_pair 候选，无标签）。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

SETTINGS_PATH = runtime_config.user_app_root() / "config" \
    / "literature_llm_settings.local.json"

MAX_TEXT_CHARS = 24000       # 单次解析注入上限（全文超长分段）
_MAX_PAIRS_PREVIEW = 30

_PROMPT = (
    "你是科研文献解析助手。请阅读下面的 COF（共价有机框架）成膜文献片段，"
    "提取其中的**每一组实验**的结构化信息。\n"
    "【第一步】先识别文中一共有几组实验（按单体组合/合成条件区分），给每组"
    "一个编号 group_id（沿用文中编号，如 G1/G2/D3；无编号则用 E1/E2...）。\n"
    "【第二步】逐组输出 JSON 数组（不要 markdown 围栏、不要多余文字），"
    "每条一个对象：\n"
    "{\n"
    ' "group_id": "G1", "experiment": "组描述（体系+方法一句话）",\n'
    ' "kind": "monomer|monomer_pair|film_outcome|condition|characterization|'
    'property|conclusion|dft",\n'
    ' "ald_smiles": "...", "amine_smiles": "...",     // monomer_pair/film_outcome\n'
    ' "stoichiometry": "...", "topology": "...", "synthesis_method": "...",\n'
    ' "film_label": 1|0.5|0,        // film_outcome：成膜1/边界0.5/不成膜0（负样本也要提！）\n'
    ' "conditions": {"solvent": "...", "temperature": "...", "catalyst": "...",'
    ' "modulator": "...", "time": "...", "atmosphere": "..."},\n'
    ' "technique": "PXRD|FTIR|BET|SEM|TEM|AFM|PL|UVVis|NMR|TGA|XPS|'
    'contact_angle|separation_flux|separation_selectivity|mechanical|'
    'photocatalysis|electrochem|dft",\n'
    ' "sample": "该表征对应的体系/样品名",\n'
    ' "metrics": [{"name": "指标名(如PLQY/比表面积/2θ峰位/截留率/选择性系数)",'
    ' "value": 数值, "unit": "单位"}],\n'
    ' "conclusion": "一句话结论", "property_name": "...",\n'
    ' "dft_method": "...", "dft_target": "...",\n'
    ' "monomer_smiles": "...", "monomer_role": "...", "monomer_cas": "...",\n'
    ' "evidence": "原文依据句（必须逐字引用文中原句，≤200字）"\n'
    "}\n"
    "【规则】1) 每个对象必须带 group_id 与 evidence；2) 只提取文中明确出现"
    "的信息，不要编造；3) 表征数据提取数值（metrics），多峰/多指标拆多条；"
    "4) 同一组实验的 condition/characterization/property 都挂同一 group_id；"
    "5) 成膜结论必须给出（含不成膜/失败体系）。\n\n文献片段：\n"
)

_RETRY_PROMPT = "格式错误。请严格只输出 JSON 数组（每条对象含 group_id 与 evidence）。"

_SMILES_TOKEN = re.compile(r"[A-Za-z0-9@+\-\[\]()\\/#%=.]{6,}")


# ---------------------------------------------------------------- 设置

def _read_settings() -> dict:
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        obj = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:
        logger.warning("文献解析设置读取失败（按空处理）: %s", exc)
        return {}


def get_settings(public: bool = True) -> dict:
    """读取设置；public=True 时 api_key 只回显掩码。"""
    s = _read_settings()
    if public and s.get("api_key"):
        key = str(s["api_key"])
        s = dict(s)
        s["api_key"] = (key[:6] + "…" + key[-4:]) if len(key) > 12 else "***"
    if public and s.get("embedding_api_key"):
        key = str(s["embedding_api_key"])
        s = dict(s)
        s["embedding_api_key"] = (key[:6] + "…" + key[-4:]) \
            if len(key) > 12 else "***"
    return s


def save_settings(enabled: bool | None = None, base_url: str | None = None,
                  api_key: str | None = None, model: str | None = None,
                  embedding_provider: str | None = None,
                  embedding_model: str | None = None,
                  embedding_api_key: str | None = None) -> dict:
    """写设置（只改传入字段；key 传掩码则不改）。返回公开设置。"""
    s = _read_settings()
    if enabled is not None:
        s["enabled"] = bool(enabled)
    if base_url is not None:
        s["base_url"] = (base_url or "").strip()
    if api_key is not None and "…" not in api_key and api_key != "***":
        s["api_key"] = api_key.strip()
    if model is not None:
        s["model"] = (model or "").strip()
    if embedding_provider is not None:
        s["embedding_provider"] = embedding_provider.strip()
    if embedding_model is not None:
        s["embedding_model"] = (embedding_model or "").strip()
    if embedding_api_key is not None and "…" not in embedding_api_key \
            and embedding_api_key != "***":
        s["embedding_api_key"] = embedding_api_key.strip()
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_PATH)
    return get_settings()


def is_enabled() -> bool:
    s = _read_settings()
    return bool(s.get("enabled")) and bool(s.get("api_key")) \
        and bool(s.get("base_url")) and bool(s.get("model"))


# ---------------------------------------------------------------- 解析

def _chat(base_url: str, api_key: str, model: str, prompt: str,
          max_tokens: int = 4000) -> str | None:
    """OpenAI 兼容 chat 调用；失败返回 None。"""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        logger.warning("文献解析 LLM 调用失败: %s", exc)
        return None


def _parse_json_array(text: str) -> list[dict] | None:
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


def _scan_smiles_candidates(text: str) -> list[dict]:
    """降级兜底：RDKit 正则扫描醛/胺 SMILES 配对候选（无标签，人工补全）。

    令牌会带上前后的括号/标点（SMILES 内部才合法），逐变体尝试解析。
    """
    from rdkit import Chem
    alds, amines, seen = [], [], set()
    for m in _SMILES_TOKEN.finditer(text or ""):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        mol = None
        for cand in (raw, raw.strip("()[]"), raw.rstrip(".,;:)"),
                     raw.strip("()[]").rstrip(".,;:)")):
            if len(cand) > 400:
                continue
            mol = Chem.MolFromSmiles(cand)
            if mol is not None:
                break
        if mol is None:
            continue
        n_ald = len(mol.GetSubstructMatches(Chem.MolFromSmarts("[CX3H](=O)")))
        n_amine = (len(mol.GetSubstructMatches(
                       Chem.MolFromSmarts("[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]")))
                   + len(mol.GetSubstructMatches(
                       Chem.MolFromSmarts("[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"))))
        if n_ald > 0 and n_amine == 0:
            alds.append(Chem.MolToSmiles(mol))
        elif n_amine > 0 and n_ald == 0:
            amines.append(Chem.MolToSmiles(mol))
    out = []
    for a in alds[:6]:
        for b in amines[:6]:
            out.append({"group_id": "E1", "kind": "monomer_pair",
                        "ald_smiles": a, "amine_smiles": b,
                        "evidence": "SMILES 正则扫描（需人工核对原文）"})
            if len(out) >= _MAX_PAIRS_PREVIEW:
                return out
    return out


def parse_text(text: str) -> dict:
    """全维度解析。返回 {"llm_used": bool, "entries": [...], "note": str}。"""
    text = (text or "").strip()
    if not text:
        return {"llm_used": False, "entries": [], "note": "文本为空"}
    if not is_enabled():
        entries = _scan_smiles_candidates(text)
        return {"llm_used": False, "entries": entries,
                "note": "文献解析 LLM 未启用：已降级为 SMILES 正则扫描"
                         "（请在设置页配置后重试获得全维度提取）"}
    s = _read_settings()
    # 全文超长分段（每段独立解析，结果合并去重 group_id 排序）
    chunks = [text[i:i + MAX_TEXT_CHARS]
              for i in range(0, len(text), MAX_TEXT_CHARS)]
    all_entries: list[dict] = []
    for chunk in chunks:
        raw = _chat(s["base_url"], s["api_key"], s["model"],
                    _PROMPT + chunk)
        if raw is None:
            continue
        arr = _parse_json_array(raw)
        if arr is None:
            raw2 = _chat(s["base_url"], s["api_key"], s["model"],
                         _PROMPT + chunk + "\n\n" + _RETRY_PROMPT)
            arr = _parse_json_array(raw2 or "")
        if arr:
            for item in arr:
                if isinstance(item, dict) and item.get("group_id") \
                        and item.get("evidence"):
                    all_entries.append(item)
    if not all_entries:
        return {"llm_used": True, "entries": _scan_smiles_candidates(text),
                "note": "LLM 解析未产出有效条目：已降级为 SMILES 扫描"}
    # 去重：同 (group_id, kind, 关键字段) 只留一条
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for e in all_entries:
        key = (str(e.get("group_id")), str(e.get("kind")),
               str(e.get("ald_smiles") or ""), str(e.get("amine_smiles") or ""),
               str(e.get("technique") or ""), str(e.get("evidence") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    deduped.sort(key=lambda e: str(e.get("group_id")))
    return {"llm_used": True, "entries": deduped,
            "note": f"LLM 解析完成：{len(deduped)} 条（{len(set(str(e.get('group_id')) for e in deduped))} 组）"}


def test_connection() -> dict:
    """测试连接：返回 {"ok": bool, "message": str}。"""
    if not is_enabled():
        return {"ok": False, "message": "文献解析 LLM 未启用或配置不完整"}
    s = _read_settings()
    reply = _chat(s["base_url"], s["api_key"], s["model"], "回复 OK")
    if reply is None:
        return {"ok": False, "message": "连接失败：请检查 base_url / api_key / model"}
    return {"ok": True, "message": f"连接成功（模型响应 {len(reply)} 字符）"}
