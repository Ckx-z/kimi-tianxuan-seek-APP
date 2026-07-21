"""CAS 号 → SMILES 解析工具（P1 后端支撑）。

解析顺序：内置单体库（data/builtin_monomers.json）→ 本地缓存
（data/cas_cache.json）→ PubChem PUG-REST（联网，成功后写缓存）。
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_PATH = PROJECT_ROOT / "data" / "builtin_monomers.json"
CACHE_PATH = PROJECT_ROOT / "data" / "cas_cache.json"

PUBCHEM_URL = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
    "{cas}/property/CanonicalSMILES/JSON"
)
PUBCHEM_TIMEOUT = 8  # 秒

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


def resolve_cas(cas: str) -> Optional[dict]:
    """解析 CAS 号为 SMILES。

    返回 {"smiles": ..., "name": ..., "source": "builtin|cache|pubchem"}；
    格式非法 / 三路均未命中 / 返回 SMILES 不合法时返回 None。
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
        return None
    except Exception as exc:  # 兜底：CAS 解析绝不影响主流程
        logger.warning("resolve_cas 异常 (%s): %s", cas, exc)
        return None
