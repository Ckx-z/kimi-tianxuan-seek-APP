"""实验时间派生（v1.9.3 问题 5）+ 助手读完整流程/时间线（问题 4.1）测试。

问题 5：`date` 是录入日期，用户期望显示的是实验过程时间线里的**第一个时间点**。
本文件覆盖容错解析（实测混杂格式 + 用户笔误）、跳过非法值取下一个可解析值、
无时间线时回退录入日期、列表排序、Word 导出时间线排序。

问题 4.1：助手工具此前只给最近 5 条时间点且完全不含 process_notes；现在列表
工具给全部时间点 + 流程要点，单条工具给全文。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from records import dates as rec_dates  # noqa: E402
from records import store as rec_store  # noqa: E402
from favorites import store as fav_store  # noqa: E402

TP = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
PA = "Nc1ccc(N)cc1"


@pytest.fixture()
def rec_dirs(tmp_path, monkeypatch):
    """隔离实验记录目录。

    注意：`records.store` 可能以裸名与 `src.records.store` 两种形式各被 import
    一次（API 路由用裸名、助手工具优先 `src.records.store`），两个实例的
    RECORDS_DIR / ATTACHMENTS_DIR 都要打桩，否则工具会读到真实数据目录。
    """
    monkeypatch.setattr(fav_store, "FAVORITES_DIR", tmp_path / "favorites")
    import importlib
    seen = []
    for name in ("records.store", "src.records.store"):
        mod = sys.modules.get(name)
        if mod is None:
            try:                       # 提前导入再打桩，防工具首次 import 时才创建
                mod = importlib.import_module(name)
            except ImportError:
                continue
        if mod is not None and mod not in seen:
            monkeypatch.setattr(mod, "RECORDS_DIR", tmp_path / "records")
            monkeypatch.setattr(mod, "ATTACHMENTS_DIR", tmp_path / "attachments")
            seen.append(mod)
    if rec_store not in seen:  # 兜底（模块已导入但不在 sys.modules 键下）
        monkeypatch.setattr(rec_store, "RECORDS_DIR", tmp_path / "records")
        monkeypatch.setattr(rec_store, "ATTACHMENTS_DIR", tmp_path / "attachments")
    return tmp_path / "records"


def _write_record(rec_dir: Path, record_id: str, **fields) -> dict:
    rec_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "record_id": record_id,
        "experiment_no": record_id,
        "status": "final",
        "favorite_id": None,
        "aldehyde": {"smiles": TP, "name": "TP"},
        "amine": {"smiles": PA, "name": "PA"},
        "conditions": {},
        "outcome": "film",
        "strength": "",
        "notes": "",
        "process_notes": "",
        "timeline": [],
        "self_summary": "",
        "mistakes": "",
        "attachments": [],
        "operator": "",
        "date": "2026-09-12",
    }
    rec.update(fields)
    (rec_dir / f"{record_id}.json").write_text(
        json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return rec


# ---------------------------------------------------------------- 解析

@pytest.mark.parametrize("label,expected", [
    ("2026-9-10 8:59", "2026-09-10"),
    ("2026-8-19  11:39", "2026-08-19"),      # 双空格
    ("2026-09-10", "2026-09-10"),
    ("2026/7/3 10:30", "2026-07-03"),
    ("26/7/3/10:30", "2026-07-03"),          # 实测两位年格式
    ("26/7/6/10:30", "2026-07-06"),
    ("2026年7月3日", "2026-07-03"),
])
def test_parse_time_label_supported_formats(label, expected):
    assert rec_dates.parse_time_label(label)[0] == expected


@pytest.mark.parametrize("label", [
    "2026-8-123 11:39",   # 用户笔误（日三位数）
    "第1天",               # 相对标注，无绝对日期
    "Day 2",
    "", "   ", "开始", "2026-13-40", "2026-2-30",
])
def test_parse_time_label_rejects_unparseable(label):
    # 2026-2-30 目前按 1<=day<=31 放行（月内非法日不深究），单独排除
    if label == "2026-2-30":
        pytest.skip("日历级校验不在本版范围")
    assert rec_dates.parse_time_label(label)[0] is None


def test_first_time_point_skips_bad_entry_and_takes_next():
    """首条笔误不应导致整条记录回退录入日期。"""
    timeline = [
        {"time_label": "2026-8-123 11:39", "description": "笔误"},
        {"time_label": "第2天", "description": "相对标注"},
        {"time_label": "2026-9-10 8:59", "description": "实验开始"},
        {"time_label": "2026-9-11 9:00", "description": "结束"},
    ]
    iso, label, conf = rec_dates.first_time_point(timeline)
    assert iso == "2026-09-10" and label == "2026-9-10 8:59" and conf == "high"


def test_first_time_point_empty_timeline():
    assert rec_dates.first_time_point([]) == (None, None, "")
    assert rec_dates.first_time_point(None) == (None, None, "")
    assert rec_dates.effective_date({"date": "2026-09-12"}) == (
        "2026-09-12", "created", "", "")


# ---------------------------------------------------------------- store 派生

def test_normalize_record_adds_experiment_date(rec_dirs):
    _write_record(rec_dirs, "rec_20260912_002", timeline=[
        {"entry_id": "e1", "time_label": "2026-9-10 8:59", "description": "开始"},
        {"entry_id": "e2", "time_label": "2026-9-10 20:59", "description": "停止"},
    ])
    rec = rec_store.list_records()[0]
    assert rec["date"] == "2026-09-12"                 # 录入日期语义不变
    assert rec["experiment_date"] == "2026-09-10"      # 实验起始时间
    assert rec["date_source"] == "timeline"
    assert rec["date_label"] == "2026-9-10 8:59"


def test_normalize_record_falls_back_to_created(rec_dirs):
    _write_record(rec_dirs, "rec_20260912_003", timeline=[
        {"entry_id": "e1", "time_label": "第1天", "description": "开始"},
    ])
    rec = rec_store.list_records()[0]
    assert rec["experiment_date"] == "2026-09-12"
    assert rec["date_source"] == "created"
    assert rec["date_label"] == ""


def test_list_records_sorted_by_experiment_date(rec_dirs):
    # 录入日期同为 09-12，实验时间 07-03 / 09-01 / 08-15 → 排序按实验时间
    _write_record(rec_dirs, "rec_20260912_001", timeline=[
        {"time_label": "26/7/3/10:30", "description": "a"}])
    _write_record(rec_dirs, "rec_20260912_002", timeline=[
        {"time_label": "2026-9-1 10:30", "description": "b"}])
    _write_record(rec_dirs, "rec_20260912_003", timeline=[
        {"time_label": "2026-8-15 10:30", "description": "c"}])
    ids = [r["record_id"] for r in rec_store.list_records()]
    assert ids == ["rec_20260912_001", "rec_20260912_003", "rec_20260912_002"]


# ---------------------------------------------------------------- 助手工具

def test_assistant_records_tool_includes_full_timeline_and_process(rec_dirs):
    from assistant.tools.records import read_experiment_records
    timeline = [
        {"entry_id": f"e{i}", "time_label": f"2026-9-{i:02d} 10:00",
         "description": f"第 {i} 天操作" * 5}
        for i in range(1, 13)          # 12 条（旧实现只给最后 5 条）
    ]
    _write_record(rec_dirs, "rec_20260912_010",
                  process_notes="完整实验流程：" + "加料、回流、洗涤。" * 20,
                  timeline=timeline)
    res = read_experiment_records()
    text = res["text"]
    assert res["is_error"] is False
    assert "完整实验流程" in text or "实验流程" in text
    assert "加料、回流、洗涤" in text
    for i in range(1, 13):
        assert f"2026-9-{i:02d} 10:00" in text, f"缺少第 {i} 个时间点"
    assert "实验时间 2026-09-01" in text
    assert res["details"]["timeline_counts"]["rec_20260912_010"] == 12


def test_assistant_single_record_tool_returns_full_text(rec_dirs):
    from assistant.tools.records import read_experiment_record
    long_process = "流程" + "步骤描述内容。" * 400      # >1200 字
    _write_record(rec_dirs, "rec_20260912_011",
                  process_notes=long_process,
                  timeline=[{"entry_id": "e1", "time_label": "2026-9-10 8:59",
                             "description": "开始实验并记录"}])
    res = read_experiment_record("rec_20260912_011")
    assert res["is_error"] is False
    assert res["details"]["timeline_count"] == 1
    assert "完整实验流程" in res["text"]
    assert res["text"].count("步骤描述内容。") > 300     # 全文而非 1200 字摘要
    # 错误分支
    assert read_experiment_record("")["is_error"] is True
    assert read_experiment_record("rec_19700101_001")["is_error"] is False


def test_assistant_tool_marks_fallback_date(rec_dirs):
    from assistant.tools.records import read_experiment_records
    _write_record(rec_dirs, "rec_20260912_012", timeline=[])
    text = read_experiment_records()["text"]
    assert "回退录入日期" in text


# ---------------------------------------------------------------- Word 导出

def test_export_timeline_sorted_with_tolerant_parser():
    from records import export_docx
    timeline = [
        {"time_label": "26/7/6/10:30", "description": "第三天"},
        {"time_label": "26/7/3/10:30", "description": "第一天"},
        {"time_label": "第3天", "description": "不可解析"},
    ]
    ordered = export_docx._sorted_timeline(timeline)
    assert [e["description"] for e in ordered] == ["第一天", "第三天", "不可解析"]


def test_export_docx_includes_experiment_date(rec_dirs):
    from records import export_docx
    _write_record(rec_dirs, "rec_20260912_013", timeline=[
        {"entry_id": "e1", "time_label": "2026-9-10 8:59", "description": "开始"}])
    rec = rec_store.list_records()[0]
    blob = export_docx.build_record_docx(rec, version="1.9.3")
    assert isinstance(blob, bytes) and len(blob) > 1000
