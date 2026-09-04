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

try:
    from literature import resolver as lit_resolver  # type: ignore
except ImportError:  # 包路径导入
    from src.literature import resolver as lit_resolver

PROJECT_ROOT = runtime_config.resource_root()
# 用户数据（可写）：frozen 时落 %APPDATA%/COF-Film-Recommend/data
FAVORITES_DIR = runtime_config.user_data_root() / "favorites"
BUILTIN_PATH = PROJECT_ROOT / "data" / "builtin_monomers.json"
TRAIN_CSV = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"

_ID_RE = re.compile(r"^fav_(\d{8})_(\d{3})$")
_FOLDER_ID_RE = re.compile(r"^folder_[\w-]{1,32}$")

# 收藏夹 Folder 存储文件（与 favorites/ 目录同级，测试 monkeypatch
# FAVORITES_DIR 时随之隔离，与 prediction_log 同口径）
FOLDERS_FILENAME = "favorite_folders.json"
DEFAULT_FOLDER_ID = "folder_default"
DEFAULT_FOLDER_NAME = "收藏夹1"

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


# ---------------------------------------------------------------- 预测快照回填（页① React 链路兼容）

def _prediction_log_path() -> Path:
    """预测日志路径：与 FAVORITES_DIR 同级的 prediction_log.jsonl。

    由 FAVORITES_DIR 推导而非重新算 user_data_root，保证测试 monkeypatch
    FAVORITES_DIR 到 tmp 时读取的日志同样隔离。
    """
    return FAVORITES_DIR.parent / "prediction_log.jsonl"


def _snapshot_from_log(ald_smiles: str, amine_smiles: str) -> dict | None:
    """从 prediction_log.jsonl 反查该单体对最近一次预测，组装快照。

    React/FastAPI 链路打分只写 prediction_log（不回写收藏），导致收藏页误显
    「未打分」；此函数按 RDKit 规范化 SMILES 匹配日志，取最后一条（日志按时间
    追加，越靠后越新）。返回 latest_prediction 同口径 dict；无命中返回 None。
    """
    ca, cm = _canonical(ald_smiles), _canonical(amine_smiles)
    if not ca or not cm:
        return None
    path = _prediction_log_path()
    if not path.exists():
        return None
    best: dict | None = None
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "prediction":
                    continue
                if _canonical(str(rec.get("ald_smiles") or "")) == ca and \
                        _canonical(str(rec.get("amine_smiles") or "")) == cm:
                    best = rec
    except Exception as exc:
        logger.warning("prediction_log 读取失败: %s", exc)
        return None
    if best is None:
        return None
    snap = {
        "score": best.get("score"),
        "std": best.get("std"),
        "arm": best.get("arm") or "",
        "ood": best.get("ood_level") or "none",
        "date": str(best.get("timestamp") or ""),
    }
    for k, src in (("score_policy", "score_policy"),
                   ("score_flags", "score_flags"),
                   ("tree_score", "tree_score"),
                   ("gnn_score", "gnn_score")):
        if best.get(src) is not None:
            snap[k] = best[src]
    return snap


def _ensure_snapshot(fav: dict) -> dict:
    """latest_prediction 无有效分数时从预测日志回填（读时兼容旧数据，同时落盘）。"""
    p = fav.get("latest_prediction")
    if isinstance(p, dict) and p.get("score") is not None:
        return fav
    snap = _snapshot_from_log(
        str((fav.get("aldehyde") or {}).get("smiles") or ""),
        str((fav.get("amine") or {}).get("smiles") or ""))
    if snap is None:
        return fav
    fav["latest_prediction"] = snap
    try:
        _write(fav)
    except Exception as exc:  # 回填落盘失败不影响读取
        logger.warning("快照回填落盘失败 %s: %s", fav.get("id"), exc)
    return fav


# ---------------------------------------------------------------- 收藏夹 Folder（P2）
#
# Folder {id, name, created_at} 集中存于 favorite_folders.json；
# 收藏条目新增 folder_id（归入某夹）与预留字段 dft_snapshot（可空，
# DFT 计算后续批次接入，本期仅字段与 API 透传）。

def _folders_path() -> Path:
    """收藏夹文件路径：由 FAVORITES_DIR 推导（同 prediction_log 口径）。"""
    return FAVORITES_DIR.parent / FOLDERS_FILENAME


def _load_folders() -> list[dict]:
    """读取全部收藏夹；文件缺失/损坏返回 []。"""
    path = _folders_path()
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        folders = obj.get("folders") if isinstance(obj, dict) else None
        if not isinstance(folders, list):
            return []
        return [f for f in folders
                if isinstance(f, dict) and isinstance(f.get("id"), str)]
    except Exception as exc:
        logger.warning("收藏夹文件读取失败 %s: %s", path.name, exc)
        return []


def _save_folders(folders: list[dict]) -> None:
    path = _folders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"folders": folders}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


def _ensure_default_folder() -> dict:
    """兜底夹：无任何收藏夹时自动创建默认「收藏夹1」。

    幂等：已存在任意收藏夹则直接返回第一个，不重复创建。
    """
    folders = _load_folders()
    if folders:
        return folders[0]
    default = {
        "id": DEFAULT_FOLDER_ID,
        "name": DEFAULT_FOLDER_NAME,
        "created_at": _now_iso(),
    }
    _save_folders([default])
    return default


def get_folder(folder_id: str) -> dict | None:
    for f in _load_folders():
        if f.get("id") == folder_id:
            return f
    return None


def list_folders() -> list[dict]:
    """全部收藏夹（按创建时间升序），每项附带 favorite_count。"""
    folders = _load_folders()
    counts: dict[str, int] = {}
    if FAVORITES_DIR.exists():
        for p in sorted(FAVORITES_DIR.glob("fav_*.json")):
            fav = _read_file(p)
            if fav and _ID_RE.match(str(fav.get("id", ""))):
                fid = str(fav.get("folder_id") or "")
                counts[fid] = counts.get(fid, 0) + 1
    return [{**f, "favorite_count": counts.get(str(f.get("id")), 0)}
            for f in folders]


def create_folder(name: str) -> dict:
    """新建收藏夹；名称为空或重名抛 ValueError（中文提示，路由转 400）。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("收藏夹名称不能为空")
    folders = _load_folders()
    if any(str(f.get("name")) == name for f in folders):
        raise ValueError(f"已存在同名收藏夹：{name}")
    import uuid

    folder = {
        "id": f"folder_{uuid.uuid4().hex[:8]}",
        "name": name,
        "created_at": _now_iso(),
    }
    folders.append(folder)
    _save_folders(folders)
    return folder


def rename_folder(folder_id: str, name: str) -> dict:
    """收藏夹改名；不存在抛 KeyError，空名/重名抛 ValueError。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("收藏夹名称不能为空")
    folders = _load_folders()
    target = next((f for f in folders if f.get("id") == folder_id), None)
    if target is None:
        raise KeyError(f"收藏夹不存在: {folder_id}")
    if any(f.get("id") != folder_id and str(f.get("name")) == name
           for f in folders):
        raise ValueError(f"已存在同名收藏夹：{name}")
    target["name"] = name
    _save_folders(folders)
    return target


def delete_folder(folder_id: str) -> int:
    """删除收藏夹并连带删除夹内全部收藏，返回删除的收藏条数。

    不存在抛 KeyError；禁止删除最后一个收藏夹（兜底夹保护，ValueError）。
    """
    folders = _load_folders()
    target = next((f for f in folders if f.get("id") == folder_id), None)
    if target is None:
        raise KeyError(f"收藏夹不存在: {folder_id}")
    if len(folders) <= 1:
        raise ValueError("至少需保留一个收藏夹，不能删除最后一个收藏夹")
    deleted = 0
    for fav in list_favorites():
        if str(fav.get("folder_id") or "") == folder_id:
            if delete_favorite(str(fav.get("id"))):
                deleted += 1
    _save_folders([f for f in folders if f.get("id") != folder_id])
    return deleted


def _migrate_dft_entries(fav: dict) -> bool:
    """dft_entries 迁移（幂等）：旧单条 dft_snapshot 包成列表首条。

    - dft_entries 缺失/非 list → 初始化为 []；
    - 旧 dft_snapshot 是非空 dict 且 dft_entries 为空 → 包成 [snapshot]，
      落盘的 dft_snapshot 置 None（GET 响应层再回填最新一条，见
      _dft_response_view）；
    返回是否有变更。
    """
    changed = False
    entries = fav.get("dft_entries")
    if not isinstance(entries, list):
        entries = []
        changed = True
    snap = fav.get("dft_snapshot")
    if isinstance(snap, dict) and snap and not entries:
        entries = [snap]
        fav["dft_snapshot"] = None
        changed = True
    fav["dft_entries"] = entries
    return changed


def _dft_response_view(fav: dict) -> dict:
    """GET 响应兼容旧前端：dft_snapshot 回填为 dft_entries 最新一条。

    仅改内存视图不落盘——磁盘上 dft_snapshot 保持迁移后的 None，
    旧前端读 dft_snapshot 拿到的就是最近一次 DFT 计算，不会炸。
    """
    entries = fav.get("dft_entries")
    if isinstance(entries, list) and entries:
        fav["dft_snapshot"] = entries[-1]
    return fav


def _references_response_view(fav: dict) -> dict:
    """GET 响应 enrichment：auto-matched 编号引用解析为真实标题/DOI/URL。

    仅改内存视图不落盘（与 _dft_response_view 同做法）——磁盘上旧格式
    {"title": 编号, "doi": ""} 保持不变，响应里 title/doi/url 已解析。
    解析失败不影响读取（resolver 内部兜底，原引用原样返回）。
    """
    refs = fav.get("references")
    if isinstance(refs, list) and refs:
        try:
            fav["references"] = lit_resolver.enrich_references(refs)
        except Exception as exc:
            logger.warning("references enrichment 失败 %s: %s", fav.get("id"), exc)
    return fav


def _response_view(fav: dict) -> dict:
    """GET 响应统一视图：DFT 快照回填 + 文献引用 enrichment（均内存视图）。"""
    return _references_response_view(_dft_response_view(fav))


def _ensure_folder_fields(fav: dict) -> dict:
    """旧格式迁移（幂等）：补 folder_id（归兜底夹）、dft_snapshot（None）
    与 dft_entries（旧快照包成列表）。

    仅在有变更时落盘；重复启动不会重复建夹或重复改写。
    """
    changed = False
    if "dft_snapshot" not in fav:
        fav["dft_snapshot"] = None
        changed = True
    if _migrate_dft_entries(fav):
        changed = True
    fid = fav.get("folder_id")
    if not isinstance(fid, str) or not fid or get_folder(fid) is None:
        fav["folder_id"] = _ensure_default_folder()["id"]
        changed = True
    if changed:
        try:
            _write(fav)
        except Exception as exc:  # 迁移落盘失败不影响读取
            logger.warning("收藏迁移落盘失败 %s: %s", fav.get("id"), exc)
    return fav


def find_favorite_by_pair(aldehyde_smiles: str, amine_smiles: str) -> dict | None:
    """按规范化 SMILES 找同单体对的既有收藏（交叉合并去重用）。

    无法规范化（非法 SMILES）时返回 None —— 不做去重拦截。
    """
    ca, cm = _canonical(aldehyde_smiles), _canonical(amine_smiles)
    if not ca or not cm:
        return None
    for fav in list_favorites():
        if _canonical(str((fav.get("aldehyde") or {}).get("smiles") or "")) == ca \
                and _canonical(str((fav.get("amine") or {}).get("smiles") or "")) == cm:
            return fav
    return None


# ---------------------------------------------------------------- 文献自动匹配

def auto_match_references(
    aldehyde_smiles: str, amine_smiles: str, max_refs: int = 8
) -> list[dict]:
    """在训练语料（v5_train_stage1_cond_filled.csv）反查报道过该醛/胺的文献。

    返回 [{"paper_id", "title": 真实标题（解析失败回退编号）, "doi", "url",
           "source": "auto-matched", "path_or_url": "",
           "match_type": "both|aldehyde|amine",
           "count": 出现次数, "note": "报道过该醛/胺/组合"}]
    标题/DOI/URL 由 literature.resolver 按 paper_id 从文献库解析；
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
            paper = lit_resolver.resolve_paper(pid) or {}
            for mtype in ("both", "aldehyde", "amine"):
                if st[mtype] > 0:
                    refs.append(
                        {
                            "paper_id": pid,
                            "title": paper.get("title") or pid,
                            "doi": paper.get("doi") or "",
                            "url": paper.get("url"),
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
    prediction: dict | None = None,
    folder_id: str | None = None,
    dft_snapshot: dict | None = None,
) -> dict:
    """新建收藏条目并落盘，返回完整条目 dict。

    CAS/name 自动从内置库反查；创建时自动匹配训练文献挂为 references。
    folder_id 指定归属收藏夹，缺省归兜底夹（收藏夹1）；指定的收藏夹
    不存在时抛 ValueError。dft_snapshot 为预留字段（本期仅透传落盘，
    默认 None）。同单体对去重由路由层 409 拦截，本函数不强制。
    prediction 为可选的当前打分快照（{score, std, ood, score_policy,
    tree_score, gnn_score}），提供且 score 非空时直接写入 latest_prediction，
    避免「查询打分后立即收藏，我的页显示未打分」。
    """
    if folder_id:
        folder = get_folder(folder_id)
        if folder is None:
            raise ValueError(f"收藏夹不存在: {folder_id}")
    else:
        folder = _ensure_default_folder()
    latest_prediction = None
    if isinstance(prediction, dict) and prediction.get("score") is not None:
        latest_prediction = {
            "score": prediction.get("score"),
            "std": prediction.get("std"),
            "arm": prediction.get("arm", "") or "",
            "ood": prediction.get("ood", "none") or "none",
            "date": _now_iso(),
        }
        for k in ("score_policy", "score_flags", "tree_score", "gnn_score"):
            if prediction.get(k) is not None:
                latest_prediction[k] = prediction[k]
    fav = {
        "id": _next_id(),
        "folder_id": folder["id"],
        "aldehyde": _monomer_obj(aldehyde_smiles, ald_name),
        "amine": _monomer_obj(amine_smiles, amine_name),
        "created_at": _now_iso(),
        "notes": notes or "",
        "latest_prediction": latest_prediction,
        "dft_snapshot": None,
        "dft_entries": ([dft_snapshot]
                        if isinstance(dft_snapshot, dict) and dft_snapshot
                        else []),
        "references": auto_match_references(aldehyde_smiles, amine_smiles),
        "experiment_record_ids": [],
    }
    _write(fav)
    return _response_view(fav)


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
    return [_response_view(_ensure_snapshot(_ensure_folder_fields(f)))
            for f in favs]


def get_favorite(fav_id: str) -> dict | None:
    """按 id 取收藏条目；不存在/损坏返回 None。"""
    path = _path_of(fav_id)
    fav = _read_file(path) if path else None
    if not fav:
        return None
    return _response_view(_ensure_snapshot(_ensure_folder_fields(fav)))


def copy_favorite(fav_id: str, folder_id: str) -> dict:
    """把收藏复制到目标收藏夹，返回新收藏条目。

    单体信息、latest_prediction 打分快照、dft_entries/dft_snapshot、notes、
    references 全量深拷贝；id 新生成、created_at 取当前时间。
    experiment_record_ids 不随复制（实验记录归属原收藏，避免同一条记录
    出现在两个组里）。原收藏不存在抛 KeyError；目标收藏夹不存在抛
    ValueError。
    """
    src = get_favorite(fav_id)
    if src is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    folder = get_folder(folder_id)
    if folder is None:
        raise ValueError(f"收藏夹不存在: {folder_id}")
    fav = json.loads(json.dumps(src, ensure_ascii=False))  # 深拷贝，断别名
    fav["id"] = _next_id()
    fav["folder_id"] = folder["id"]
    fav["created_at"] = _now_iso()
    fav["experiment_record_ids"] = []
    _write(fav)
    return _response_view(fav)


def add_dft_entry(fav_id: str, entry: dict) -> dict:
    """追加一条 DFT 计算条目到 dft_entries，返回完整收藏（响应视图）。

    条目内容透传前端快照（job_id/x_type/x_smiles/e_bind_kcal 等），后端
    不强校验；缺 created_at 时补当前时间。落盘的 dft_snapshot 保持 None，
    GET 响应由 _dft_response_view 回填最新一条。收藏不存在抛 KeyError，
    条目非非空 dict 抛 ValueError。
    """
    if not isinstance(entry, dict) or not entry:
        raise ValueError("DFT 条目必须是非空 JSON 对象")
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    entry = dict(entry)
    if not str(entry.get("created_at") or "").strip():
        entry["created_at"] = _now_iso()
    entries = fav.get("dft_entries")
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    fav["dft_entries"] = entries
    fav["dft_snapshot"] = None
    _write(fav)
    return _response_view(fav)


def update_favorite(fav_id: str, **fields) -> dict:
    """更新收藏条目任意字段（id 不可改），返回更新后的完整条目。

    指定 folder_id（移夹）时校验目标收藏夹存在，否则抛 ValueError；
    条目不存在时抛 KeyError。
    """
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    fields.pop("id", None)
    fid = fields.get("folder_id")
    if isinstance(fid, str) and fid and get_folder(fid) is None:
        raise ValueError(f"收藏夹不存在: {fid}")
    # 旧前端路径兼容：显式传 dft_snapshot 且 dft_entries 为空时，
    # 同时包进 dft_entries（新结构以 dft_entries 为准）
    snap = fields.get("dft_snapshot")
    if isinstance(snap, dict) and snap:
        entries = fav.get("dft_entries")
        if isinstance(entries, list) and not entries:
            fields = dict(fields)
            fields["dft_entries"] = [snap]
    fav.update(fields)
    _write(fav)
    return _response_view(fav)


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
    for k in ("score_policy", "score_flags", "tree_score", "gnn_score"):
        if prediction.get(k) is not None:
            fav["latest_prediction"][k] = prediction[k]
    return _write(fav)


def update_snapshot_for_pair(
    aldehyde_smiles: str, amine_smiles: str, prediction: dict
) -> int:
    """FastAPI /api/predict 打分后：按单体对回写所有匹配收藏的快照。

    同一对单体可能被重复收藏（多条 fav_*），逐条回写。返回更新条数。
    任何单条失败不影响其余，不抛异常。
    """
    ca, cm = _canonical(aldehyde_smiles), _canonical(amine_smiles)
    if not ca or not cm:
        return 0
    n = 0
    for fav in list_favorites():
        try:
            if _canonical(str((fav.get("aldehyde") or {}).get("smiles") or "")) == ca \
                    and _canonical(str((fav.get("amine") or {}).get("smiles") or "")) == cm:
                update_prediction_snapshot(str(fav.get("id")), prediction)
                n += 1
        except Exception as exc:
            logger.warning("按单体对回写快照失败 %s: %s", fav.get("id"), exc)
    return n


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
