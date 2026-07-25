"""CAS 号 → SMILES 解析工具（P1 后端支撑）。

解析顺序：内置单体库（data/builtin_monomers.json）→ 本地缓存
（data/cas_cache.json）→ PubChem PUG-REST（联网，成功后写缓存）→
LLM 兜底（走 src/llm/client.py，未配置时自动跳过）。
任一环失败都优雅降级为 None，不抛异常，不静默猜测。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from rdkit import Chem

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
BUILTIN_PATH = PROJECT_ROOT / "data" / "builtin_monomers.json"
CACHE_PATH = runtime_config.user_data_root() / "cas_cache.json"

PUBCHEM_URL = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
    "{cas}/property/CanonicalSMILES/JSON"
)
PUBCHEM_TIMEOUT = 5  # 秒（国内网络易超时，快速降级到 LLM 兜底）

# LLM 兜底参数：longcat 为推理型模型，开启推理时 token 消耗巨大且易超
# 120s 超时；CAS→SMILES 是事实查询不需要思考链，故显式关闭推理
# （thinking: disabled，实测响应 ~2s）
LLM_MAX_TOKENS = 2000
_LLM_EXTRA_BODY = {"thinking": {"type": "disabled"}}

# 关闭推理后模型偶发幻觉（输出合法但错误的 SMILES），故采用双路一致性
# 校验：CAS→SMILES 直查 与 CAS→名称→SMILES 两条独立路径，RDKit 规范化后
# 结构一致才接受，不一致返回 None（宁缺毋滥，避免写错数据进缓存）
_LLM_PROMPT_SMILES = (
    "请给出 CAS 号 {cas} 对应化合物的 SMILES 字符串（canonical SMILES）。\n"
    "要求：只输出 SMILES 本身，一行，不要任何解释、引号、markdown 代码块或多余文字。\n"
    "如果你不确定或该 CAS 号不存在，请只输出 UNKNOWN。"
)
_LLM_PROMPT_NAME = (
    "CAS 号 {cas} 对应的化合物名称是什么（中文名或英文名均可）？\n"
    "只输出名称本身，不要任何解释。如果你不确定或该 CAS 号不存在，请只输出 UNKNOWN。"
)
_LLM_PROMPT_NAME2SMILES = (
    "化合物 {name} 的 SMILES 是什么？\n"
    "只输出 SMILES 本身，一行，不要任何解释。如果你不确定，请只输出 UNKNOWN。"
)

# CAS 格式：2-7 位数字 - 2 位数字 - 1 位校验数字
_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def is_valid_cas(cas: str) -> bool:
    """CAS 号格式校验（仅格式，不校验校验位）。"""
    if not cas or not isinstance(cas, str):
        return False
    return bool(_CAS_RE.match(cas.strip()))


def _valid_smiles(smiles: str) -> bool:
    """RDKit 校验 SMILES 合法性。"""
    if not smiles or not isinstance(smiles, str):
        return False
    try:
        return Chem.MolFromSmiles(smiles.strip()) is not None
    except Exception:
        return False


def _load_builtin() -> dict:
    """内置单体库：cas → {smiles, name}。加载失败返回空 dict。"""
    try:
        items = json.loads(BUILTIN_PATH.read_text(encoding="utf-8"))
        return {
            m["cas"].strip(): {"smiles": m["smiles"], "name": m["name"]}
            for m in items
            if m.get("cas") and m["cas"].strip()
        }
    except Exception as exc:  # 文件缺失/损坏不阻塞解析
        logger.warning("内置单体库加载失败: %s", exc)
        return {}


def _load_cache() -> dict:
    """本地 CAS 缓存：cas → {smiles, name}。加载失败返回空 dict。"""
    try:
        if not CACHE_PATH.exists():
            return {}
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("CAS 缓存加载失败: %s", exc)
        return {}


def _write_cache(cache: dict) -> None:
    """写缓存；失败静默（不影响主流程）。"""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("CAS 缓存写入失败: %s", exc)


def _fetch_pubchem(cas: str) -> Optional[dict]:
    """PubChem PUG-REST 查询 CAS → CanonicalSMILES。失败返回 None。

    独立成函数便于测试 mock（无网络环境降级验证）。
    """
    try:
        import requests

        resp = requests.get(
            PUBCHEM_URL.format(cas=cas), timeout=PUBCHEM_TIMEOUT
        )
        if resp.status_code != 200:
            return None
        props = resp.json()["PropertyTable"]["Properties"][0]
        # 新版 API 字段名可能为 ConnectivitySMILES
        smiles = props.get("CanonicalSMILES") or props.get("ConnectivitySMILES")
        if not smiles:
            return None
        return {"smiles": smiles, "name": ""}
    except Exception as exc:
        logger.info("PubChem 查询失败 (%s): %s", cas, exc)
        return None


def _canonical_smiles(smiles: str) -> Optional[str]:
    """RDKit 规范化 SMILES（用于双路结构比对）；非法返回 None。"""
    try:
        mol = Chem.MolFromSmiles(smiles.strip())
        return Chem.MolToSmiles(mol) if mol is not None else None
    except Exception:
        return None


def _llm_first_line(content: Optional[str]) -> Optional[str]:
    """取 LLM 响应第一行，去 markdown 围栏/引号；空或 UNKNOWN 返回 None。"""
    if not content or not isinstance(content, str):
        return None
    line = content.strip().splitlines()[0].strip().strip("`").strip().strip("\"'").strip()
    if not line or line.upper() == "UNKNOWN":
        return None
    return line


def _fetch_llm(cas: str) -> Optional[dict]:
    """LLM 兜底：查询 CAS → SMILES。未配置 / 失败 / 不确定返回 None。

    走 src/llm/client.py 的三级配置链，未配置时 is_configured() 为 False
    直接跳过（不影响主流程的降级语义）。独立成函数便于测试 mock。

    为抑制幻觉，采用双路一致性校验：
    路径 A：CAS → SMILES 直查；
    路径 B：CAS → 化合物名称 → SMILES。
    两路结果经 RDKit 规范化后结构一致才接受，任一失败/不一致返回 None。
    """
    try:
        try:
            from src.llm import client as llm_client
        except ImportError:
            from llm import client as llm_client  # type: ignore

        if not llm_client.is_configured():
            return None

        def ask(prompt: str) -> Optional[str]:
            return _llm_first_line(
                llm_client.chat_completion(
                    [{"role": "user", "content": prompt}],
                    max_tokens=LLM_MAX_TOKENS,
                    temperature=0.0,
                    extra_body=_LLM_EXTRA_BODY,
                )
            )

        # 路径 A：CAS → SMILES
        smi_a = ask(_LLM_PROMPT_SMILES.format(cas=cas))
        canon_a = _canonical_smiles(smi_a) if smi_a else None

        # 路径 B：CAS → 名称 → SMILES
        name = ask(_LLM_PROMPT_NAME.format(cas=cas))
        smi_b = ask(_LLM_PROMPT_NAME2SMILES.format(name=name)) if name else None
        canon_b = _canonical_smiles(smi_b) if smi_b else None

        if canon_a and canon_b and canon_a == canon_b:
            return {"smiles": canon_a, "name": name or ""}

        logger.info(
            "LLM 双路不一致或不确定 (%s): A=%s B=%s", cas, canon_a, canon_b
        )
        return None
    except Exception as exc:
        logger.info("LLM 查询失败 (%s): %s", cas, exc)
        return None


def resolve_cas(cas: str) -> Optional[dict]:
    """解析 CAS 号为 SMILES。

    返回 {"smiles": ..., "name": ..., "source": "builtin|cache|pubchem|llm"}；
    格式非法 / 四路均未命中 / 返回 SMILES 不合法时返回 None。
    """
    try:
        if not is_valid_cas(cas):
            return None
        cas = cas.strip()

        # 1. 内置单体库（离线可用）
        hit = _load_builtin().get(cas)
        if hit and _valid_smiles(hit["smiles"]):
            return {"smiles": hit["smiles"], "name": hit["name"], "source": "builtin"}

        # 2. 本地缓存
        cache = _load_cache()
        hit = cache.get(cas)
        if hit and _valid_smiles(hit.get("smiles", "")):
            return {
                "smiles": hit["smiles"],
                "name": hit.get("name", ""),
                "source": "cache",
            }

        # 3. PubChem 在线查询，成功后写缓存
        hit = _fetch_pubchem(cas)
        if hit and _valid_smiles(hit["smiles"]):
            cache[cas] = {"smiles": hit["smiles"], "name": hit.get("name", "")}
            _write_cache(cache)
            return {
                "smiles": hit["smiles"],
                "name": hit.get("name", ""),
                "source": "pubchem",
            }

        # 4. LLM 兜底（未配置自动跳过），RDKit 校验合法才接受并写缓存
        hit = _fetch_llm(cas)
        if hit and _valid_smiles(hit["smiles"]):
            cache[cas] = {"smiles": hit["smiles"], "name": hit.get("name", "")}
            _write_cache(cache)
            return {
                "smiles": hit["smiles"],
                "name": hit.get("name", ""),
                "source": "llm",
            }
        return None
    except Exception as exc:  # 兜底：CAS 解析绝不影响主流程
        logger.warning("resolve_cas 异常 (%s): %s", cas, exc)
        return None
