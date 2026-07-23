"""收藏夹存储（P2 后端，页③支撑）。

每条收藏一个 JSON 文件：data/favorites/fav_<YYYYMMDD>_<NNN>.json
schema 见 docs/APP_REDESIGN_PROPOSAL.md 第 3 节③：
{id, aldehyde/amine{smiles,cas,name}, created_at, notes,
 latest_prediction, references[], experiment_record_ids[]}

- CAS/name 自动从 data/builtin_monomers.json 反查填充（按 RDKit 规范化
  SMILES 匹配）；
- 创建时自动调 auto_match_references 挂训练文献；
- 所有写操作失败抛异常由调用方处理；读操作对损坏文件跳过不炸。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:  # 裸名导入（src/ 直接在 sys.path 上）
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
# 用户数据（可写）：frozen 时落 %APPDATA%/COF-Film-Recommend/data
FAVORITES_DIR = runtime_config.user_data_root() / "favorites"
BUILTIN_PATH = PROJECT_ROOT / "data" / "builtin_monomers.json"
TRAIN_CSV = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"

_ID_RE = re.compile(r"^fav_(\d{8})_(\d{3})$")

# 文献匹配类型 → 中文说明
_MATCH_NOTES = {
    "both": "报道过该醛胺组合",
    "aldehyde": "报道过该醛单体",
    "amine": "报道过该胺单体",
}


# ---------------------------------------------------------------- 工具

def _now_iso() -> str:
    """ISO 8601 带本地时区，秒级精度。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical(smiles: str) -> str | None:
    """RDKit 规范化 SMILES；解析失败返回 None。"""
    if not smiles or not isinstance(smiles, str):
        return None
    try:
        from rdkit import Chem

        mol = Chem.MolFromSmiles(smiles.strip())
        if mol is None:
            return None
        return Chem.MolToSmiles(mol)
    except Exception:
        return None


def _load_builtin() -> list[dict]:
    """加载内置单体库；失败返回空列表。"""
    try:
        items = json.loads(BUILTIN_PATH.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except Exception as exc:
        logger.warning("内置单体库加载失败: %s", exc)
        return []


def _lookup_builtin(smiles: str) -> dict:
    """按规范化 SMILES 反查内置库，返回 {"cas","name"}；未命中返回空串字段。"""
    canon = _canonical(smiles)
    if canon:
        for m in _load_builtin():
            if _canonical(m.get("smiles", "")) == canon:
                return {"cas": m.get("cas", ""), "name": m.get("name", "")}
    return {"cas": "", "name": ""}


def _monomer_obj(smiles: str, name: str = "") -> dict:
    """构造单体对象 {smiles, cas, name}，CAS/name 自动从内置库反查。

    smiles 保存规范化形式（可解析时）；显式传入的 name 优先于库内名称。
    """
    smiles = (smiles or "").strip()
    canon = _canonical(smiles)
    hit = _lookup_builtin(smiles)
    return {
        "smiles": canon or smiles,
        "cas": hit["cas"],
        "name": (name or "").strip() or hit["name"],
    }


def _next_id() -> str:
    """生成 fav_<YYYYMMDD>_<NNN>，按当日已有文件取最大序号 +1。"""
    today = datetime.now().strftime("%Y%m%d")
    max_n = 0
    if FAVORITES_DIR.exists():
        for p in FAVORITES_DIR.glob("fav_*.json"):
            m = _ID_RE.match(p.stem)
            if m and m.group(1) == today:
                max_n = max(max_n, int(m.group(2)))
    return f"fav_{today}_{max_n + 1:03d}"


def _read_file(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as exc:
        logger.warning("收藏文件读取失败 %s: %s", path.name, exc)
        return None


def _write(fav: dict) -> dict:
    FAVORITES_DIR.mkdir(parents=True, exist_ok=True)
    path = FAVORITES_DIR / f"{fav['id']}.json"
    path.write_text(
        json.dumps(fav, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return fav


def _path_of(fav_id: str) -> Path | None:
    """fav_id → 文件路径；id 非法或文件不存在返回 None。"""
    if not fav_id or not isinstance(fav_id, str) or not _ID_RE.match(fav_id):
        return None
    path = FAVORITES_DIR / f"{fav_id}.json"
    return path if path.exists() else None


# ---------------------------------------------------------------- 文献自动匹配

def auto_match_references(
    aldehyde_smiles: str, amine_smiles: str, max_refs: int = 8
) -> list[dict]:
    """在训练语料（v5_train_stage1_cond_filled.csv）反查报道过该醛/胺的文献。

    返回 [{"title": paper_id, "doi": "", "source": "auto-matched",
           "path_or_url": "", "match_type": "both|aldehyde|amine",
           "count": 出现次数, "note": "报道过该醛/胺/组合"}]
    both（同 paper 同组合）优先，其次按出现次数降序；最多 max_refs 条。
    输入无法解析或数据缺失时返回 []，不抛异常。
    """
    try:
        import pandas as pd

        canon_ald = _canonical(aldehyde_smiles)
        canon_amine = _canonical(amine_smiles)
        if canon_ald is None and canon_amine is None:
            return []
        if not TRAIN_CSV.exists():
            logger.warning("训练数据不存在: %s", TRAIN_CSV)
            return []

        df = pd.read_csv(
            TRAIN_CSV,
            usecols=["paper_id", "aldehyde_smiles", "amine_smiles"],
        )

        # 规范化缓存，避免对重复 SMILES 反复调 RDKit
        canon_cache: dict[str, str | None] = {}

        def _canon_cached(s: str) -> str | None:
            s = str(s)
            if s not in canon_cache:
                canon_cache[s] = _canonical(s)
            return canon_cache[s]

        # paper_id -> {"both": n, "aldehyde": n, "amine": n}
        stats: dict[str, dict[str, int]] = {}
        for row in df.itertuples(index=False):
            hit_ald = canon_ald is not None and _canon_cached(row.aldehyde_smiles) == canon_ald
            hit_amine = canon_amine is not None and _canon_cached(row.amine_smiles) == canon_amine
            if not (hit_ald or hit_amine):
                continue
            pid = str(row.paper_id)
            st = stats.setdefault(pid, {"both": 0, "aldehyde": 0, "amine": 0})
            if hit_ald and hit_amine:
                st["both"] += 1
            elif hit_ald:
                st["aldehyde"] += 1
            else:
                st["amine"] += 1

        order = {"both": 0, "aldehyde": 1, "amine": 2}
        refs = []
        for pid, st in stats.items():
            for mtype in ("both", "aldehyde", "amine"):
                if st[mtype] > 0:
                    refs.append(
                        {
                            "title": pid,
                            "doi": "",
                            "source": "auto-matched",
                            "path_or_url": "",
                            "match_type": mtype,
                            "count": st[mtype],
                            "note": _MATCH_NOTES[mtype],
                        }
                    )
                    break  # 每篇文献只挂最强的一种匹配
        refs.sort(key=lambda r: (order[r["match_type"]], -r["count"]))
        return refs[: max(int(max_refs), 0)]
    except Exception as exc:  # 兜底：匹配失败绝不阻塞收藏主流程
        logger.warning("auto_match_references 异常: %s", exc)
        return []


# ---------------------------------------------------------------- CRUD

def add_favorite(
    aldehyde_smiles: str,
    amine_smiles: str,
    ald_name: str = "",
    amine_name: str = "",
    notes: str = "",
) -> dict:
    """新建收藏条目并落盘，返回完整条目 dict。

    CAS/name 自动从内置库反查；创建时自动匹配训练文献挂为 references。
    同一对单体重复收藏会创建多条（去重由 UI 层提示，不在此处强制）。
    """
    fav = {
        "id": _next_id(),
        "aldehyde": _monomer_obj(aldehyde_smiles, ald_name),
        "amine": _monomer_obj(amine_smiles, amine_name),
        "created_at": _now_iso(),
        "notes": notes or "",
        "latest_prediction": None,
        "references": auto_match_references(aldehyde_smiles, amine_smiles),
        "experiment_record_ids": [],
    }
    return _write(fav)


def list_favorites() -> list[dict]:
    """全部收藏条目，按创建时间倒序（新的在前）；损坏文件跳过。"""
    if not FAVORITES_DIR.exists():
        return []
    favs = []
    for p in sorted(FAVORITES_DIR.glob("fav_*.json")):
        fav = _read_file(p)
        if fav and _ID_RE.match(str(fav.get("id", ""))):
            favs.append(fav)
    favs.sort(key=lambda f: str(f.get("created_at", "")), reverse=True)
    return favs


def get_favorite(fav_id: str) -> dict | None:
    """按 id 取收藏条目；不存在/损坏返回 None。"""
    path = _path_of(fav_id)
    return _read_file(path) if path else None


def update_favorite(fav_id: str, **fields) -> dict:
    """更新收藏条目任意字段（id 不可改），返回更新后的完整条目。

    条目不存在时抛 KeyError。
    """
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    fields.pop("id", None)
    fav.update(fields)
    return _write(fav)


def delete_favorite(fav_id: str) -> bool:
    """删除收藏条目；成功 True，不存在 False。"""
    path = _path_of(fav_id)
    if path is None:
        return False
    path.unlink()
    return True


def update_prediction_snapshot(fav_id: str, prediction: dict) -> dict:
    """页①打分后回写最新打分快照到 latest_prediction，返回更新后的条目。

    prediction 取 {score, std, arm, ood} 四个字段，自动补 date；
    可选透传 {score_policy, tree_score, gnn_score} 口径溯源字段
    （两模型较高值口径；旧调用方不传则省略，向后兼容）。
    条目不存在时抛 KeyError。
    """
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    prediction = prediction or {}
    fav["latest_prediction"] = {
        "score": prediction.get("score"),
        "std": prediction.get("std"),
        "arm": prediction.get("arm", ""),
        "ood": prediction.get("ood", ""),
        "date": _now_iso(),
    }
    for k in ("score_policy", "tree_score", "gnn_score"):
        if prediction.get(k) is not None:
            fav["latest_prediction"][k] = prediction[k]
    return _write(fav)


def add_reference(
    fav_id: str, title: str, doi: str = "", url_or_path: str = "", note: str = ""
) -> dict:
    """手动添加参考文献，返回更新后的完整条目。条目不存在时抛 KeyError。"""
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    fav.setdefault("references", []).append(
        {
            "title": (title or "").strip(),
            "doi": (doi or "").strip(),
            "source": "user-added",
            "path_or_url": (url_or_path or "").strip(),
            "note": (note or "").strip(),
        }
    )
    return _write(fav)
