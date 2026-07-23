"""相似成膜案例推荐（P1 后端支撑）。

在训练文献数据（data/interim/v5_train_stage1_cond_filled.csv）中找出
is_film >= 0.8 的成功配对，用 RDKit Morgan 指纹 Tanimoto 相似度
（醛对醛、胺对胺分别计算再取平均）排序，返回最相似的成功案例。
解析失败 / 无数据时返回空列表，不抛异常。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
TRAIN_CSV = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"

FILM_THRESHOLD = 0.8


def _morgan_fp(smiles: str):
    """SMILES → Morgan 指纹；解析失败返回 None。"""
    if not smiles or not isinstance(smiles, str):
        return None
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        mol = Chem.MolFromSmiles(smiles.strip())
        if mol is None:
            return None
        return AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
    except Exception:
        return None


def find_similar_film_cases(
    ald_smiles: str, amine_smiles: str, top_k: int = 3
) -> list[dict]:
    """查找训练文献中最相似的成功成膜配对。

    返回 [{"aldehyde_smiles", "amine_smiles", "is_film", "paper_id",
    "similarity"}]，按相似度降序；输入无法解析或无数据时返回 []。
    """
    try:
        import pandas as pd
        from rdkit import DataStructs

        fp_ald = _morgan_fp(ald_smiles)
        fp_amine = _morgan_fp(amine_smiles)
        if fp_ald is None or fp_amine is None:
            return []
        if not TRAIN_CSV.exists():
            logger.warning("训练数据不存在: %s", TRAIN_CSV)
            return []

        df = pd.read_csv(
            TRAIN_CSV,
            usecols=["aldehyde_smiles", "amine_smiles", "is_film", "paper_id"],
        )
        df = df[pd.to_numeric(df["is_film"], errors="coerce") >= FILM_THRESHOLD]
        if df.empty:
            return []

        results = []
        for row in df.itertuples(index=False):
            fa = _morgan_fp(row.aldehyde_smiles)
            fb = _morgan_fp(row.amine_smiles)
            if fa is None or fb is None:
                continue
            sim = (
                DataStructs.TanimotoSimilarity(fp_ald, fa)
                + DataStructs.TanimotoSimilarity(fp_amine, fb)
            ) / 2.0
            results.append(
                {
                    "aldehyde_smiles": row.aldehyde_smiles,
                    "amine_smiles": row.amine_smiles,
                    "is_film": float(row.is_film),
                    "paper_id": str(row.paper_id),
                    "similarity": round(float(sim), 4),
                }
            )
        results.sort(key=lambda r: r["similarity"], reverse=True)
        return results[: max(int(top_k), 0)]
    except Exception as exc:  # 兜底：推荐失败绝不影响预测主流程
        logger.warning("find_similar_film_cases 异常: %s", exc)
        return []
