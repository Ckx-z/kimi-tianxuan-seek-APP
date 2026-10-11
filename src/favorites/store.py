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

import hashlib
import json
import logging
import re
import sys
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

# ------------------------------------------------ 档案卡 / DFT 记录（v220）
#
# 档案卡分两类（读取时按 dft_entries 派生的响应字段 archive_type）：
# - cof_monomer_group：COF 单体组档案卡（单体信息 / 打分结果 / 单体性质 /
#   实验方案卡 / 参考文献 / 该组实验记录 / DFT 计算记录）；
# - dft_pair：双分子 DFT 计算特殊档案卡（来源为「任意双分子（pair）模式」
#   的收藏，单体信息 + DFT 计算记录）。
ARCHIVE_TYPE_COF = "cof_monomer_group"
ARCHIVE_TYPE_PAIR = "dft_pair"

# 条件指纹（condition_key）参与计算的字段名白名单（稳定序 + sha1）
CONDITION_FIELDS = (
    "mode", "backend", "method", "optimize",
    "a_smiles", "b_smiles", "x_type", "solvent_id", "ald2_smiles",
    "amine2_smiles", "custom_smiles", "x_smiles", "dimer_smiles",
    "n_samples", "with_props", "complex_xyz_hash",
)

# 后端任务只透出子集字段，缺的算不出来则留空（None），两边都缺即等价为空
# → 同一真实条件的指纹仍然一致（与 src/dft/jobs.py 的缓存 key 口径同源）。
_JOB_CONDITION_KEYS = (
    "mode", "backend", "method", "optimize", "a_smiles", "b_smiles",
    "x_type", "solvent_id", "ald2_smiles", "amine2_smiles", "custom_smiles",
    "n_samples", "with_props", "complex_xyz_hash", "x_smiles", "dimer_smiles",
)

# 条目内嵌套存储的条件（写盘字段名 conditions）与扁平别名（读取兼容）
_ENTRY_CONDITION_ALIASES = {
    "a_smiles": "xs_a_smiles",
    "b_smiles": "xs_b_smiles",
    "ald2_smiles": "xs_ald2_smiles",
    "amine2_smiles": "xs_amine2_smiles",
}

_LABEL_RE = re.compile(r"^[A-Z]{1,2}$")


# ---------------------------------------------------------------- 条件指纹

def _norm_condition_value(value):
    """指纹归一化：None/空串/空列表统一成 None，其余原样（稳定序列化前置处理）。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return None
    return value


def _xyz_hash(xyz) -> str | None:
    """复合物初猜 xyz 的相对指纹（换行/首尾空白归一后 sha1，不落全文）。"""
    if not isinstance(xyz, str) or not xyz.strip():
        return None
    text = "\n".join(line.rstrip() for line in xyz.strip().splitlines())
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _condition_key(conditions: dict | None) -> str:
    """条件指纹：条件字段稳定序列化（sort_keys + 紧凑分隔符）后 sha1 摘要。

    条件相同 → 指纹相同（视为同一条记录，更新其结果）；任一条件不同 →
    指纹不同（新增一条记录）。conditions 为空/非 dict 时返回 ""，调用方
    视为「无条件信息」，按条目自身内容兜底比较。
    """
    if not isinstance(conditions, dict):
        return ""
    payload = {k: _norm_condition_value(conditions.get(k))
               for k in CONDITION_FIELDS}
    if not any(v is not None for v in payload.values()):
        return ""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _entry_conditions(entry: dict, job: dict | None = None) -> dict:
    """条目 + 任务信息 → 条件字段字典（指纹输入，也是落盘的 conditions）。

    优先级：条目显式 conditions > 后端任务真实参数 > 条目扁平字段。
    """
    merged: dict = {}

    def _put(key: str, value) -> None:
        value = _norm_condition_value(value)
        if value is not None and merged.get(key) is None:
            merged[key] = value

    if isinstance(job, dict):
        job_input = job.get("input")
        if not isinstance(job_input, dict):
            job_input = {}
        for key in _JOB_CONDITION_KEYS:
            if key in ("a_smiles", "b_smiles"):
                _put(key, job_input.get("ald_smiles" if key == "a_smiles"
                                        else "amine_smiles")
                     or job.get("ald_smiles_input" if key == "a_smiles"
                                else "amine_smiles_input"))
                continue
            if key in job_input:
                _put(key, job_input.get(key))
                continue
            _put(key, job.get(key))
        _put("complex_xyz_hash", _xyz_hash(job.get("complex_xyz")))

    raw = entry.get("conditions")
    if isinstance(raw, dict):
        for key in CONDITION_FIELDS:
            if key in raw:
                merged[key] = _norm_condition_value(raw.get(key))

    for key in CONDITION_FIELDS:
        _put(key, entry.get(key))
        alias = _ENTRY_CONDITION_ALIASES.get(key)
        if alias:
            _put(key, entry.get(alias))
    return {k: v for k, v in merged.items() if v is not None}


def _same_conditions(entry_a: dict, entry_b: dict) -> bool:
    """两条记录的显式 conditions 是否完全相同（缺 conditions 时视为不相同）。"""
    cond_a = entry_a.get("conditions")
    cond_b = entry_b.get("conditions")
    if not isinstance(cond_a, dict) or not isinstance(cond_b, dict):
        return False
    return _condition_key(cond_a) == _condition_key(cond_b)


def _entry_mode(entry: dict) -> str:
    """条目计算模式：conditions.mode > 条目 mode 字段，缺省按二聚体模式。"""
    conditions = entry.get("conditions")
    if isinstance(conditions, dict) and conditions.get("mode"):
        return str(conditions.get("mode"))
    return str(entry.get("mode") or "dimer")


def _archive_type_of(entries: list[dict]) -> str:
    """档案卡类型（响应字段）：仅当 DFT 记录全部来自 pair（任意双分子）模式
    → 双分子 DFT 计算特殊档案卡；无记录/含二聚体记录 → COF 单体组档案卡。"""
    modes = [_entry_mode(e) for e in entries if isinstance(e, dict)]
    if modes and all(m == "pair" for m in modes):
        return ARCHIVE_TYPE_PAIR
    return ARCHIVE_TYPE_COF


# ---------------------------------------------------------------- 标号

def _label_at(index: int) -> str:
    """标号序列：0→A … 25→Z，26→AA，27→AB …（双字母，够 700+ 条）。"""
    index = max(int(index), 0)
    letters = ""
    index += 1
    while index > 0 and len(letters) < 3:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters or "A"


def _label_index(label) -> int | None:
    """标号 → 序号（A=0…Z=25，AA=26…）；非法返回 None。"""
    if not isinstance(label, str):
        return None
    text = label.strip().upper()
    if not _LABEL_RE.match(text):
        return None
    n = 0
    for ch in text:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _label_key(entry: dict) -> str:
    """标号序列的分组键：同「条件指纹」的记录共用一条 A/B/C 序列。"""
    return str(entry.get("condition_key") or "")


def _next_label(used: set[str], start: int = 0) -> str:
    """取下一个未占用的标号（从 start 序号开始找）。"""
    i = max(int(start), 0)
    while True:
        candidate = _label_at(i)
        if candidate not in used:
            return candidate
        i += 1


def _used_labels(entries: list[dict]) -> set[str]:
    """当前条目里已占用的标号集合。"""
    return {str(e.get("label") or "").strip().upper()
            for e in entries if _label_index(e.get("label")) is not None}


def _next_label_after(entries: list[dict]) -> str:
    """新增记录的标号：**取最后（最大）一个已用标号的下一个字母**，而非回填空档。

    删除记录后不复用被删掉的字母（删 B 后新记录取 D，不占用空出来的 B），
    这样标号在时间轴上单调、不会被误读成"历史记录换了号"。
    """
    indexes = [_label_index(e.get("label")) for e in entries]
    tops = [i for i in indexes if i is not None]
    start = (max(tops) + 1) if tops else 0
    return _next_label(_used_labels(entries), start=start)


def _label_view(entry: dict, index: int, seq: dict[str, int]) -> str:
    """读取期标号（纯内存，不落盘）：存量标号优先，缺失时按同组出现顺序派生。

    旧数据（无 label）按「列表顺序 + 同分组内计数」派生，同一份数据每次
    读取结果一致；一旦发生写操作（新增/删除条目）即由 _backfill_labels
    把派生结果落盘固化，此后删除记录不会重排已有标号。
    """
    label = entry.get("label")
    if _label_index(label) is not None:
        return str(label).strip().upper()
    key = _label_key(entry)
    seq[key] = seq.get(key, 0) + 1
    return _label_at(seq[key] - 1)


def _backfill_labels(entries: list[dict]) -> list[str]:
    """为缺 label 的存量条目补标号（写路径调用，保证落盘固化）。

    标号只增不改：已带 label 的条目原样保留（删除记录后不重排），新记录
    取同组（同条件指纹）内未被占用的下一个字母。
    """
    used: dict[str, set[str]] = {}
    for entry in entries:
        if isinstance(entry, dict):
            label = entry.get("label")
            if _label_index(label) is not None:
                used.setdefault(_label_key(entry), set()).add(
                    str(label).strip().upper())
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if _label_index(entry.get("label")) is not None:
            continue
        key = _label_key(entry)
        bucket = used.setdefault(key, set())
        label = _next_label(bucket)
        bucket.add(label)
        entry["label"] = label
    return [str(e.get("label") or "") for e in entries if isinstance(e, dict)]


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


def _normalize_dft_entries(fav: dict) -> list[dict]:
    """dft_entries 规整（幂等，纯数据修复）：

    - 非 list → []；非 dict 项 → {}（占位保留，删坏条目交由用户/接口处理）；
    - 缺 condition_key 的存量条目 → 按条目字段派生补上（老数据读出新口径）；
    - 旧条目用 x_type 存目标分子描述（DFT 2.0 起该字段为枚举）→ 仅当
      x_description 缺失时拷贝一份，不改写原字段。
    """
    entries = fav.get("dft_entries")
    if not isinstance(entries, list):
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not str(entry.get("condition_key") or "").strip():
            key = _condition_key(_entry_conditions(entry))
            if key:
                entry["condition_key"] = key
        if not str(entry.get("x_description") or "").strip():
            legacy_x = str(entry.get("x_type") or "").strip()
            if legacy_x and legacy_x not in ("self_stack", "solvent",
                                             "other_dimer", "custom"):
                entry["x_description"] = legacy_x
    fav["dft_entries"] = entries
    return entries


def _normalize_entry_dicts(raw) -> list[dict] | None:
    """dft_entries 规整为 dict 列表；非 list（缺失/损坏）返回 None 表示"不是 list"。"""
    if not isinstance(raw, list):
        return None
    out: list[dict] = []
    for e in raw:
        if isinstance(e, dict):
            out.append(e)
        else:  # 损坏条目占位保留（用户可在界面删除）
            out.append({})
    return out


def _ensure_dft_entries_field(fav: dict) -> bool:
    """旧格式迁移（幂等）：保证 dft_entries 是 dict 列表（缺失/损坏 → []）。

    迁移**只补字段形态，不改写条目内容**（不塞 condition_key/label），也不把
    旧 dft_snapshot 搬进列表 —— 旧数据的搬运与派生标号只在响应视图里做
    （见 _dft_entries_view / _dft_response_view），保证老收藏"读多少次磁盘
    都不变"；一旦发生写操作（新增/删除记录、编辑元数据）即在新结构下落盘
    （见 _entries_for_write）。返回是否有变更（由调用方决定是否落盘）。
    """
    raw = fav.get("dft_entries")
    entries = _normalize_entry_dicts(raw)
    if entries is None:
        fav["dft_entries"] = []
        return True
    fav["dft_entries"] = entries
    return entries != raw


def _entries_for_write(fav: dict) -> list[dict]:
    """写路径取条目列表：必要时把旧 dft_snapshot 搬进 dft_entries（新结构落盘）。

    这是"旧数据首次被修改时"的一次性搬运：搬运后磁盘上 dft_entries 为唯一
    数据源、dft_snapshot 置 None（响应层仍会回填最新一条给旧前端）。
    """
    raw = fav.get("dft_entries")
    entries = _normalize_entry_dicts(raw)
    if entries is None:
        entries = []
    snap = fav.get("dft_snapshot")
    if not entries and isinstance(snap, dict) and snap:
        entries = [snap]
        fav["dft_snapshot"] = None
    fav["dft_entries"] = entries
    return entries


def _dft_entries_view(fav: dict) -> list[dict]:
    """读取期条目列表（不落盘、不改写磁盘条目字典）：

    - dft_entries 已是 list → 原样返回（新建/写入路径已带 condition_key/label）；
    - 旧收藏只有 dft_snapshot → 包装成 [snapshot] 供展示（磁盘不变，写路径
      才真正搬运，见 _entries_for_write）。
    """
    entries = _normalize_entry_dicts(fav.get("dft_entries"))
    if entries is not None and entries:
        return entries
    snap = fav.get("dft_snapshot")
    if isinstance(snap, dict) and snap:
        return [snap]
    return entries or []


def _derived_labels(entries: list[dict], index_offset: int = 0) -> list[str]:
    """读取期派生标号数组（纯内存，不落盘、不改动条目字典）。

    存量标号优先；缺标号的历史条目按「列表顺序 + 同条件指纹内计数」派生
    A/B/C…，同一份数据每次读取结果一致。写路径（新增/删除记录）会把标号
    固化落盘，此后删除记录不会重排已有标号。
    """
    seq: dict[str, int] = {}
    labels: list[str] = []
    for i, entry in enumerate(entries):
        labels.append(_label_view(entry, i + index_offset, seq))
    return labels


def _dft_response_view(fav: dict) -> dict:
    """GET 响应兼容旧前端：dft_snapshot 回填为最新一条 dft_entries。

    仅改内存视图不落盘——磁盘上 dft_snapshot 保持原值，旧前端读
    dft_snapshot 拿到的就是最近一次 DFT 计算，不会炸。
    另外补两个**派生**字段（同样只存在于响应里，不改写磁盘条目）：
    - dft_labels：与 dft_entries 等长的标号数组（A/B/C…，见 _derived_labels）；
    - archive_type：档案卡类型（cof_monomer_group | dft_pair）。

    注意：不往条目字典里塞 label/mode 等派生键——旧数据（无
    condition_key/label 的历史条目）在响应里保持原样，避免"读一次就变样"。
    """
    entries = _dft_entries_view(fav)
    if isinstance(fav.get("dft_entries"), list) or entries:
        fav["dft_entries"] = entries
    if entries:
        fav["dft_snapshot"] = entries[-1]
    fav["dft_labels"] = _derived_labels(entries)
    fav["archive_type"] = _archive_type_of(entries)
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
    与 dft_entries 字段形态（缺失/损坏 → []，不改写条目内容与旧快照）。

    仅在有变更时落盘；重复启动不会重复建夹或重复改写。
    """
    changed = False
    if "dft_snapshot" not in fav:
        fav["dft_snapshot"] = None
        changed = True
    if _ensure_dft_entries_field(fav):
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


def _dft_job_modules() -> list:
    """DFT 任务注册表模块（两种导入形态可能同时存在，需都查到）。

    api/routers/dft.py 与 src/ 内模块可能分别按 `src.dft.jobs` 与 `dft.jobs`
    两种包路径导入 —— 各自是**独立的模块实例**（独立的 _JOBS 注册表），
    因此这里把两个都返回，谁有任务用谁。
    """
    mods = []
    for name in ("src.dft.jobs", "dft.jobs"):
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "get_job", None) is not None:
            mods.append(mod)
    if mods:
        return mods
    for name in ("src.dft.jobs", "dft.jobs"):
        try:
            __import__(name)
            mod = sys.modules.get(name)
            if mod is not None and getattr(mod, "get_job", None) is not None:
                mods.append(mod)
        except Exception:  # 后端模块不可用（离线/裁剪）不阻塞收藏
            continue
    return mods


def _job_view_for_entry(entry: dict) -> dict | None:
    """按 job_id 取后端 DFT 任务视图（补齐条目缺失的条件字段）。

    任务不存在（服务重启清空/测试假 id）或 job_id 缺失时返回 None —— 只按
    条目自带字段计算条件指纹，绝不影响写入主流程。
    """
    job_id = str(entry.get("job_id") or "").strip()
    if not job_id:
        return None
    job = None
    try:
        for mod in _dft_job_modules():
            job = mod.get_job(job_id)
            if isinstance(job, dict):
                break
    except Exception as exc:  # 后端模块不可用（离线/裁剪）不阻塞收藏
        logger.warning("DFT 任务查询失败 %s: %s", job_id, exc)
        return None
    if not isinstance(job, dict):
        return None
    job = dict(job)
    return {
        "method": job.get("method"),
        "mode": job.get("mode", "dimer"),
        "backend": job.get("backend", "xtb"),
        "optimize": job.get("optimize"),
        "with_props": job.get("with_props"),
        "n_samples": job.get("n_samples"),
        "complex_xyz": job.get("complex_xyz"),
        "ald_smiles_input": job.get("ald_smiles_input"),
        "amine_smiles_input": job.get("amine_smiles_input"),
        "input": {
            "ald_smiles": job.get("ald_smiles_input"),
            "amine_smiles": job.get("amine_smiles_input"),
            "x_type": job.get("x_type"),
            "solvent_id": job.get("solvent_id"),
            "ald2_smiles": job.get("ald2_smiles"),
            "amine2_smiles": job.get("amine2_smiles"),
            "custom_smiles": job.get("custom_smiles"),
            "n_samples": job.get("n_samples"),
        },
    }


def add_dft_entry(fav_id: str, entry: dict) -> dict:
    """保存一条 DFT 计算条目（条件指纹去重 + 标号），返回完整收藏（响应视图）。

    规则（v220 档案卡）：
    - 条目条件字段（mode/method/backend/x_smiles/n_samples/with_props/
      complex_xyz_hash/…）稳定序列化后 sha1 → condition_key（条件指纹）；
    - 与已有条目**指纹相同** → 视为同一条：保留原标号与首次时间，用本次
      结果覆盖该条（重复收藏不再堆重复记录）；
    - **指纹不同** → 新增一条，标号取当前最大标号的下一个字母
      （A→B→C…，删记录后不回填空档，超过 26 条走 AA/AB…）；
    - 条目的条件字段缺失时按 job_id 从后端任务补齐（老客户端只传 job_id
      也能拿到正确指纹），仍缺则按条目自带字段计算；
    - 缺 created_at 时补当前时间；落盘的 dft_snapshot 保持 None，GET 响应由
      _dft_response_view 回填最新一条。收藏不存在抛 KeyError，条目非非空
      dict 抛 ValueError。
    """
    if not isinstance(entry, dict) or not entry:
        raise ValueError("DFT 条目必须是非空 JSON 对象")
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    entry = dict(entry)
    entries = _entries_for_write(fav)   # 旧 dft_snapshot 首次写入时搬进 dft_entries
    _backfill_labels(entries)

    job = _job_view_for_entry(entry)
    conditions = _entry_conditions(entry, job)
    condition_key = _condition_key(conditions) or str(
        entry.get("condition_key") or "").strip()
    if conditions:
        entry["conditions"] = conditions
    if condition_key:
        entry["condition_key"] = condition_key
    entry["mode"] = _entry_mode(entry)
    if not str(entry.get("created_at") or "").strip():
        entry["created_at"] = _now_iso()

    dup_index = None
    for i, old in enumerate(entries):
        old_key = str(old.get("condition_key") or "")
        if condition_key and old_key == condition_key:
            dup_index = i
            break
        if not condition_key and not old_key and _same_conditions(old, entry):
            dup_index = i
            break

    if dup_index is not None:
        old = entries[dup_index]
        merged = dict(old)
        merged.update(entry)  # 本次结果覆盖，含失败/错误字段
        # 标号稳定：更新不换号；存量缺标号（旧数据）时按当前位置补一个
        merged["label"] = (str(old.get("label")).strip().upper()
                           if _label_index(old.get("label")) is not None
                           else _derived_labels(entries)[dup_index])
        merged["condition_key"] = condition_key or old.get("condition_key")
        merged["created_at"] = old.get("created_at") or merged.get("created_at")
        entries[dup_index] = merged
    else:
        entry["label"] = _next_label_after(entries)
        entries.append(entry)

    fav["dft_entries"] = entries
    fav["dft_snapshot"] = None
    _write(fav)
    return _response_view(fav)


def delete_dft_entry(fav_id: str, entry_index: int) -> dict:
    """删除 dft_entries 里第 entry_index 条记录（失败/无效记录可删除）。

    删除后持久化；**已有标号不重排**（新增记录取当前最大标号的下一个字母，
    不复用被删掉字母），因此列表里标号可能出现空档，属预期。返回删除后的
    完整收藏（响应视图，附带 deleted_entry 便于前端提示）。收藏或索引不
    存在抛 KeyError。
    """
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    entries = _entries_for_write(fav)   # 旧 dft_snapshot 首次写入时搬进 dft_entries
    try:
        index = int(entry_index)
    except (TypeError, ValueError):
        raise KeyError(f"记录序号无效: {entry_index!r}")
    if index < 0 or index >= len(entries):
        raise KeyError(f"记录不存在：第 {entry_index} 条（共 {len(entries)} 条）")
    removed = entries.pop(index)
    fav["dft_entries"] = entries
    fav["dft_snapshot"] = None
    _write(fav)
    view = _response_view(fav)
    view["deleted_entry"] = removed if isinstance(removed, dict) else {
        "raw": removed}
    view["deleted_index"] = index
    return view


def update_monomer_meta(fav_id: str, monomer: str, name=None,
                        cas=None) -> dict:
    """修改单体信息里的名称 / 手动补充 CAS 号（仅这两项元数据可改）。

    monomer 取 "aldehyde" | "amine"；name / cas 为 None 表示不改该项，
    传空串表示清空。SMILES 与打分/DFT 等计算产物一律不动。收藏不存在或
    单体名非法抛 KeyError，name/cas 非字符串抛 ValueError。
    """
    if monomer not in ("aldehyde", "amine"):
        raise KeyError(f"未知单体：{monomer}（仅 aldehyde / amine）")
    for value, label in ((name, "名称"), (cas, "CAS 号")):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{label}必须是字符串")
    fav = get_favorite(fav_id)
    if fav is None:
        raise KeyError(f"收藏条目不存在: {fav_id}")
    m = fav.get(monomer)
    if not isinstance(m, dict):
        m = {"smiles": "", "cas": "", "name": ""}
    if name is not None:
        m["name"] = name.strip()
    if cas is not None:
        raw_cas = cas.strip()
        # 用户可粘贴带空格/全角连字符的 CAS：统一成半角，其余原样保留
        m["cas"] = raw_cas.replace("－", "-").replace("—", "-").replace("–", "-")
    fav[monomer] = m
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
    if "dft_entries" in fields:
        # 旧调用方显式写 dft_entries 时同样补条件指纹与派生标号（新口径一致）
        _normalize_dft_entries(fav)
        _backfill_labels(fav["dft_entries"])
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
