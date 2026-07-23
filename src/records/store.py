"""实验记录存储（P2 后端，页④支撑）。

落盘到 data/rag_export/records/rec_<YYYYMMDD>_<NNN>.json —— 即
App ↔ RAG 数据契约的单一数据源（schema 见 data/rag_export/README.md
Schema 2 experiment_record），不再另建 data/experiment_records/。

- record_id 独立编号体系（防 CV 泄漏教训）；
- 关联收藏的记录：favorite_id 必须指向已存在的收藏条目（单体对象与
  预测快照从收藏里取），创建后把 rec_id 回挂到 favorite 的
  experiment_record_ids；
- 游离记录（P3 扩展）：favorite_id=None 时必须显式给醛/胺 SMILES，
  单体对象按契约填充（cas/name 尝试内置库反查）、favorite_id 置 null、
  prediction_snapshot 置 null、不回挂任何收藏。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from favorites import store as favorites_store

logger = logging.getLogger(__name__)

try:
    from src import runtime_config
except ImportError:
    import runtime_config  # type: ignore

PROJECT_ROOT = runtime_config.resource_root()
RECORDS_DIR = runtime_config.user_data_root() / "rag_export" / "records"

SCHEMA_VERSION = "1.0"
RECORD_TYPE = "experiment_record"
VALID_OUTCOMES = ("film", "partial", "failed")  # 成膜 / 部分 / 失败

_ID_RE = re.compile(r"^rec_(\d{8})_(\d{3})$")

# 契约 conditions 标准字段（未知留空字符串；额外字段原样保留）
# P4a：solvent 拆为 solvent_1 / solvent_2，新增 eluent（洗脱剂）；
# temperature_c / time_days / vessel 沿用 1.0 契约原名不变。
_CONDITION_KEYS = (
    "solvent_1",
    "solvent_2",
    "eluent",
    "modulator",
    "catalyst",
    "temperature_c",
    "time_days",
    "vessel",
    "addition_order",
)

# 旧键 → 新键兼容映射（仅当新键未提供时生效；旧键本身按"额外字段"原样保留）
_LEGACY_CONDITION_KEYS = {"solvent": "solvent_1"}

# 契约示例文件不作为真实记录列出
_EXAMPLE_FILE = "example.json"


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _next_id() -> str:
    """生成 rec_<YYYYMMDD>_<NNN>，按当日已有文件取最大序号 +1。"""
    today = datetime.now().strftime("%Y%m%d")
    max_n = 0
    if RECORDS_DIR.exists():
        for p in RECORDS_DIR.glob("rec_*.json"):
            m = _ID_RE.match(p.stem)
            if m and m.group(1) == today:
                max_n = max(max_n, int(m.group(2)))
    return f"rec_{today}_{max_n + 1:03d}"


def _read_file(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as exc:
        logger.warning("实验记录读取失败 %s: %s", path.name, exc)
        return None


def create_record(
    favorite_id: str | None = None,
    aldehyde_smiles: str = "",
    amine_smiles: str = "",
    conditions: dict | None = None,
    outcome: str = "",
    strength: str = "",
    notes: str = "",
    operator: str = "",
    experiment_no: str = "",
) -> dict:
    """创建实验记录并按 RAG 契约落盘，返回完整记录 dict。

    - experiment_no 必填（关联/游离记录都要），空串 ValueError；落盘为
      独立字段并并入 notes 前缀（P4a 用户决策：独立必填字段）；
    - outcome 必须是 film|partial|failed，否则 ValueError；
    - 关联收藏（favorite_id 非 None）：favorite_id 必须指向已存在的收藏
      条目，否则 KeyError（单体对象与 prediction_snapshot 从收藏冗余），
      成功后把 record_id 回挂到收藏条目的 experiment_record_ids；
    - 游离记录（favorite_id=None）：aldehyde_smiles / amine_smiles 必须
      非空，否则 ValueError；favorite_id 置 null、prediction_snapshot
      置 null、不回挂任何收藏。

    向后兼容：旧签名 create_record(favorite_id, conditions, outcome,
    strength, notes, operator) 的位置调用（第二个位置参数是 dict）仍然
    可用，参数自动按旧语义重排；conditions 里的旧键 "solvent" 自动映射
    到新标准键 "solvent_1"（新键已提供时不动）。
    """
    # 旧位置调用检测：create_record(fid, conditions_dict, outcome, ...)
    if isinstance(aldehyde_smiles, dict):
        legacy_cond = aldehyde_smiles
        legacy_outcome = amine_smiles if isinstance(amine_smiles, str) else ""
        legacy_strength = conditions if isinstance(conditions, str) else ""
        legacy_notes = outcome if isinstance(outcome, str) else ""
        legacy_operator = strength if isinstance(strength, str) else ""
        aldehyde_smiles, amine_smiles = "", ""
        conditions = legacy_cond
        outcome = legacy_outcome
        # 旧位置槽位为空时保留显式传入的关键字值（混合调用友好）
        strength = legacy_strength or strength
        notes = legacy_notes or notes
        operator = legacy_operator or operator

    experiment_no = (experiment_no or "").strip()
    if not experiment_no:
        raise ValueError("experiment_no 为必填字段（如 A5、G2-3），不能为空")

    outcome = (outcome or "").strip()
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"outcome 必须是 {VALID_OUTCOMES} 之一，收到: {outcome!r}"
        )

    if favorite_id is not None:
        fav = favorites_store.get_favorite(favorite_id)
        if fav is None:
            raise KeyError(f"收藏条目不存在，无法创建实验记录: {favorite_id}")
        aldehyde = fav.get("aldehyde", {"smiles": "", "cas": "", "name": ""})
        amine = fav.get("amine", {"smiles": "", "cas": "", "name": ""})
        snap = fav.get("latest_prediction")
        prediction_snapshot = (
            {
                "score": snap.get("score"),
                "std": snap.get("std"),
                "ood": snap.get("ood", ""),
            }
            if isinstance(snap, dict)
            else None
        )
    else:
        aldehyde_smiles = (aldehyde_smiles or "").strip()
        amine_smiles = (amine_smiles or "").strip()
        if not aldehyde_smiles or not amine_smiles:
            raise ValueError(
                "游离记录（favorite_id=None）必须提供非空的醛/胺 SMILES"
            )
        aldehyde = favorites_store._monomer_obj(aldehyde_smiles)
        amine = favorites_store._monomer_obj(amine_smiles)
        prediction_snapshot = None

    cond = {k: "" for k in _CONDITION_KEYS}
    extra = dict(conditions or {})
    for old, new in _LEGACY_CONDITION_KEYS.items():
        if old in extra and not extra.get(new):
            extra[new] = extra[old]
    cond.update(extra)

    notes = (notes or "").strip()
    notes = (
        f"实验编号：{experiment_no}" if not notes
        else f"实验编号：{experiment_no}；{notes}"
    )

    rec = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "record_id": _next_id(),
        "experiment_no": experiment_no,
        "favorite_id": favorite_id,
        "aldehyde": aldehyde,
        "amine": amine,
        "prediction_id": None,
        "prediction_snapshot": prediction_snapshot,
        "conditions": cond,
        "outcome": outcome,
        "failure_class": None,
        "strength": strength or "",
        "notes": notes,
        "attachments": [],
        "operator": operator or "",
        "date": _today(),
        "minimax_plan_no": None,
    }

    # 实验编号重复警告：同 favorite 下已存在相同 experiment_no 的记录时，
    # 仅在返回 dict 上加 duplicate_experiment_no=True（不落盘、不拦截保存，
    # 用户可能有意重复实验）
    duplicate = False
    if favorite_id is not None:
        duplicate = any(
            r.get("experiment_no") == experiment_no
            for r in list_records(favorite_id=favorite_id)
        )

    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    path = RECORDS_DIR / f"{rec['record_id']}.json"
    path.write_text(
        json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    # 回挂到收藏条目（游离记录不回挂）
    if favorite_id is not None:
        ids = list(fav.get("experiment_record_ids") or [])
        ids.append(rec["record_id"])
        favorites_store.update_favorite(favorite_id, experiment_record_ids=ids)

    if duplicate:
        rec["duplicate_experiment_no"] = True
    return rec


def list_records(favorite_id: str | None = None) -> list[dict]:
    """全部实验记录（可按 favorite_id 过滤），按日期+id 升序（时间线）。

    契约示例文件 example.json 不作为真实记录列出；损坏文件跳过。
    """
    if not RECORDS_DIR.exists():
        return []
    recs = []
    for p in sorted(RECORDS_DIR.glob("rec_*.json")):
        if p.name == _EXAMPLE_FILE:
            continue
        rec = _read_file(p)
        if rec and _ID_RE.match(str(rec.get("record_id", ""))):
            if favorite_id is None or rec.get("favorite_id") == favorite_id:
                recs.append(rec)
    recs.sort(key=lambda r: (str(r.get("date", "")), str(r.get("record_id", ""))))
    return recs


def get_record(rec_id: str) -> dict | None:
    """按 record_id 取记录；不存在/损坏返回 None。"""
    if not rec_id or not isinstance(rec_id, str) or not _ID_RE.match(rec_id):
        return None
    path = RECORDS_DIR / f"{rec_id}.json"
    return _read_file(path) if path.exists() else None


def delete_record(rec_id: str) -> bool:
    """删除记录：移除落盘文件，并从关联收藏的 experiment_record_ids 解挂。

    返回是否实际删除（记录不存在/损坏返回 False）。
    """
    rec = get_record(rec_id)
    if rec is None:
        return False
    fid = rec.get("favorite_id")
    if fid:
        fav = favorites_store.get_favorite(fid)
        if fav:
            ids = [r for r in (fav.get("experiment_record_ids") or [])
                   if r != rec_id]
            try:
                favorites_store.update_favorite(fid, experiment_record_ids=ids)
            except Exception as exc:  # 解挂失败不阻塞文件删除，仅告警
                logger.warning("删除记录 %s 时解挂收藏 %s 失败: %s", rec_id, fid, exc)
    try:
        (RECORDS_DIR / f"{rec_id}.json").unlink()
    except OSError as exc:
        logger.warning("删除记录文件 %s 失败: %s", rec_id, exc)
        return False
    return True
