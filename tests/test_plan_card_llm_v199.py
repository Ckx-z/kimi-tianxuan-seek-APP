"""F3.2 测试：方案卡用量写入 conditions + LLM 提议与降级。

不打真实 LLM：注入 chat 打桩。
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

from recommend import plan_card_llm as pcl  # noqa: E402

BASE = {
    "template": "默认成膜方案",
    "conditions": {"temperature": "室温", "note": "模板默认"},
    "steps": [{"title": "配液"}, {"title": "成膜"}],
}


# ---------------------------------------------------------------- 核心：写用量

def test_apply_dosages_writes_into_conditions():
    """**这是 F3.2 的核心修复**：用量必须进 conditions，且来源可追溯。"""
    out = pcl.apply_dosages(BASE, {"aldehyde_amine_ratio": "1:1.2",
                                   "solvent": "均三甲苯/二氧六环 1:1",
                                   "catalyst_eq": "3 eq", "time": "72 h"},
                            source="discussed")
    assert out["conditions"]["aldehyde_amine_ratio"] == "1:1.2"
    assert out["conditions"]["solvent"].startswith("均三甲苯")
    assert out["conditions"]["catalyst_eq"] == "3 eq"
    assert out["dosage_source"] == "discussed"
    assert set(out["dosage_fields"]) == {"aldehyde_amine_ratio", "solvent",
                                         "catalyst_eq", "time"}
    # 模板原有条件保留（同键覆盖，其余不动）
    assert out["conditions"]["note"] == "模板默认"
    # 不修改入参（纯函数）
    assert "aldehyde_amine_ratio" not in BASE["conditions"]


def test_apply_dosages_empty_marks_default_without_polluting():
    out = pcl.apply_dosages(BASE, None)
    assert out["dosage_source"] == "default" and out["dosage_fields"] == []
    assert out["conditions"] == BASE["conditions"]
    out2 = pcl.apply_dosages(BASE, {"": "", "x": None})     # 空值被过滤
    assert out2["dosage_source"] == "default" and out2["dosage_fields"] == []


# ---------------------------------------------------------------- LLM 提议

def test_propose_dosages_parses_and_user_values_win():
    def fake_chat(messages, **kw):
        return ('说明文字…\n{"amounts": {"aldehyde_amine_ratio": "1:3", '
                '"solvent": "DMF"}, "rationale": "按文献 A 的比例"}')

    out = pcl.propose_dosages({"ald": "A", "amine": "B"}, "高结晶度薄膜",
                              amounts={"aldehyde_amine_ratio": "1:1.2"},   # 用户已确认
                              steps=BASE["steps"], chat=fake_chat)
    assert out["ok"] is True
    assert out["amounts"]["solvent"] == "DMF"
    assert out["amounts"]["aldehyde_amine_ratio"] == "1:1.2"      # **用户确认项胜出**
    assert "文献 A" in out["rationale"]


@pytest.mark.parametrize("reply", [
    "没有任何 JSON 的回复",
    "{坏 JSON",
    "{}",                       # 无 amounts
    "",
])
def test_propose_dosages_degrades_without_raising(reply):
    out = pcl.propose_dosages(None, "", chat=lambda m, **kw: reply)
    assert out["ok"] is False and out["amounts"] == {}


def test_propose_dosages_handles_llm_failure():
    def boom(messages, **kw):
        raise TimeoutError("t")

    out = pcl.propose_dosages(None, "", chat=boom)
    assert out["ok"] is False and "TimeoutError" in out["error"]


# ---------------------------------------------------------------- 组合

def test_generate_card_with_user_amounts_skips_llm():
    called = {"n": 0}

    def fake_chat(*a, **kw):
        called["n"] += 1
        return "{}"

    out = pcl.generate_card_with_dosages(BASE, amounts={"solvent": "水"}, chat=fake_chat)
    assert called["n"] == 0, "用户已确认用量时不应再调模型"
    assert out["conditions"]["solvent"] == "水" and out["dosage_source"] == "discussed"


def test_generate_card_from_llm_then_degrades():
    def ok_chat(messages, **kw):
        return json.dumps({"amounts": {"temperature": "90 °C", "time": "24 h"},
                           "rationale": "常规溶剂热"}, ensure_ascii=False)

    ok = pcl.generate_card_with_dosages(BASE, monomers={"ald": "A"}, goal="成膜",
                                        chat=ok_chat)
    assert ok["conditions"]["temperature"] == "90 °C"
    assert ok["dosage_source"] == "literature" and "常规溶剂热" in ok["dosage_rationale"]

    def bad_chat(messages, **kw):
        raise RuntimeError("endpoint down")

    deg = pcl.generate_card_with_dosages(BASE, goal="成膜", chat=bad_chat)
    assert deg["dosage_source"] == "default"
    assert deg["conditions"]["temperature"] == "室温"          # 模板默认保留
    assert "模板默认" in deg["dosage_rationale"]
