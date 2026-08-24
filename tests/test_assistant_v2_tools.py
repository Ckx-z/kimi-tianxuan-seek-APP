"""科研助手 V2.0 新工具单测：直调工具函数（不经过 LLM）。

覆盖 6 个新工具：list_favorites / list_prediction_history / manage_favorite
/ generate_plan_card / draft_experiment_record / query_dft。
所有写操作目录 monkeypatch 到 tmp_path（favorites / records / plans /
dft_cache / dft_log / prediction_log 全部隔离），不碰真实数据。
注意 favorites.store 存在 src.favorites.store 与 favorites.store 两个模块
实例（src/ 与项目根都在 sys.path），两处都要 patch。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import favorites.store as fav_store_bare  # noqa: E402
import src.favorites.store as fav_store_pkg  # noqa: E402
from src.assistant.tools import dft as dft_tool  # noqa: E402
from src.assistant.tools import favorites as fav_tool  # noqa: E402
from src.assistant.tools import history as history_tool  # noqa: E402
from src.assistant.tools import plan as plan_tool  # noqa: E402
from src.assistant.tools import records as records_tool  # noqa: E402
from src.dft import cache as dft_cache  # noqa: E402
from src.dft import jobs as dft_jobs  # noqa: E402
from src.dft import log as dft_log  # noqa: E402
from src.records import store as rec_store  # noqa: E402
from src.recommend import generated_plans  # noqa: E402
from src.utils import predict_log  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"   # 均苯三甲醛类醛单体
PA = "Nc1ccc(N)cc1"                   # 对苯二胺


@pytest.fixture()
def fav_dir(tmp_path, monkeypatch):
    """收藏目录隔离（两个模块实例都 patch；收藏夹文件随父目录隔离）。"""
    d = tmp_path / "favorites"
    monkeypatch.setattr(fav_store_bare, "FAVORITES_DIR", d)
    monkeypatch.setattr(fav_store_pkg, "FAVORITES_DIR", d)
    return d


@pytest.fixture()
def rec_dir(tmp_path, monkeypatch):
    d = tmp_path / "records"
    monkeypatch.setattr(rec_store, "RECORDS_DIR", d)
    return d


@pytest.fixture()
def plans_dir(tmp_path, monkeypatch):
    d = tmp_path / "generated_plans"
    monkeypatch.setattr(generated_plans, "PLANS_DIR", d)
    return d


# ---------------------------------------------------------------------------
# list_favorites（读）
# ---------------------------------------------------------------------------

def test_list_favorites_empty(fav_dir):
    r = fav_tool.list_favorites_tool()
    assert r["is_error"] is False
    assert "暂无收藏" in r["text"] or "尚无收藏夹" in r["text"]
    assert r["details"]["count"] == 0


def test_list_favorites_with_data_and_folder_filter(fav_dir, monkeypatch):
    monkeypatch.setattr(fav_tool, "_current_snapshot",
                        lambda a, b: {"score": 0.65, "ood": "none"})
    fav_store_pkg._ensure_default_folder()  # 先建兜底夹（缺省归 folders[0]）
    folder = fav_store_pkg.create_folder("重点体系")
    fav = fav_store_pkg.add_favorite(TP, PA, folder_id=folder["id"],
                                     prediction={"score": 0.65})
    fav_store_pkg.add_favorite("O=Cc1ccccc1", "Nc1ccccc1")  # 进兜底夹

    r = fav_tool.list_favorites_tool()
    assert r["is_error"] is False
    assert r["details"]["count"] == 2
    assert "重点体系" in r["text"]
    assert fav["id"] in r["text"]
    assert "0.650" in r["text"]

    r2 = fav_tool.list_favorites_tool(folder_id=folder["id"])
    assert r2["details"]["count"] == 1
    assert r2["details"]["favorite_ids"] == [fav["id"]]

    r3 = fav_tool.list_favorites_tool(folder_id="folder_nope")
    assert r3["is_error"] is True and "不存在" in r3["text"]


# ---------------------------------------------------------------------------
# list_prediction_history（读）
# ---------------------------------------------------------------------------

def test_list_prediction_history_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(predict_log, "LOG_PATH",
                        tmp_path / "prediction_log.jsonl")
    r = history_tool.list_prediction_history()
    assert r["is_error"] is False and "未查到" in r["text"]


def test_list_prediction_history_order_and_limit(tmp_path, monkeypatch):
    log = tmp_path / "prediction_log.jsonl"
    monkeypatch.setattr(predict_log, "LOG_PATH", log)
    rows = [
        {"type": "prediction", "ald_smiles": TP, "amine_smiles": PA,
         "score": 0.5, "ood_level": "none", "timestamp": f"2026-08-2{i}T10:00:00"}
        for i in range(3)
    ]
    rows.append({"type": "other", "note": "非打分记录应被过滤"})
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                           for r in rows), encoding="utf-8")
    r = history_tool.list_prediction_history(limit=2)
    assert r["is_error"] is False
    assert r["details"]["count"] == 3 and r["details"]["shown"] == 2
    # 新→旧：最新的（i=2）在最前
    assert r["text"].index("2026-08-22") < r["text"].index("2026-08-21")


# ---------------------------------------------------------------------------
# manage_favorite（写，幂等）
# ---------------------------------------------------------------------------

def test_manage_favorite_add_move_delete(fav_dir, monkeypatch):
    monkeypatch.setattr(fav_tool, "_current_snapshot",
                        lambda a, b: {"score": 0.72, "ood": "none",
                                      "tree_score": 0.72})
    # add：收藏到按名称指定的夹（不存在自动新建），附打分快照
    r = fav_tool.manage_favorite({
        "action": "add", "ald_smiles": TP, "amine_smiles": PA,
        "folder_name": "助手收藏", "notes": "助手推荐"})
    assert r["is_error"] is False
    fid = r["details"]["favorite_id"]
    fav = fav_store_pkg.get_favorite(fid)
    assert fav is not None
    assert fav["latest_prediction"]["score"] == 0.72
    folder = fav_store_pkg.get_folder(fav["folder_id"])
    assert folder["name"] == "助手收藏"

    # 幂等：同组合重复 add → 不新建条目
    r2 = fav_tool.manage_favorite({
        "action": "add", "ald_smiles": TP, "amine_smiles": PA})
    assert r2["is_error"] is False
    assert r2["details"]["favorite_id"] == fid
    assert r2["details"]["deduplicated"] is True
    assert len(fav_store_pkg.list_favorites()) == 1

    # move：移到新夹
    f2 = fav_store_pkg.create_folder("待验证")
    r3 = fav_tool.manage_favorite({
        "action": "move", "favorite_id": fid, "folder_id": f2["id"]})
    assert r3["is_error"] is False
    assert fav_store_pkg.get_favorite(fid)["folder_id"] == f2["id"]
    # 重复 move → 幂等说明
    r4 = fav_tool.manage_favorite({
        "action": "move", "favorite_id": fid, "folder_id": f2["id"]})
    assert r4["is_error"] is False and "无需移动" in r4["text"]

    # delete：删除 + 重复删除幂等
    r5 = fav_tool.manage_favorite({"action": "delete", "favorite_id": fid})
    assert r5["is_error"] is False
    assert fav_store_pkg.get_favorite(fid) is None
    r6 = fav_tool.manage_favorite({"action": "delete", "favorite_id": fid})
    assert r6["is_error"] is False and "无需重复" in r6["text"]


def test_manage_favorite_param_errors(fav_dir):
    r = fav_tool.manage_favorite({"action": "add", "ald_smiles": TP})
    assert r["is_error"] is True and "参数缺失" in r["text"]
    r = fav_tool.manage_favorite({"action": "explode"})
    assert r["is_error"] is True and "未知 action" in r["text"]
    r = fav_tool.manage_favorite({"action": "move",
                                  "favorite_id": "fav_20990101_001",
                                  "folder_name": "x"})
    assert r["is_error"] is True and "不存在" in r["text"]


def test_manage_favorite_impact_text():
    assert "删除" in fav_tool.manage_favorite_impact(
        {"action": "delete", "favorite_id": "fav_x"})
    assert "收藏" in fav_tool.manage_favorite_impact(
        {"action": "add", "folder_name": "收藏夹1"})


# ---------------------------------------------------------------------------
# generate_plan_card（写，幂等）
# ---------------------------------------------------------------------------

def test_generate_plan_card_creates_and_dedupes(fav_dir, plans_dir):
    r = plan_tool.generate_plan_card_tool(TP, PA)
    assert r["is_error"] is False
    plan_id = r["details"]["plan_id"]
    assert plan_id.startswith("plan_")
    assert (plans_dir / f"{plan_id}.json").is_file()
    assert "方案卡" in r["text"]

    # 幂等：同单体对 + 同模板 → 返回已有 plan_id，不产生新文件
    r2 = plan_tool.generate_plan_card_tool(TP, PA)
    assert r2["is_error"] is False
    assert r2["details"]["plan_id"] == plan_id
    assert r2["details"]["deduplicated"] is True
    assert len(list(plans_dir.glob("plan_*.json"))) == 1


def test_generate_plan_card_param_and_template_errors(fav_dir, plans_dir):
    r = plan_tool.generate_plan_card_tool("", PA)
    assert r["is_error"] is True and "参数缺失" in r["text"]
    r = plan_tool.generate_plan_card_tool(TP, PA, template_id="tpl_nope")
    assert r["is_error"] is True and "模板不存在" in r["text"]


# ---------------------------------------------------------------------------
# draft_experiment_record（写，草稿态，幂等）
# ---------------------------------------------------------------------------

def test_draft_record_with_favorite(fav_dir, rec_dir, monkeypatch):
    monkeypatch.setattr(fav_tool, "_current_snapshot", lambda a, b: None)
    fav = fav_store_pkg.add_favorite(TP, PA)
    args = {"favorite_id": fav["id"], "outcome": "partial",
            "notes": "60°C 预聚 2h 后浑浊", "operator": "ckx"}
    r = records_tool.draft_experiment_record(args)
    assert r["is_error"] is False
    rid = r["details"]["record_id"]
    rec = rec_store.get_record(rid)
    assert rec["status"] == "draft"
    assert rec["favorite_id"] == fav["id"]
    # 回挂到收藏
    assert rid in (fav_store_pkg.get_favorite(fav["id"])
                   .get("experiment_record_ids") or [])

    # 幂等：同收藏 + 同 notes → 不重复创建
    r2 = records_tool.draft_experiment_record(args)
    assert r2["is_error"] is False
    assert r2["details"]["record_id"] == rid
    assert len(rec_store.list_records()) == 1


def test_draft_record_free_and_param_errors(fav_dir, rec_dir):
    r = records_tool.draft_experiment_record({
        "aldehyde_smiles": TP, "amine_smiles": PA, "notes": "游离草稿"})
    assert r["is_error"] is False
    rec = rec_store.get_record(r["details"]["record_id"])
    assert rec["status"] == "draft" and rec["favorite_id"] is None

    r = records_tool.draft_experiment_record({"notes": "什么都没给"})
    assert r["is_error"] is True and "参数缺失" in r["text"]
    r = records_tool.draft_experiment_record({
        "aldehyde_smiles": TP, "amine_smiles": PA, "outcome": "exploded"})
    assert r["is_error"] is True and "outcome" in r["text"]


# ---------------------------------------------------------------------------
# query_dft（读缓存/历史 + 写提交轮询）
# ---------------------------------------------------------------------------

@pytest.fixture()
def dft_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(dft_cache, "CACHE_DIR", tmp_path / "dft_cache")
    monkeypatch.setattr(dft_log, "LOG_PATH", tmp_path / "dft_log.jsonl")
    return tmp_path


def _canon_pair():
    from src.dft import engine
    return engine.canonicalize_smiles(TP), engine.canonicalize_smiles(PA)


def test_query_dft_cache_hit(dft_dirs):
    from src.dft import engine
    canon_a, canon_b = _canon_pair()
    key = dft_cache.cache_key(canon_a, canon_b, "gfn2")
    dft_cache.save_cache(key, {
        "method": "gfn2", "smiles_a": canon_a, "smiles_b": canon_b,
        "e_bind_kcal": -12.34, "e_bind_kj": -51.6, "gap_ev": 2.1,
        "dipole_debye": 1.5, "elapsed_sec": 42.0,
    })
    r = dft_tool.query_dft(TP, PA, "gfn2")
    assert r["is_error"] is False
    assert r["details"]["cached"] is True
    assert "-12.34" in r["text"] and "半经验" in r["text"]
    # 缓存命中时 confirm_impact 为 None（纯读不确认）
    assert dft_tool.confirm_impact(
        {"smiles_a": TP, "smiles_b": PA, "method": "gfn2"}) is None


def test_query_dft_history_hit(dft_dirs):
    canon_a, canon_b = _canon_pair()
    dft_log.log_dft({"smiles_a": canon_a, "smiles_b": canon_b,
                     "method": "gfnff", "status": "done",
                     "e_bind_kcal": -8.0, "e_bind_kj": -33.5})
    r = dft_tool.query_dft(TP, PA, "gfnff")
    assert r["is_error"] is False
    assert "来自计算历史" in r["text"] and "-8.00" in r["text"]


def test_query_dft_submit_and_complete(dft_dirs, monkeypatch):
    """未命中：提交任务 → 轮询到 done → 返回结果。"""
    canon_a, canon_b = _canon_pair()
    monkeypatch.setattr(dft_tool.engine, "xtb_binary", lambda: Path("/fake/xtb"))
    state = {"polled": 0}

    def fake_create(a, b, method):
        return {"job_id": "job_test1", "status": "pending",
                "progress_hint": "排队", "method": method, "result": None,
                "error": None, "cached": False, "created_at": "t"}

    def fake_get(job_id):
        state["polled"] += 1
        if state["polled"] < 2:
            return {"job_id": job_id, "status": "running",
                    "progress_hint": "计算中", "result": None, "error": None}
        return {"job_id": job_id, "status": "done", "progress_hint": "完成",
                "error": None,
                "result": {"method": "gfn2", "smiles_a": canon_a,
                           "smiles_b": canon_b, "e_bind_kcal": -10.0,
                           "e_bind_kj": -41.8, "gap_ev": 1.9,
                           "dipole_debye": 0.8}}

    monkeypatch.setattr(dft_jobs, "create_job", fake_create)
    monkeypatch.setattr(dft_jobs, "get_job", fake_get)
    monkeypatch.setattr(dft_tool, "_POLL_INTERVAL_SEC", 0.01)

    # confirm_impact：未命中 → 需要确认
    impact = dft_tool.confirm_impact(
        {"smiles_a": TP, "smiles_b": PA, "method": "gfn2"})
    assert impact and "提交" in impact

    r = dft_tool.query_dft(TP, PA, "gfn2")
    assert r["is_error"] is False
    assert r["details"]["job_id"] == "job_test1"
    assert "-10.00" in r["text"] and "半经验" in r["text"]


def test_query_dft_timeout_returns_job_id(dft_dirs, monkeypatch):
    monkeypatch.setattr(dft_tool.engine, "xtb_binary", lambda: Path("/fake/xtb"))
    monkeypatch.setattr(dft_jobs, "create_job", lambda a, b, m: {
        "job_id": "job_slow", "status": "pending", "progress_hint": "排队",
        "method": m, "result": None, "error": None, "cached": False,
        "created_at": "t"})
    monkeypatch.setattr(dft_jobs, "get_job", lambda jid: {
        "job_id": jid, "status": "running", "progress_hint": "计算中",
        "result": None, "error": None})
    monkeypatch.setattr(dft_tool, "_POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(dft_tool, "_POLL_TIMEOUT", {"gfn2": 0.05})
    r = dft_tool.query_dft(TP, PA, "gfn2")
    assert r["is_error"] is False  # 超时不是错误：任务已提交
    assert "job_slow" in r["text"] and "已提交" in r["text"]
    assert r["details"]["status"] == "running"


def test_query_dft_param_and_engine_errors(dft_dirs, monkeypatch):
    r = dft_tool.query_dft("", PA, "gfn2")
    assert r["is_error"] is True and "参数缺失" in r["text"]
    r = dft_tool.query_dft(TP, PA, "b3lyp")
    assert r["is_error"] is True and "未知方法" in r["text"]
    r = dft_tool.query_dft("not_a_smiles", PA, "gfn2")
    assert r["is_error"] is True and "无法解析" in r["text"]
    # 引擎缺失：无缓存时 is_error，且 confirm_impact 为 None（无可确认的写操作）
    monkeypatch.setattr(dft_tool.engine, "xtb_binary", lambda: None)
    r = dft_tool.query_dft(TP, PA, "gfn2")
    assert r["is_error"] is True and "xtb" in r["text"]
    assert dft_tool.confirm_impact(
        {"smiles_a": TP, "smiles_b": PA, "method": "gfn2"}) is None


# ---------------------------------------------------------------------------
# registry：写工具确认声明
# ---------------------------------------------------------------------------

def test_registry_confirm_marks():
    from src.assistant import registry
    # 读工具无需确认
    for name in ("predict_film", "query_graphrag", "read_experiment_records",
                 "list_favorites", "list_prediction_history"):
        assert registry.confirm_impact(name, {}) is None
    # 写工具固定确认
    assert registry.confirm_impact("manage_favorite", {"action": "add"})
    assert registry.confirm_impact("generate_plan_card",
                                   {"ald_smiles": TP, "amine_smiles": PA})
    assert registry.confirm_impact("draft_experiment_record", {})
    # query_dft 参数非法时不确认（直接报错路径）
    assert registry.confirm_impact("query_dft", {"smiles_a": ""}) is None
    # 新工具 schema 全部进入 OpenAI tools 列表
    names = {s["function"]["name"] for s in registry.list_tool_schemas()}
    assert {"list_favorites", "list_prediction_history", "manage_favorite",
            "generate_plan_card", "draft_experiment_record", "query_dft"} <= names
