"""训练集组合池（v1.6.1）：判断某个醛+胺**组合**是否在训练集中出现过。

与 routing.MonomerPool（单体级）互补：单体都见过不代表组合见过。
组合未见时 GNN 外推可信度低——FilmPredictor 会标记 pair_seen=False，
主分口径对 GNN 分量做收缩（见 api/deps.headline_score）。

数据源：data/interim/v5_train_stage1_cond_filled.csv（打包时已随 datas 携带，
frozen 下同样可读）。加载惰性 + 常驻内存；读取失败降级为「视为见过」
（不收缩，绝不因此阻塞打分主流程）。
"""

from __future__ import annotations

import logging

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

PAIR_POOL_CSV = runtime_config.resource_root() / "data" / "interim" \
    / "v5_train_stage1_cond_filled.csv"

_pair_set: set | None = None
_load_failed = False


def _key(ald_smiles: str, amine_smiles: str) -> str:
    return f"{ald_smiles}\t{amine_smiles}"


def load_pair_set() -> set | None:
    """惰性加载训练组合集合；失败返回 None（调用方按「见过」处理）。"""
    global _pair_set, _load_failed
    if _pair_set is not None:
        return _pair_set
    if _load_failed:
        return None
    try:
        if not PAIR_POOL_CSV.is_file():
            _load_failed = True
            return None
        out: set = set()
        with open(PAIR_POOL_CSV, encoding="utf-8-sig") as f:
            header = None
            for line in f:
                cols = line.rstrip("\n").split(",")
                if header is None:
                    header = {c: i for i, c in enumerate(cols)}
                    continue
                a = (header.get("aldehyde_smiles"), header.get("amine_smiles"))
                if a[0] is None or a[1] is None:
                    _load_failed = True
                    return None
                out.add(_key(cols[a[0]], cols[a[1]]))
        _pair_set = out
        logger.info("训练组合池加载完成：%d 对", len(out))
        return out
    except Exception as exc:  # 读取失败降级，不影响打分
        logger.warning("训练组合池加载失败（按全见过处理）: %s", exc)
        _load_failed = True
        return None


def pair_seen(ald_smiles: str, amine_smiles: str) -> bool:
    """该组合是否在训练集中出现过；池不可用时按 True（不收缩）。"""
    pool = load_pair_set()
    if pool is None:
        return True
    return _key(ald_smiles, amine_smiles) in pool
