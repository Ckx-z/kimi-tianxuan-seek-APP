"""3D/二聚体描述符缓存。

避免每次重新生成 RDKit 构象，显著加速全量特征计算。
缓存策略：
  - 单体 3D：按 SMILES 字符串缓存
  - 二聚体 3D：按 (ald_smiles, amine_smiles) 元组缓存
存储格式：joblib（轻量 dict）
"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from typing import Optional

import joblib

from .conformer import DESCRIPTOR_NAMES, compute_3d_descriptors
from .dimer import DIMER_DESCRIPTOR_NAMES, compute_dimer_3d


try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
DEFAULT_CACHE_DIR = runtime_config.user_data_root() / "interim" / "3d_cache"
DEFAULT_MONOMER_CACHE = DEFAULT_CACHE_DIR / "monomer_3d.joblib"
DEFAULT_DIMER_CACHE = DEFAULT_CACHE_DIR / "dimer_3d.joblib"

# 二聚体构象生成超时（秒）；部分含硫/大环分子可能极慢
DEFAULT_DIMER_TIMEOUT = 15


def _load_cache(path: Path) -> dict:
    """安全加载缓存文件。"""
    if path.exists():
        try:
            return joblib.load(path)
        except Exception:
            return {}
    return {}


def _save_cache(path: Path, cache: dict) -> None:
    """安全保存缓存文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先写入临时文件再重命名，防止中断导致缓存损坏
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    joblib.dump(cache, tmp_path)
    tmp_path.replace(path)


def get_monomer_3d(smiles: str,
                   n_confs: int = 5,
                   seed: int = 42,
                   cache_path: Optional[Path] = None) -> Optional[list[float]]:
    """带缓存的单体 3D 描述符。

    Args:
        smiles: 单体 SMILES
        n_confs: 构象数
        seed: 随机种子
        cache_path: 缓存文件路径，默认 data/interim/3d_cache/monomer_3d.joblib

    Returns:
        10 维描述符列表，失败返回 None
    """
    if not smiles or not isinstance(smiles, str):
        return None

    if cache_path is None:
        cache_path = DEFAULT_MONOMER_CACHE

    cache = _load_cache(cache_path)
    if smiles in cache:
        return cache[smiles]

    desc = compute_3d_descriptors(smiles, n_confs=n_confs, seed=seed)
    if desc is not None:
        cache[smiles] = desc
        _save_cache(cache_path, cache)
    return desc


def _compute_dimer_worker(ald_smiles: str,
                          amine_smiles: str,
                          n_confs: int,
                          seed: int,
                          queue: "mp.Queue") -> None:
    """子进程工作函数：计算二聚体 3D 并将结果放入队列。"""
    try:
        desc = compute_dimer_3d(ald_smiles, amine_smiles, n_confs=n_confs, seed=seed)
        queue.put(desc)
    except Exception:
        queue.put(None)


def _compute_dimer_with_timeout(ald_smiles: str,
                                amine_smiles: str,
                                n_confs: int = 5,
                                seed: int = 42,
                                timeout: int = DEFAULT_DIMER_TIMEOUT) -> Optional[list[float]]:
    """带超时控制的二聚体 3D 计算。

    部分含硫、金属或大环分子会导致 RDKit 构象生成/优化极慢甚至挂起，
    用子进程 + join(timeout) 强制终止。
    """
    queue = mp.Queue()
    process = mp.Process(
        target=_compute_dimer_worker,
        args=(ald_smiles, amine_smiles, n_confs, seed, queue),
    )
    process.start()
    process.join(timeout)

    if process.is_alive():
        process.terminate()
        process.join()
        return None

    try:
        return queue.get_nowait()
    except Exception:
        return None


def get_dimer_3d(ald_smiles: str,
                 amine_smiles: str,
                 n_confs: int = 5,
                 seed: int = 42,
                 cache_path: Optional[Path] = None,
                 timeout: int = DEFAULT_DIMER_TIMEOUT) -> Optional[list[float]]:
    """带缓存的二聚体 3D 描述符。

    Args:
        ald_smiles: 醛 SMILES
        amine_smiles: 胺 SMILES
        n_confs: 构象数
        seed: 随机种子
        cache_path: 缓存文件路径，默认 data/interim/3d_cache/dimer_3d.joblib
        timeout: 单对二聚体计算超时（秒）

    Returns:
        10 维描述符列表，失败或超时返回 None
    """
    if not ald_smiles or not amine_smiles:
        return None

    if cache_path is None:
        cache_path = DEFAULT_DIMER_CACHE

    cache = _load_cache(cache_path)
    key = (ald_smiles, amine_smiles)
    if key in cache:
        return cache[key]

    desc = _compute_dimer_with_timeout(
        ald_smiles, amine_smiles, n_confs=n_confs, seed=seed, timeout=timeout
    )
    if desc is not None:
        cache[key] = desc
        _save_cache(cache_path, cache)
    return desc


def clear_3d_cache(cache_dir: Optional[Path] = None) -> None:
    """清空 3D 缓存（谨慎使用）。"""
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    for path in cache_dir.glob("*.joblib"):
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    # 简单缓存测试
    ald = "O=Cc1ccccc1"
    amine = "Nc1ccccc1"
    print("monomer:", get_monomer_3d(ald) is not None)
    print("dimer:", get_dimer_3d(ald, amine) is not None)
