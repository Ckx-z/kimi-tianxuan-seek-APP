"""单体分子指纹特征（阶段 12 / exp_006）。

动机（exp_005 遗留）：双留出（醛胺均未见）下所有模型在 0.63-0.68 挣扎，
更强正则反而更差 → 瓶颈在化学表征而非容量。本模块把醛/胺单体的
Morgan 指纹（ECFP 等价物）/ MACCS 结构键作为定长特征列拼入树模型，
检验子结构表征能否突破双留出瓶颈。

设计要点：
- 指纹只依赖单体 SMILES，不含标签信息 → 全局计算不构成泄漏，
  可在 CV 外一次性算好（与描述符同理）。
- 解析失败的 SMILES 整行补 0（与描述符 3D 失败的兜底策略一致）。
- 按唯一单体缓存指纹，再映射回行：896 醛 + 1366 胺只需 ~2262 次 RDKit 调用。
- 输出列名定长：fp_ald_0..N-1 / fp_amine_0..N-1（N = n_bits；
  MACCS 固定 166 位，丢弃恒为 0 的第 0 位）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import MACCSkeys, rdFingerprintGenerator
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")  # 已知脏 SMILES，静默解析告警（失败补 0）

VALID_KINDS = ("morgan", "maccs")
MACCS_N_BITS = 166  # GenMACCSKeys 返回 167 位，第 0 位为占位，丢弃


def fingerprint_params(kind: str = "morgan", radius: int = 2, n_bits: int = 1024) -> dict:
    """规范化指纹参数（存入 pkl / 报告的自描述 dict）。"""
    kind = kind.lower()
    if kind not in VALID_KINDS:
        raise ValueError(f"未知指纹类型：{kind}，可选 {VALID_KINDS}")
    if kind == "maccs":
        n_bits = MACCS_N_BITS  # MACCS 位长固定，忽略传入值
    return {"kind": kind, "radius": int(radius), "n_bits": int(n_bits)}


def _morgan_fp(smiles: str, radius: int, n_bits: int) -> np.ndarray:
    """单个 SMILES 的 Morgan 指纹（0/1 数组），解析失败返回全 0。"""
    arr = np.zeros(n_bits, dtype=np.uint8)
    mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
    if mol is None:
        return arr
    gen = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
    DataStructs.ConvertToNumpyArray(gen.GetFingerprint(mol), arr)
    return arr


def _maccs_fp(smiles: str) -> np.ndarray:
    """单个 SMILES 的 MACCS 结构键（166 位），解析失败返回全 0。"""
    arr = np.zeros(MACCS_N_BITS + 1, dtype=np.uint8)
    mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
    if mol is not None:
        DataStructs.ConvertToNumpyArray(MACCSkeys.GenMACCSKeys(mol), arr)
    return arr[1:]  # 第 0 位为占位符，丢弃


def monomer_fingerprint(smiles: str, kind: str = "morgan",
                        radius: int = 2, n_bits: int = 1024) -> np.ndarray:
    """单单体指纹入口（参数语义与 fingerprint_params 一致）。"""
    p = fingerprint_params(kind, radius, n_bits)
    if p["kind"] == "maccs":
        return _maccs_fp(smiles)
    return _morgan_fp(smiles, p["radius"], p["n_bits"])


def fingerprint_column_names(kind: str = "morgan", radius: int = 2,
                             n_bits: int = 1024) -> tuple[list[str], list[str]]:
    """返回 (醛侧列名, 胺侧列名)。"""
    p = fingerprint_params(kind, radius, n_bits)
    n = p["n_bits"]
    return ([f"fp_ald_{i}" for i in range(n)],
            [f"fp_amine_{i}" for i in range(n)])


def featurize_fingerprints(df: pd.DataFrame,
                           smiles_cols: tuple[str, str] = ("aldehyde_smiles", "amine_smiles"),
                           kind: str = "morgan", radius: int = 2,
                           n_bits: int = 1024) -> pd.DataFrame:
    """把 DataFrame 中的醛/胺单体对转换为指纹特征矩阵。

    按唯一单体各算一次指纹再映射回行；解析失败的单体整列补 0。
    保留原始 DataFrame 的索引，方便与其他特征矩阵按列拼接。

    Returns:
        (len(df), 2 * n_bits) 的 uint8 DataFrame，
        列 fp_ald_0..N-1 / fp_amine_0..N-1。
    """
    p = fingerprint_params(kind, radius, n_bits)
    ald_cols, amine_cols = fingerprint_column_names(kind, radius, n_bits)

    def _map_column(series: pd.Series, cols: list[str]) -> pd.DataFrame:
        cache: dict[str, np.ndarray] = {}
        for s in series.unique():
            cache[s] = monomer_fingerprint(s, p["kind"], p["radius"], p["n_bits"])
        mat = np.vstack([cache[s] for s in series])
        return pd.DataFrame(mat, columns=cols, index=series.index)

    fp_ald = _map_column(df[smiles_cols[0]], ald_cols)
    fp_amine = _map_column(df[smiles_cols[1]], amine_cols)
    return pd.concat([fp_ald, fp_amine], axis=1)
