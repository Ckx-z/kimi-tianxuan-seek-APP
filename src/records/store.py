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
import uuid
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
# 实验过程时间线附件目录（开发模式 data/experiment_records/attachments/，
# frozen 模式 %APPDATA%/COF-Film-Recommend/data/experiment_records/attachments/）
ATTACHMENTS_DIR = (
    runtime_config.user_data_root() / "experiment_records" / "attachments"
)

SCHEMA_VERSION = "1.0"
RECORD_TYPE = "experiment_record"
VALID_OUTCOMES = ("film", "partial", "failed")  # 成膜 / 部分 / 失败
VALID_STATUSES = ("draft", "final")             # 草稿 / 正式记录

# 附件约束：单文件 ≤20MB，常见图片 / pdf / 文档类型（按扩展名判定）
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
ALLOWED_ATTACHMENT_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".xls", ".xlsx",
}
IMAGE_ATTACHMENT_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

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


def _sanitize_timeline(timeline: list,
                       existing: dict[str, list] | None = None) -> list[dict]:
    """清洗时间点记录条目（时间线）。

    每条保留 entry_id（缺省生成 tl_<uuid8>）、time_label（第几天/第几小时
    或日期时间，纯文本）、description。attachments 不由客户端提供：existing
    传入 {entry_id: attachments} 时按 entry_id 回接服务端已登记附件元数据，
    其余条目附件为空列表（附件只经 add_attachment 登记，防伪造）。
    """
    existing = existing or {}
    out: list[dict] = []
    for item in timeline if isinstance(timeline, list) else []:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("entry_id") or "").strip()
        if not entry_id:
            entry_id = f"tl_{uuid.uuid4().hex[:8]}"
        out.append({
            "entry_id": entry_id,
            "time_label": str(item.get("time_label") or "").strip(),
            "description": str(item.get("description") or "").strip(),
            "attachments": list(existing.get(entry_id) or []),
        })
    return out


def _normalize_record(rec: dict) -> dict:
    """旧数据兼容：缺新字段（status/process_notes/timeline）时补默认值。

    旧记录一律视为 final（草稿为本功能新增语义，历史记录全部是正式记录）。
    """
    rec.setdefault("status", "final")
    rec.setdefault("process_notes", "")
    tl = rec.get("timeline")
    rec["timeline"] = tl if isinstance(tl, list) else []
    return rec


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
    status: str = "final",
    process_notes: str = "",
    timeline: list | None = None,
) -> dict:
    """创建实验记录并按 RAG 契约落盘，返回完整记录 dict。

    - status="final"（默认）：走完整校验 —— experiment_no 必填（空串
      ValueError）、outcome 必须 film|partial|failed、游离记录醛/胺
      SMILES 必填；落盘为独立字段并并入 notes 前缀；
    - status="draft"（草稿暂存）：宽松校验 —— experiment_no / outcome /
      游离 SMILES 均可留空，notes 不加编号前缀，之后经 update_record
      继续编辑或转正式；
    - 关联收藏（favorite_id 非 None）：favorite_id 必须指向已存在的收藏
      条目，否则 KeyError（单体对象与 prediction_snapshot 从收藏冗余），
      成功后把 record_id 回挂到收藏条目的 experiment_record_ids；
    - 游离记录（favorite_id=None）：final 时 aldehyde_smiles / amine_smiles
      必须非空，否则 ValueError；favorite_id 置 null、prediction_snapshot
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

    status = (status or "final").strip()
    if status not in VALID_STATUSES:
        raise ValueError(f"status 必须是 {VALID_STATUSES} 之一，收到: {status!r}")
    is_draft = status == "draft"

    experiment_no = (experiment_no or "").strip()
    if not is_draft and not experiment_no:
        raise ValueError("experiment_no 为必填字段（如 A5、G2-3），不能为空")

    outcome = (outcome or "").strip()
    if is_draft:
        if outcome and outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome 必须是 {VALID_OUTCOMES} 之一或留空，收到: {outcome!r}"
            )
    elif outcome not in VALID_OUTCOMES:
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
        if not is_draft and (not aldehyde_smiles or not amine_smiles):
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
    if not is_draft:
        notes = (
            f"实验编号：{experiment_no}" if not notes
            else f"实验编号：{experiment_no}；{notes}"
        )

    rec = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "record_id": _next_id(),
        "experiment_no": experiment_no,
        "status": status,
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
        "process_notes": (process_notes or "").strip(),
        "timeline": _sanitize_timeline(timeline or []),
        "attachments": [],
        "operator": operator or "",
        "date": _today(),
        "minimax_plan_no": None,
    }

    # 实验编号重复警告：同 favorite 下已存在相同 experiment_no 的记录时，
    # 仅在返回 dict 上加 duplicate_experiment_no=True（不落盘、不拦截保存，
    # 用户可能有意重复实验）
    duplicate = False
    if experiment_no and favorite_id is not None:
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
                recs.append(_normalize_record(rec))
    recs.sort(key=lambda r: (str(r.get("date", "")), str(r.get("record_id", ""))))
    return recs


def get_record(rec_id: str) -> dict | None:
    """按 record_id 取记录；不存在/损坏返回 None。"""
    if not rec_id or not isinstance(rec_id, str) or not _ID_RE.match(rec_id):
        return None
    path = RECORDS_DIR / f"{rec_id}.json"
    rec = _read_file(path) if path.exists() else None
    return _normalize_record(rec) if rec is not None else None


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
    # 一并清理该记录的附件目录（失败仅告警，不阻塞删除结果）
    att_dir = ATTACHMENTS_DIR / rec_id
    if att_dir.is_dir():
        import shutil
        try:
            shutil.rmtree(att_dir)
        except OSError as exc:
            logger.warning("删除记录 %s 附件目录失败: %s", rec_id, exc)
    return True


# ---------------------------------------------------------------------------
# 草稿编辑 / 转正式（update）
# ---------------------------------------------------------------------------

_UPDATABLE_FIELDS = (
    "experiment_no", "outcome", "strength", "notes", "operator",
    "process_notes",
)


def update_record(rec_id: str, fields: dict | None = None, **kwargs) -> dict:
    """更新记录字段（草稿继续编辑 / 转正式；正式记录也可更新流程与时间线）。

    fields（或关键字）可含：
    - status："draft" 继续暂存（宽松校验）；"final" 转正式（走与创建一致
      的完整校验：experiment_no 必填、outcome 三选、游离记录 SMILES 非空，
      并把编号并入 notes 前缀）；
    - experiment_no / outcome / strength / notes / operator / process_notes；
    - conditions：与现有 conditions 合并（九键外的额外键原样保留）；
    - timeline：时间点记录条目（附件元数据按 entry_id 回接服务端登记值）。

    记录不存在 KeyError；校验失败 ValueError；返回更新后的完整记录。
    """
    rec = get_record(rec_id)
    if rec is None:
        raise KeyError(f"记录 {rec_id} 不存在")

    payload = dict(fields or {})
    payload.update(kwargs)

    target_status = str(payload.pop("status", rec.get("status", "final"))).strip()
    if target_status not in VALID_STATUSES:
        raise ValueError(
            f"status 必须是 {VALID_STATUSES} 之一，收到: {target_status!r}"
        )
    is_draft = target_status == "draft"

    for key in _UPDATABLE_FIELDS:
        if key in payload:
            val = payload.pop(key)
            rec[key] = (val or "").strip() if isinstance(val, str) else (val or "")

    if "conditions" in payload:
        cond = dict(rec.get("conditions") or {})
        extra = dict(payload.pop("conditions") or {})
        for old, new in _LEGACY_CONDITION_KEYS.items():
            if old in extra and not extra.get(new):
                extra[new] = extra[old]
        cond.update(extra)
        rec["conditions"] = cond

    if "timeline" in payload:
        existing = {
            str(e.get("entry_id")): list(e.get("attachments") or [])
            for e in rec.get("timeline") or []
            if isinstance(e, dict)
        }
        rec["timeline"] = _sanitize_timeline(payload.pop("timeline"), existing)

    # 草稿→正式 时补 notes 编号前缀（草稿保存时不加）
    rec["experiment_no"] = str(rec.get("experiment_no") or "").strip()
    if not is_draft:
        if not rec["experiment_no"]:
            raise ValueError("experiment_no 为必填字段（如 A5、G2-3），不能为空")
        outcome = str(rec.get("outcome") or "").strip()
        if outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome 必须是 {VALID_OUTCOMES} 之一，收到: {outcome!r}"
            )
        if rec.get("favorite_id") is None:
            ald = str((rec.get("aldehyde") or {}).get("smiles") or "").strip()
            amine = str((rec.get("amine") or {}).get("smiles") or "").strip()
            if not ald or not amine:
                raise ValueError(
                    "游离记录（favorite_id=None）必须提供非空的醛/胺 SMILES"
                )
        prefix = f"实验编号：{rec['experiment_no']}"
        notes = str(rec.get("notes") or "").strip()
        if not notes.startswith("实验编号："):
            rec["notes"] = prefix if not notes else f"{prefix}；{notes}"
    else:
        outcome = str(rec.get("outcome") or "").strip()
        if outcome and outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome 必须是 {VALID_OUTCOMES} 之一或留空，收到: {outcome!r}"
            )
        rec["outcome"] = outcome

    rec["status"] = target_status

    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    (RECORDS_DIR / f"{rec_id}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return rec


# ---------------------------------------------------------------------------
# 实验过程时间线附件（上传 / 下载 / 删除）
# ---------------------------------------------------------------------------


def _attachment_meta(rec: dict, attachment_id: str) -> tuple[dict, dict] | None:
    """在记录时间线中定位附件，返回 (entry, meta)；找不到返回 None。"""
    for entry in rec.get("timeline") or []:
        if not isinstance(entry, dict):
            continue
        for meta in entry.get("attachments") or []:
            if isinstance(meta, dict) and meta.get("attachment_id") == attachment_id:
                return entry, meta
    return None


def add_attachment(rec_id: str, entry_id: str,
                   filename: str, data: bytes) -> dict:
    """给某时间点条目登记附件：写盘 + 元数据并入记录 json，返回附件元数据。

    限制：单文件 ≤20MB（ValueError）；扩展名须属常见图片/pdf/文档类型
    （ValueError）；entry_id 必须已存在于记录时间线（KeyError）。
    """
    rec = get_record(rec_id)
    if rec is None:
        raise KeyError(f"记录 {rec_id} 不存在")
    entry = next(
        (e for e in rec.get("timeline") or []
         if isinstance(e, dict) and e.get("entry_id") == entry_id),
        None,
    )
    if entry is None:
        raise KeyError(f"时间点条目 {entry_id} 不存在于记录 {rec_id}")

    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"附件超过大小限制（{MAX_ATTACHMENT_BYTES // 1024 // 1024}MB）"
        )
    safe_name = Path(filename or "file").name
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXTS:
        raise ValueError(f"不支持的附件类型: {ext or '（无扩展名）'}")

    attachment_id = f"att_{uuid.uuid4().hex[:10]}"
    dest_dir = ATTACHMENTS_DIR / rec_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{attachment_id}{ext}"
    dest.write_bytes(data)

    meta = {
        "attachment_id": attachment_id,
        "filename": safe_name,
        "ext": ext,
        "is_image": ext in IMAGE_ATTACHMENT_EXTS,
        "size": len(data),
        "uploaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    entry.setdefault("attachments", []).append(meta)
    (RECORDS_DIR / f"{rec_id}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return meta


def get_attachment_path(rec_id: str,
                        attachment_id: str) -> tuple[Path, dict] | None:
    """按附件 id 返回 (文件路径, 元数据)；记录/附件不存在或文件丢失返回 None。"""
    rec = get_record(rec_id)
    if rec is None:
        return None
    found = _attachment_meta(rec, attachment_id)
    if found is None:
        return None
    _, meta = found
    path = ATTACHMENTS_DIR / rec_id / f"{attachment_id}{meta.get('ext', '')}"
    if not path.is_file():
        return None
    return path, meta


def remove_attachment(rec_id: str, attachment_id: str) -> bool:
    """删除附件：移除文件并从记录时间线元数据中摘除；返回是否实际删除。"""
    rec = get_record(rec_id)
    if rec is None:
        return False
    found = _attachment_meta(rec, attachment_id)
    if found is None:
        return False
    entry, meta = found
    entry["attachments"] = [
        m for m in entry.get("attachments") or []
        if m.get("attachment_id") != attachment_id
    ]
    path = ATTACHMENTS_DIR / rec_id / f"{attachment_id}{meta.get('ext', '')}"
    try:
        if path.is_file():
            path.unlink()
    except OSError as exc:
        logger.warning("删除附件文件 %s 失败: %s", path.name, exc)
    (RECORDS_DIR / f"{rec_id}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return True
