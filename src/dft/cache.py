"""DFT 结果缓存：key = 二聚体 canonical SMILES + X 描述 + 方法档位。

DFT 2.0 起计算对象是「缩合二聚体与第三物质 X 的结合能」，缓存 key
相应升级为 (dimer_smiles, x_cache_part, method)——同一对单体但不同 X
类型（自身堆积/不同溶剂/不同异质二聚体）的结果互不串扰。

缓存落在 user_data_root()/dft_cache/<sha1>.json；模块级 CACHE_DIR 供测试
monkeypatch 到 tmp 目录（与 prediction_log / favorites 的测试口径一致）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

CACHE_DIR = runtime_config.user_data_root() / "dft_cache"


def cache_key(dimer_smiles: str, x_cache_part: str, method: str,
              mode: str = "dimer", backend: str = "xtb",
              sampler_tag: str | None = None) -> str:
    """(后端, 模式, 主体 canonical SMILES, X 缓存描述, 方法[, 采样口径]) → sha1。

    mode 参与散列：同一对 SMILES 的 dimer（缩合二聚体·X）与 pair
    （任意双分子 A···B）结果互不命中。pair 模式下 dimer_smiles 位
    传分子 A 的 canonical SMILES、x_cache_part 为 "pair:<canon_b>"。
    backend 参与散列：xTB 快速档与 Psi4 精度档结果互不命中；
    backend="xtb" 保持旧串格式以兼容存量缓存。
    sampler_tag（如 "mc0"/"mc12"）参与散列：MC 取向采样引入后，
    旧单取向口径的缓存不被误命中（gfnff 档不采样，传 None 保持旧格式）。
    """
    if backend == "xtb":
        raw = f"{mode}::{dimer_smiles}::{x_cache_part}::{method}"
    else:
        raw = f"{backend}::{mode}::{dimer_smiles}::{x_cache_part}::{method}"
    if sampler_tag:
        raw += f"::{sampler_tag}"
    return hashlib.sha1(raw.encode()).hexdigest()


def load_cache(key: str) -> dict | None:
    """命中返回缓存结果 dict（自动补 cached=True 由调用方处理）；否则 None。"""
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("DFT 缓存读取失败 %s: %s", path, exc)
        return None


def save_cache(key: str, result: dict) -> None:
    """写缓存；任何失败静默（缓存不是主流程）。"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("DFT 缓存写入失败: %s", exc)


def delete_cache(key: str) -> bool:
    """删除指定 key 的缓存文件；返回是否确实删除（存在且删除成功）。"""
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except Exception as exc:
        logger.warning("DFT 缓存删除失败 %s: %s", path, exc)
        return False


def cache_entry_count() -> int:
    """当前缓存条目数（诊断/界面反馈用）。"""
    try:
        return len(list(CACHE_DIR.glob("*.json")))
    except Exception:
        return 0
