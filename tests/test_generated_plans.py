"""任务 B 测试：采纳建议 → 编号方案卡（src/recommend/generated_plans.py）。

用 tmp_path + monkeypatch 隔离数据目录，不碰真实 data/。
覆盖：正常采纳 / 版本号递增 / 回写状态 / 缺单体报错。
"""

from __future__ import annotations

import json

import pytest

from src.recommend import generated_plans


# ---------------------------------------------------------------- 夹具

def _write(path, obj: dict) -> None:
    """写 JSON 测试数据文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离目录：suggestions / records / favorites / generated_plans 全部指向 tmp。"""
    dirs = {
        "suggestions": tmp_path / "suggestions",
        "records": tmp_path / "records",
        "favorites": tmp_path / "favorites",
        "plans": tmp_path / "generated_plans",
    }
    monkeypatch.setattr(generated_plans, "SUGGESTIONS_DIR", dirs["suggestions"])
    monkeypatch.setattr(generated_plans, "RECORDS_DIR", dirs["records"])
    monkeypatch.setattr(generated_plans, "FAVORITES_DIR", dirs["favorites"])
    monkeypatch.setattr(generated_plans, "PLANS_DIR", dirs["plans"])
    return dirs


def _favorite(fav_id: str = "fav_20260722_001") -> dict:
    return {
        "id": fav_id,
        "aldehyde": {"smiles": "O=Cc1ccccc1C=O", "cas": "", "name": "测试醛"},
        "amine": {"smiles": "Nc1ccc(N)cc1", "cas": "", "name": "测试胺"},
        "created_at": "2026-07-22T09:00:00+08:00",
    }


def _suggestion(
    sug_id: str = "sug_20260722_001",
    favorite_id="fav_20260722_001",
    payload: dict | None = None,
    evidence_refs: list | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "record_type": "suggestion",
        "suggestion_id": sug_id,
        "favorite_id": favorite_id,
        "type": "condition_adjust",
        "payload": payload
        or {
            "adjustments": [
                {
                    "field": "time_days",
                    "from": "3天",
                    "to": "5天",
                    "rationale": "延长反应时间提高成膜完整性",
                }
            ]
        },
        "evidence_refs": evidence_refs or [],
        "created_at": "2026-07-22T14:00:00+08:00",
        "status": "new",
    }


# ---------------------------------------------------------------- 测试

class TestAdoptSuggestion:
    def test_adopt_normal(self, sandbox):
        """正常采纳：生成 plan dict，字段齐全，adjustments 原文保留。"""
        _write(sandbox["favorites"] / "fav_20260722_001.json", _favorite())
        _write(sandbox["suggestions"] / "sug_20260722_001.json", _suggestion())

        plan = generated_plans.adopt_suggestion("sug_20260722_001")

        # 顶层字段契约
        for key in (
            "plan_id", "seq", "favorite_id", "suggestion_id",
            "template_name", "plan_card", "adjustments_applied", "created_at",
        ):
            assert key in plan, f"缺字段 {key}"
        assert plan["seq"] == 1
        assert plan["favorite_id"] == "fav_20260722_001"
        assert plan["suggestion_id"] == "sug_20260722_001"
        assert plan["plan_id"].startswith("plan_")
        # 默认模板 = 侯老师 v3.9
        assert "侯老师" in plan["template_name"]
        # 方案卡内容来自单体
        assert plan["plan_card"]["aldehyde"]["name"] == "测试醛"
        assert plan["plan_card"]["amine"]["smiles"] == "Nc1ccc(N)cc1"
        # adjustments 原文保留，未被改写
        assert plan["adjustments_applied"][0]["to"] == "5天"
        # 落盘成功且内容与返回值一致
        saved = json.loads(
            (sandbox["plans"] / f"{plan['plan_id']}.json").read_text(encoding="utf-8")
        )
        assert saved == plan

    def test_seq_increments_per_favorite(self, sandbox):
        """同一 favorite 的方案版本号 seq 递增。"""
        _write(sandbox["favorites"] / "fav_20260722_001.json", _favorite())
        _write(sandbox["suggestions"] / "sug_20260722_001.json", _suggestion())
        _write(
            sandbox["suggestions"] / "sug_20260722_002.json",
            _suggestion(sug_id="sug_20260722_002"),
        )

        plan1 = generated_plans.adopt_suggestion("sug_20260722_001")
        plan2 = generated_plans.adopt_suggestion("sug_20260722_002")

        assert plan1["seq"] == 1
        assert plan2["seq"] == 2
        assert plan1["plan_id"] != plan2["plan_id"]

    def test_status_written_back(self, sandbox):
        """采纳后回写建议文件 status=adopted + adopted_plan_id。"""
        _write(sandbox["favorites"] / "fav_20260722_001.json", _favorite())
        _write(sandbox["suggestions"] / "sug_20260722_001.json", _suggestion())

        plan = generated_plans.adopt_suggestion("sug_20260722_001")

        sug = json.loads(
            (sandbox["suggestions"] / "sug_20260722_001.json").read_text(encoding="utf-8")
        )
        assert sug["status"] == "adopted"
        assert sug["adopted_plan_id"] == plan["plan_id"]

    def test_missing_monomers_raises(self, sandbox):
        """游离建议查不到单体（payload 无单体、无实验记录可反查）→ AdoptError。"""
        _write(
            sandbox["suggestions"] / "sug_20260722_009.json",
            _suggestion(
                sug_id="sug_20260722_009",
                favorite_id=None,
                payload={"adjustments": [{"field": "x", "from": "a", "to": "b"}]},
                evidence_refs=[{"kind": "experiment_record", "ref": "rec_不存在", "note": ""}],
            ),
        )

        with pytest.raises(generated_plans.AdoptError, match="单体"):
            generated_plans.adopt_suggestion("sug_20260722_009")
