"""主动能力（V2.2）：实验日报聚合 + 连续失败主动提醒。

日报（build_daily_brief）：聚合指定日期（缺省今天）的当日数据——
新建/更新的实验记录、DFT 计算任务、新收藏、新录入文献（literature_intake
流水）；LLM 已配置时以 ming 人格生成一段 ≤150 字中文点评与"明日建议"
（失败/未配置时 commentary 为 None，只返回结构化数据，绝不阻塞）。

提醒（list_nudges）：同一收藏（favorite_id）下正式实验记录按时间倒序
连续命中"失败语义"（宽松集合，见 is_failure_record）≥2 次即命中；
同一天同一收藏 dismiss 后当天不再出现（dismiss 状态落
user_data_root/assistant/nudge_dismissals.json）。

纪律：全部只读聚合 + 一个 dismiss 状态文件；任何单源读取失败降级为空，
不抛出影响主流程。
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
    from src.assistant import llm_bridge
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore
    from assistant import llm_bridge  # type: ignore

# dismiss 状态（同日同收藏只提醒一次）
NUDGE_DISMISS_PATH = (
    runtime_config.user_data_root() / "assistant" / "nudge_dismissals.json"
)

# ---------------------------------------------------------------------------
# 失败语义集合（宽松判定）
# ---------------------------------------------------------------------------

# outcome 字段标准失败值（契约三值为 film / partial / failed，这里放宽松集合
# 防历史脏数据与外部导入的异形取值）
FAILURE_OUTCOMES = frozenset({
    "failed", "fail", "failure", "no_film", "none",
})
# 文本兜底关键词：outcome 缺失/异常时，从 notes/self_summary/mistakes 里找
FAILURE_KEYWORDS = (
    "失败", "未成功", "没成功", "未成膜", "没成膜", "不成膜", "无膜",
    "未反应", "无沉淀", "沉淀很少", "开裂", "糊掉", "碳化",
)

# 连续失败达到该次数即触发提醒
NUDGE_THRESHOLD = 2

_OUTCOME_ZH = {"film": "成膜", "partial": "部分成膜", "failed": "失败"}

_COMMENTARY_MAX_TOKENS = 500
_MISTAKE_SNIPPET = 120


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _local_date(iso_str: str) -> str:
    """把 ISO 时间串（可带时区）转本地日期 YYYY-MM-DD；解析失败返回空串。"""
    s = str(iso_str or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        return dt.date().isoformat()  # 朴素时间按本地口径（与 records.date 一致）
    return dt.astimezone().date().isoformat()


def _monomer_label(aldehyde: dict, amine: dict) -> str:
    """单体组名：优先名称，其次 CAS，最后 SMILES。"""
    ald = (aldehyde or {})
    amine = (amine or {})
    a = ald.get("name") or ald.get("cas") or ald.get("smiles") or "未知醛"
    m = amine.get("name") or amine.get("cas") or amine.get("smiles") or "未知胺"
    return f"{a} + {m}"


def _record_label(rec: dict) -> str:
    return _monomer_label(rec.get("aldehyde"), rec.get("amine"))


def _fav_label(fav: dict) -> str:
    return _monomer_label(fav.get("aldehyde"), fav.get("amine"))


def _cut(s: str, n: int = _MISTAKE_SNIPPET) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n] + "…"


def is_failure_record(rec: dict) -> bool:
    """宽松失败判定：outcome 命中失败集合，或 outcome 缺失时文本含失败关键词。

    outcome 为 film / partial 时直接判否（人填的 ground truth 优先）；
    outcome 为空或异常值时退到 notes/self_summary/mistakes 关键词兜底。
    """
    outcome = str(rec.get("outcome") or "").strip().lower()
    if outcome in FAILURE_OUTCOMES:
        return True
    if outcome in ("film", "partial"):
        return False
    text = " ".join(
        str(rec.get(k) or "")
        for k in ("notes", "self_summary", "mistakes")
    )
    return any(kw in text for kw in FAILURE_KEYWORDS)


# ---------------------------------------------------------------------------
# 数据源读取（全部防御式，单源失败降级为空）
# ---------------------------------------------------------------------------

def _rec_store():
    try:
        from src.records import store as rec_store
    except ImportError:  # pragma: no cover
        from records import store as rec_store  # type: ignore
    return rec_store


def _fav_store():
    try:
        from src.favorites import store as fav_store
    except ImportError:  # pragma: no cover
        from favorites import store as fav_store  # type: ignore
    return fav_store


def _dft_log_path() -> Path:
    try:
        from src.dft import log as dft_log
    except ImportError:  # pragma: no cover
        from dft import log as dft_log  # type: ignore
    return dft_log.LOG_PATH


def _intake_path() -> Path:
    try:
        from src.literature import resolver as lit_resolver
    except ImportError:  # pragma: no cover
        from literature import resolver as lit_resolver  # type: ignore
    return lit_resolver.INTAKE_PATH


def _read_jsonl(path: Path) -> list[dict]:
    entries: list[dict] = []
    try:
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    entries.append(obj)
    except Exception as exc:
        logger.warning("日报读取流水失败 %s: %s", path, exc)
    return entries


def _records_with_mtime() -> list[tuple[dict, str]]:
    """全部实验记录 + 文件 mtime 的本地日期（用于"当日更新"判定）。"""
    rec_store = _rec_store()
    out: list[tuple[dict, str]] = []
    try:
        base = rec_store.RECORDS_DIR
        if not base.exists():
            return []
        for p in sorted(base.glob("rec_*.json")):
            if p.name == "example.json":
                continue
            rec = rec_store._read_file(p)
            if not rec or not rec_store._ID_RE.match(str(rec.get("record_id", ""))):
                continue
            rec = rec_store._normalize_record(rec)
            try:
                mdate = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
                mdate_str = mdate.date().isoformat()
            except OSError:
                mdate_str = ""
            out.append((rec, mdate_str))
    except Exception as exc:
        logger.warning("日报读取实验记录失败: %s", exc)
    return out


def _record_brief_item(rec: dict, with_summary: bool = True) -> dict:
    outcome = str(rec.get("outcome") or "")
    item = {
        "record_id": rec.get("record_id"),
        "experiment_no": rec.get("experiment_no") or "",
        "monomers": _record_label(rec),
        "outcome": outcome,
        "outcome_zh": _OUTCOME_ZH.get(outcome, outcome or "未填"),
        "status": rec.get("status") or "final",
    }
    if with_summary:
        item["self_summary"] = _cut(rec.get("self_summary") or "", 200)
    return item


# ---------------------------------------------------------------------------
# 日报聚合
# ---------------------------------------------------------------------------

def build_daily_brief(date: str | None = None,
                      generate_commentary: bool = True) -> dict:
    """聚合指定日期（缺省今天）的科研日报；LLM 已配置时附 ming 点评。

    返回结构（端点契约，字段恒存在）：
    date / llm_enabled / records_created_count / records_created[] /
    records_updated_count / records_updated[] / dft_count /
    dft_best_e_bind_kcal / favorites_count / favorites[] /
    literature_count / literature[] / commentary。
    """
    target = (date or "").strip() or _today()

    # --- 实验记录：当日新建（date 字段）/ 当日更新（mtime，排除当日新建） ---
    created: list[dict] = []
    updated: list[dict] = []
    for rec, mdate in _records_with_mtime():
        rdate = str(rec.get("date") or "")
        if rdate == target:
            created.append(_record_brief_item(rec))
        elif mdate == target:
            updated.append(_record_brief_item(rec, with_summary=False))

    # --- DFT：当日完成任务数 + 最佳结合能（e_bind_kcal 最小 = 结合最强） ---
    dft_count = 0
    dft_best: float | None = None
    for entry in _read_jsonl(_dft_log_path()):
        if entry.get("type") != "dft":
            continue
        if _local_date(entry.get("timestamp") or "") != target:
            continue
        dft_count += 1
        e = entry.get("e_bind_kcal")
        if entry.get("status") == "done" and isinstance(e, (int, float)):
            dft_best = e if dft_best is None else min(dft_best, e)

    # --- 新收藏：created_at 当日（兜底用 id 日期段） ---
    favorites: list[dict] = []
    try:
        for fav in _fav_store().list_favorites():
            fid = str(fav.get("id") or "")
            fdate = _local_date(fav.get("created_at") or "")
            if not fdate:
                m = re.match(r"^fav_(\d{4})(\d{2})(\d{2})_", fid)
                fdate = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
            if fdate == target:
                favorites.append({"favorite_id": fid, "monomers": _fav_label(fav)})
    except Exception as exc:
        logger.warning("日报读取收藏失败: %s", exc)

    # --- 新录入文献：intake 流水 action=confirm_intake 且 at 当日 ---
    literature: list[dict] = []
    for entry in _read_jsonl(_intake_path()):
        if entry.get("action") != "confirm_intake":
            continue
        if _local_date(entry.get("at") or "") != target:
            continue
        final = entry.get("final") if isinstance(entry.get("final"), dict) else {}
        title = str(final.get("title") or entry.get("title") or "").strip()
        literature.append({
            "paper_id": entry.get("paper_id"),
            "title": title or "（未命名文献）",
        })

    data = {
        "date": target,
        "llm_enabled": llm_bridge.is_configured(),
        "records_created_count": len(created),
        "records_created": created,
        "records_updated_count": len(updated),
        "records_updated": updated,
        "dft_count": dft_count,
        "dft_best_e_bind_kcal": dft_best,
        "favorites_count": len(favorites),
        "favorites": favorites,
        "literature_count": len(literature),
        "literature": literature,
        "commentary": None,
    }

    if generate_commentary and data["llm_enabled"]:
        data["commentary"] = _generate_commentary(data)
    return data


def _commentary_prompt(data: dict) -> str:
    """把结构化日报压成供 LLM 点评的事实清单（不含任何编造素材）。"""
    lines = [f"日期：{data['date']}"]
    lines.append(
        f"新建实验记录 {data['records_created_count']} 条"
        + ("：" if data["records_created"] else "。")
    )
    for r in data["records_created"][:10]:
        bit = f"- {r['monomers']}（{r['outcome_zh']}"
        if r.get("self_summary"):
            bit += f"，自我总结：{r['self_summary']}"
        lines.append(bit + "）")
    if data["records_updated_count"]:
        lines.append(f"更新历史记录 {data['records_updated_count']} 条。")
    if data["dft_count"]:
        line = f"DFT 计算 {data['dft_count']} 个任务"
        if data["dft_best_e_bind_kcal"] is not None:
            line += (f"，最佳结合能 {data['dft_best_e_bind_kcal']} kcal/mol"
                     "（半经验方法，仅供相对比较）")
        lines.append(line + "。")
    if data["favorites_count"]:
        names = "、".join(f["monomers"] for f in data["favorites"][:5])
        lines.append(f"新收藏 {data['favorites_count']} 组：{names}。")
    if data["literature_count"]:
        lines.append(f"新录入文献 {data['literature_count']} 篇。")
    if data["records_created_count"] == 0 and data["dft_count"] == 0 \
            and data["favorites_count"] == 0 and data["literature_count"] == 0:
        lines.append("当日系统内无任何新数据。")
    return "\n".join(lines)


def _generate_commentary(data: dict) -> str | None:
    """ming 人格生成 ≤150 字中文点评 + 明日建议；失败返回 None。"""
    prompt = (
        "你是 ming，用户的 COF 成膜科研助手（理性优先，语气温和简练）。"
        "下面是用户今日的科研活动事实清单。请输出一段不超过 150 字的中文点评："
        "先一句话概括今天做了什么，再给一两条具体的「明日建议」"
        "（顺着事实给，比如失败的单体组该复盘、新收藏该安排实验；"
        "今天没有数据时就鼓励明天开工）。只输出点评正文，不要标题、不要列表符号、"
        "不要复述全部清单、不要编造清单之外的数字与结论。\n\n"
        + _commentary_prompt(data)
    )
    try:
        text = llm_bridge.chat_text(
            [{"role": "user", "content": prompt}],
            max_tokens=_COMMENTARY_MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning("日报点评生成失败: %s", exc)
        return None
    if not text:
        return None
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text or None


# ---------------------------------------------------------------------------
# 连续失败提醒（nudges）
# ---------------------------------------------------------------------------

def compute_failure_nudges() -> list[dict]:
    """全量连续失败提醒（未做 dismiss 过滤），按连续失败次数降序。

    判定：同一 favorite_id 的正式（非草稿）记录按时间正序排列后，从最新
    往回连续命中失败语义（is_failure_record）≥ NUDGE_THRESHOLD 次。
    """
    rec_store = _rec_store()
    fav_store = _fav_store()
    try:
        recs = rec_store.list_records()
    except Exception as exc:
        logger.warning("nudges 读取实验记录失败: %s", exc)
        return []

    by_fav: dict[str, list[dict]] = {}
    for rec in recs:
        fid = rec.get("favorite_id")
        if fid:
            by_fav.setdefault(str(fid), []).append(rec)

    nudges: list[dict] = []
    for fid, group in by_fav.items():
        # list_records 已按 (date, record_id) 升序；正式记录参与判定
        final_recs = [r for r in group if r.get("status") != "draft"]
        streak = 0
        latest_failure: dict | None = None
        for rec in reversed(final_recs):
            if is_failure_record(rec):
                streak += 1
                if latest_failure is None:
                    latest_failure = rec
            else:
                break
        if streak < NUDGE_THRESHOLD or latest_failure is None:
            continue

        monomers = ""
        try:
            fav = fav_store.get_favorite(fid)
            if fav:
                monomers = _fav_label(fav)
        except Exception:
            fav = None
        if not monomers:
            monomers = _record_label(latest_failure)

        mistakes = _cut(
            latest_failure.get("mistakes")
            or latest_failure.get("self_summary")
            or latest_failure.get("notes")
            or ""
        )
        nudges.append({
            "kind": "consecutive_failure",
            "favorite_id": fid,
            "monomers": monomers,
            "consecutive_failures": streak,
            "latest_mistakes": mistakes or "（该记录未填写失误与总结）",
            "suggestion": (
                f"「{monomers}」已连续 {streak} 次实验失败。"
                "建议暂停同条件重复，先复盘最近记录的条件与失误字段，"
                "必要时换溶剂体系或调整温度/时间，可让 ming 帮你做失败归因分析。"
            ),
        })
    nudges.sort(key=lambda n: (-n["consecutive_failures"], n["favorite_id"]))
    return nudges


def compute_new_mistake_nudges() -> list[dict]:
    """新失误记录提醒（v1.6.0 P2）：今天（文件 mtime）更新了 mistakes 字段的
    正式记录 → 提醒复盘/深度研究；同日同收藏经 dismiss 只提醒一次。"""
    try:
        recs_with_mtime = _records_with_mtime()
        fav_store = _fav_store()
    except Exception as exc:
        logger.warning("失误提醒读取数据失败: %s", exc)
        return []
    out: list[dict] = []
    for rec, mdate in recs_with_mtime:
        if mdate != _today():
            continue
        if str(rec.get("status") or "") == "draft":
            continue
        mistakes = str(rec.get("mistakes") or "").strip()
        if not mistakes:
            continue
        fid = str(rec.get("favorite_id") or "")
        if not fid:
            continue
        monomers = _record_label(rec)
        try:
            fav = fav_store.get_favorite(fid)
            if fav:
                monomers = _fav_label(fav)
        except Exception:
            pass
        out.append({
            "kind": "new_mistake",
            "favorite_id": fid,
            "record_id": rec.get("record_id"),
            "monomers": monomers,
            "latest_mistakes": _cut(mistakes),
            "suggestion": (
                f"「{monomers}」最近一次实验填写了失误记录。建议复盘这组的"
                "历史讨论，或发起一次深度研究做失败归因。"
            ),
        })
    out.sort(key=lambda n: n["favorite_id"])
    return out


# ---------------------------------------------------------------------------
# dismiss 状态（同日同收藏只提醒一次）
# ---------------------------------------------------------------------------

def _load_dismissals() -> list[dict]:
    try:
        if NUDGE_DISMISS_PATH.is_file():
            data = json.loads(NUDGE_DISMISS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("dismissals"), list):
                return [d for d in data["dismissals"] if isinstance(d, dict)]
    except Exception as exc:
        logger.warning("nudge dismiss 状态读取失败: %s", exc)
    return []


def _save_dismissals(items: list[dict]) -> None:
    NUDGE_DISMISS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"dismissals": items}, ensure_ascii=False, indent=1)
    NUDGE_DISMISS_PATH.write_text(payload + "\n", encoding="utf-8")


def _dismissed_ids(date: str) -> set[str]:
    return {
        str(d.get("favorite_id"))
        for d in _load_dismissals()
        if str(d.get("date") or "") == date and d.get("favorite_id")
    }


def dismiss_nudge(favorite_id: str, date: str | None = None) -> None:
    """登记某收藏当日 dismiss（幂等：同日同收藏只存一条）。"""
    fid = str(favorite_id or "").strip()
    if not fid:
        return
    target = (date or "").strip() or _today()
    items = _load_dismissals()
    if not any(str(d.get("favorite_id")) == fid
               and str(d.get("date") or "") == target for d in items):
        items.append({"favorite_id": fid, "date": target})
    # 截断保存，防文件膨胀（dismiss 语义只关心"当天"，旧条目无实际作用）
    _save_dismissals(items[-500:])


def list_nudges(date: str | None = None) -> list[dict]:
    """当日应展示的提醒列表：连续失败 + 新失误记录（v1.6.0 P2），
    已 dismiss 的收藏当日过滤。"""
    target = (date or "").strip() or _today()
    dismissed = _dismissed_ids(target)
    merged = compute_failure_nudges() + compute_new_mistake_nudges()
    # 同收藏可能两类提醒并存：dismiss 按 favorite_id 记，两类同隐
    return [n for n in merged if n["favorite_id"] not in dismissed]
