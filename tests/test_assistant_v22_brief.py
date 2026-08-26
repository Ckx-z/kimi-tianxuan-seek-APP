"""科研助手 V2.2 主动能力测试：日报聚合 / 连续失败判定 / dismiss 去重。

覆盖：
- build_daily_brief：新建/更新记录、DFT 任务与最佳结合能、新收藏、新文献；
  LLM 未配置时 commentary=None；配置时走 chat_text 桩生成点评；
- is_failure_record：失败语义集合（outcome 集合 + 文本关键词兜底）；
- compute_failure_nudges / list_nudges：连续 ≥2 次失败命中、成膜打断、
  草稿不计入、dismiss 当日去重（跨天重新出现）；
- API 端点契约：GET /daily-brief（含非法 date 400）、GET /nudges、
  POST /nudges/dismiss；
- get_daily_brief 工具文本。

所有存储目录/日志路径 monkeypatch 到 tmp_path，不碰真实数据；
favorites.store 存在 src.favorites.store 与 favorites.store 两个模块实例，
两处都 patch（同 test_assistant_v2_tools 口径）。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import favorites.store as fav_store_bare  # noqa: E402
import src.favorites.store as fav_store_pkg  # noqa: E402
import src.llm.client as llm_client  # noqa: E402
from src.assistant import brief  # noqa: E402
from src.assistant import llm_bridge  # noqa: E402
from src.assistant import registry  # noqa: E402
from src.dft import log as dft_log  # noqa: E402
from src.literature import resolver as lit_resolver  # noqa: E402
from src.records import store as rec_store  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"   # 均苯三甲醛类醛单体
PA = "Nc1ccc(N)cc1"                   # 对苯二胺
DAY = "2026-08-24"                    # 固定目标日期（避免"今天"漂移）


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """全部存储隔离到 tmp；LLM 默认未配置。"""
    fav_dir = tmp_path / "favorites"
    monkeypatch.setattr(fav_store_bare, "FAVORITES_DIR", fav_dir)
    monkeypatch.setattr(fav_store_pkg, "FAVORITES_DIR", fav_dir)
    monkeypatch.setattr(rec_store, "RECORDS_DIR", tmp_path / "records")
    monkeypatch.setattr(dft_log, "LOG_PATH", tmp_path / "dft_log.jsonl")
    monkeypatch.setattr(lit_resolver, "INTAKE_PATH",
                        tmp_path / "literature_intake.jsonl")
    monkeypatch.setattr(brief, "NUDGE_DISMISS_PATH",
                        tmp_path / "assistant" / "nudge_dismissals.json")
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    monkeypatch.setattr(llm_client, "MINIMAX_SECRETS",
                        tmp_path / "secrets.local.json")
    monkeypatch.setattr(llm_client, "CACHE_DIR", tmp_path / "llm_cache")
    for var in ("COF_LLM_BASE_URL", "COF_LLM_API_KEY", "COF_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


def _mk_favorite(ald: str = TP, amine: str = PA) -> dict:
    fav_store_pkg._ensure_default_folder()
    return fav_store_pkg.add_favorite(ald, amine, prediction={"score": 0.6})


def _mk_record(fav_id: str | None, outcome: str, date: str = DAY,
               monkeypatch=None, **kw) -> dict:
    """按指定日期创建记录（patch _today 控制 date 字段）。"""
    if monkeypatch is not None:
        monkeypatch.setattr(rec_store, "_today", lambda: date)
    return rec_store.create_record(
        favorite_id=fav_id,
        aldehyde_smiles=kw.pop("aldehyde_smiles", TP),
        amine_smiles=kw.pop("amine_smiles", PA),
        outcome=outcome, experiment_no=kw.pop("experiment_no", "T1"), **kw)


def _set_mtime(path: Path, date: str) -> None:
    ts = datetime.fromisoformat(f"{date}T12:00:00").timestamp()
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# 失败语义集合
# ---------------------------------------------------------------------------

def test_is_failure_outcome_set():
    assert brief.is_failure_record({"outcome": "failed"}) is True
    assert brief.is_failure_record({"outcome": "Failed"}) is True   # 大小写宽松
    assert brief.is_failure_record({"outcome": "fail"}) is True
    assert brief.is_failure_record({"outcome": "film"}) is False
    assert brief.is_failure_record({"outcome": "partial"}) is False


def test_is_failure_text_fallback():
    # outcome 缺失/异常时，文本关键词兜底
    assert brief.is_failure_record(
        {"outcome": "", "mistakes": "这次又没成膜"}) is True
    assert brief.is_failure_record(
        {"outcome": "", "notes": "实验失败，全是粉末"}) is True
    assert brief.is_failure_record(
        {"outcome": "", "self_summary": "未成功重复文献条件"}) is True
    assert brief.is_failure_record(
        {"outcome": "", "notes": "顺利成膜，膜层均匀"}) is False
    # outcome 为 film 时即使文本提到"失败"也判否（人填结果优先）
    assert brief.is_failure_record(
        {"outcome": "film", "notes": "上次失败这次成了"}) is False


# ---------------------------------------------------------------------------
# 日报聚合
# ---------------------------------------------------------------------------

def test_daily_brief_aggregation(isolated, monkeypatch):
    fav = _mk_favorite()
    # 收藏 created_at 是"今天"，改成目标日期以命中"当日新收藏"
    fav_path = fav_store_pkg._path_of(fav["id"])
    fav_obj = json.loads(fav_path.read_text(encoding="utf-8"))
    fav_obj["created_at"] = f"{DAY}T09:00:00+08:00"
    fav_path.write_text(json.dumps(fav_obj, ensure_ascii=False), encoding="utf-8")

    r1 = _mk_record(fav["id"], "film", DAY, monkeypatch,
                    self_summary="80℃ 三天成膜完整")
    r2 = _mk_record(fav["id"], "failed", DAY, monkeypatch,
                    experiment_no="T2", mistakes="沉淀是粉末")
    # 前一天建的记录、目标日被更新 → 计入"更新"而非"新建"
    old = _mk_record(fav["id"], "film", "2026-08-20", monkeypatch,
                     experiment_no="T0")
    monkeypatch.setattr(rec_store, "_today", lambda: DAY)
    rec_store.update_record(old["record_id"], {"notes": "补记现象"})
    # 把文件 mtime 固定到目标日（"当日更新"按 mtime 判定）
    _set_mtime(rec_store.RECORDS_DIR / f"{old['record_id']}.json", DAY)

    # DFT：目标日 2 条（含 1 条失败任务），非目标日 1 条
    dft_log.log_dft({"smiles_a": TP, "smiles_b": PA, "method": "gfn2",
                     "status": "done", "e_bind_kcal": -7.5,
                     "timestamp": f"{DAY}T02:00:00+00:00"})
    dft_log.log_dft({"smiles_a": TP, "smiles_b": PA, "method": "gfn2",
                     "status": "done", "e_bind_kcal": -9.2,
                     "timestamp": f"{DAY}T10:00:00"})
    dft_log.log_dft({"smiles_a": TP, "smiles_b": PA, "method": "gfn2",
                     "status": "failed",
                     "timestamp": f"{DAY}T11:00:00"})
    dft_log.log_dft({"smiles_a": TP, "smiles_b": PA, "method": "gfn2",
                     "status": "done", "e_bind_kcal": -99.0,
                     "timestamp": "2026-08-23T10:00:00"})

    # 文献：目标日 1 条 confirm_intake + 1 条无关 action
    lit_resolver.append_intake({"action": "confirm_intake", "paper_id": "42",
                                "final": {"title": "COF 成膜新方法"},
                                "at": f"{DAY}T15:00:00+08:00"})
    lit_resolver.append_intake({"action": "doi_backfill", "paper_id": "10",
                                "at": f"{DAY}T15:01:00+08:00"})

    data = brief.build_daily_brief(DAY, generate_commentary=False)

    assert data["date"] == DAY
    assert data["llm_enabled"] is False
    assert data["commentary"] is None

    assert data["records_created_count"] == 2
    ids = {r["record_id"] for r in data["records_created"]}
    assert ids == {r1["record_id"], r2["record_id"]}
    by_id = {r["record_id"]: r for r in data["records_created"]}
    assert by_id[r1["record_id"]]["outcome_zh"] == "成膜"
    assert "成膜完整" in by_id[r1["record_id"]]["self_summary"]
    assert by_id[r2["record_id"]]["outcome_zh"] == "失败"
    # 单体组名（名称或 SMILES 拼装）
    assert "+" in by_id[r1["record_id"]]["monomers"]

    assert data["records_updated_count"] == 1
    assert data["records_updated"][0]["record_id"] == old["record_id"]

    assert data["dft_count"] == 3            # 含失败任务
    assert data["dft_best_e_bind_kcal"] == -9.2   # 只在 done 中取最小

    assert data["favorites_count"] == 1
    assert data["favorites"][0]["favorite_id"] == fav["id"]

    assert data["literature_count"] == 1     # doi_backfill 不算新录入
    assert data["literature"][0]["title"] == "COF 成膜新方法"


def test_daily_brief_empty_day(isolated):
    data = brief.build_daily_brief(DAY, generate_commentary=False)
    assert data["records_created_count"] == 0
    assert data["dft_count"] == 0
    assert data["dft_best_e_bind_kcal"] is None
    assert data["favorites_count"] == 0
    assert data["literature_count"] == 0
    assert data["commentary"] is None


def test_daily_brief_commentary_with_llm(isolated, monkeypatch):
    fav = _mk_favorite()
    _mk_record(fav["id"], "film", DAY, monkeypatch)
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    captured = {}

    def fake_chat(messages, max_tokens=4000, temperature=0.3, **kw):
        captured["prompt"] = messages[0]["content"]
        return "今天完成一组成膜实验，明日建议复盘边缘厚度。"

    monkeypatch.setattr(llm_bridge, "chat_text", fake_chat)
    data = brief.build_daily_brief(DAY)
    assert data["llm_enabled"] is True
    assert data["commentary"] == "今天完成一组成膜实验，明日建议复盘边缘厚度。"
    assert "明日建议" in captured["prompt"]
    assert DAY in captured["prompt"]


def test_daily_brief_commentary_llm_failure_degrades(isolated, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    monkeypatch.setattr(llm_bridge, "chat_text", lambda *a, **k: None)
    data = brief.build_daily_brief(DAY)
    assert data["commentary"] is None        # 失败降级，不影响结构化数据


# ---------------------------------------------------------------------------
# 连续失败判定
# ---------------------------------------------------------------------------

def test_nudges_consecutive_failures(isolated, monkeypatch):
    fav = _mk_favorite()
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T1",
               mistakes="溶剂可能不对")
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T2",
               mistakes="还是粉末")
    nudges = brief.compute_failure_nudges()
    assert len(nudges) == 1
    n = nudges[0]
    assert n["favorite_id"] == fav["id"]
    assert n["consecutive_failures"] == 2
    assert "还是粉末" in n["latest_mistakes"]     # 取最近一次失败
    assert "+" in n["monomers"]
    assert n["suggestion"]


def test_nudges_streak_broken_by_film(isolated, monkeypatch):
    fav = _mk_favorite()
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T1")
    _mk_record(fav["id"], "film", DAY, monkeypatch, experiment_no="T2")
    assert brief.compute_failure_nudges() == []

    # 成膜后再失败 1 次：streak=1，未达阈值
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T3")
    assert brief.compute_failure_nudges() == []

    # 再失败 1 次：streak=2，命中
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T4")
    nudges = brief.compute_failure_nudges()
    assert len(nudges) == 1
    assert nudges[0]["consecutive_failures"] == 2


def test_nudges_skip_draft_and_free_records(isolated, monkeypatch):
    fav = _mk_favorite()
    # 草稿不计入（即使 outcome=failed）
    rec_store.create_record(favorite_id=fav["id"], outcome="failed",
                            status="draft", notes="草稿")
    assert brief.compute_failure_nudges() == []
    # 游离记录（无 favorite_id）不参与按收藏的提醒
    _mk_record(None, "failed", DAY, monkeypatch)
    _mk_record(None, "failed", DAY, monkeypatch, experiment_no="F2")
    assert brief.compute_failure_nudges() == []


# ---------------------------------------------------------------------------
# dismiss 去重（同日同收藏只提醒一次）
# ---------------------------------------------------------------------------

def test_nudges_dismiss_same_day(isolated, monkeypatch):
    fav = _mk_favorite()
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T1")
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T2")

    monkeypatch.setattr(brief, "_today", lambda: DAY)
    assert len(brief.list_nudges()) == 1

    brief.dismiss_nudge(fav["id"])
    assert brief.list_nudges() == []          # 当日不再提醒

    brief.dismiss_nudge(fav["id"])            # 幂等：重复 dismiss 不炸
    items = brief._load_dismissals()
    assert len([d for d in items if d["favorite_id"] == fav["id"]
                and d["date"] == DAY]) == 1

    # 换一天（未 dismiss）重新出现
    monkeypatch.setattr(brief, "_today", lambda: "2026-08-25")
    assert len(brief.list_nudges()) == 1


# ---------------------------------------------------------------------------
# API 端点契约
# ---------------------------------------------------------------------------

def test_api_daily_brief_and_bad_date(isolated, client, monkeypatch):
    fav = _mk_favorite()
    _mk_record(fav["id"], "film", DAY, monkeypatch)

    r = client.get(f"/api/assistant/daily-brief?date={DAY}")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == DAY
    assert body["records_created_count"] == 1
    assert body["commentary"] is None         # LLM 未配置
    assert set(body) >= {"date", "llm_enabled", "records_created",
                         "records_updated", "dft_count",
                         "dft_best_e_bind_kcal", "favorites",
                         "literature", "commentary"}

    # 缺省 date = 今天（不报错即可）
    assert client.get("/api/assistant/daily-brief").status_code == 200
    # 非法日期 400
    bad = client.get("/api/assistant/daily-brief?date=2026/08/24")
    assert bad.status_code == 400


def test_api_nudges_and_dismiss(isolated, client, monkeypatch):
    fav = _mk_favorite()
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T1")
    _mk_record(fav["id"], "failed", DAY, monkeypatch, experiment_no="T2")

    r = client.get("/api/assistant/nudges")
    assert r.status_code == 200
    nudges = r.json()["nudges"]
    assert len(nudges) == 1
    assert nudges[0]["favorite_id"] == fav["id"]
    assert nudges[0]["consecutive_failures"] == 2

    r2 = client.post("/api/assistant/nudges/dismiss",
                     json={"favorite_id": fav["id"]})
    assert r2.status_code == 200
    assert r2.json()["dismissed"] is True
    assert r2.json()["nudges"] == []
    assert client.get("/api/assistant/nudges").json()["nudges"] == []

    # 空 favorite_id 400
    assert client.post("/api/assistant/nudges/dismiss",
                       json={"favorite_id": "  "}).status_code == 400


# ---------------------------------------------------------------------------
# get_daily_brief 工具
# ---------------------------------------------------------------------------

def test_tool_get_daily_brief(isolated, monkeypatch):
    fav = _mk_favorite()
    _mk_record(fav["id"], "failed", DAY, monkeypatch, mistakes="温度偏高")
    r = registry.execute("get_daily_brief", {"date": DAY})
    assert r["is_error"] is False
    assert DAY in r["text"]
    assert "新建实验记录 1 条" in r["text"]
    assert "失败" in r["text"]
    assert r["details"]["records_created_count"] == 1

    empty = registry.execute("get_daily_brief", {"date": "2020-01-01"})
    assert empty["is_error"] is False
    assert "没有新" in empty["text"]
