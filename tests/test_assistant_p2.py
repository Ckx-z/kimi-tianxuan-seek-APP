"""P2 测试（v1.6.0）：SKILLS 机制 / 按单体组记忆 / 新失误记录提醒。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.llm.client as llm_client  # noqa: E402
from src.assistant import brief, memory, persona, skills  # noqa: E402
from src.assistant import llm_bridge  # noqa: E402


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离：memory 路径 / 技能覆盖目录 / LLM 配置 / nudge dismiss 状态。"""
    monkeypatch.setattr(memory, "MEMORY_PATH",
                        tmp_path / "assistant" / "memory.md")
    monkeypatch.setattr(memory, "PAIR_DIR",
                        tmp_path / "assistant" / "agent_memory")
    monkeypatch.setattr(skills, "_OVERLAY_DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "_OVERRIDE_PATH",
                        tmp_path / "config" / "skills.local.json")
    monkeypatch.setattr(brief, "NUDGE_DISMISS_PATH",
                        tmp_path / "assistant" / "nudge_dismissals.json")
    monkeypatch.setattr(llm_client, "LOCAL_SETTINGS",
                        tmp_path / "llm_settings.local.json")
    return tmp_path


# ---------------------------------------------------------------- SKILLS

def test_skills_builtin_listed():
    names = {s["name"] for s in skills.list_skills()}
    assert {"iterate_methodology", "literature_search_sop",
            "failure_analysis"} <= names
    for s in skills.list_skills():
        assert s["enabled"] is True  # 默认启用
        assert s["description"]


def test_skills_user_overlay_overrides(iso):
    (iso / "skills").mkdir(parents=True)
    (iso / "skills" / "iterate_methodology.md").write_text(
        "---\nname: iterate_methodology\ndescription: 用户自定版本\n"
        "default-enabled: true\n---\n# 用户改的正文\n自定义方法论", encoding="utf-8")
    lst = {s["name"]: s for s in skills.list_skills()}
    assert lst["iterate_methodology"]["source"] == "user"
    assert lst["iterate_methodology"]["description"] == "用户自定版本"
    block = skills.skills_block()
    assert "用户改的正文" in block and "自定义方法论" in block


def test_skills_set_enabled_roundtrip(iso):
    assert skills.set_enabled("failure_analysis", False) is True
    lst = {s["name"]: s for s in skills.list_skills()}
    assert lst["failure_analysis"]["enabled"] is False
    assert "failure_analysis" not in skills.skills_block()
    assert skills.set_enabled("nope", True) is False  # 不存在
    assert skills.set_enabled("bad/name", True) is False  # 非法名


def test_persona_includes_skills_block():
    prompt = persona.build_system_prompt()
    assert "# 技能（方法论，按此执行）" in prompt
    assert "iterate_methodology" in prompt


# ---------------------------------------------------------------- 按单体组记忆

def _fake_favorite():
    return {"aldehyde": {"cas": "443922-06-3", "name": "三氟甲基苯甲醛",
                         "smiles": "A"},
            "amine": {"cas": "341-58-2", "name": "TFMB", "smiles": "B"}}


def test_pair_from_context_with_favorite(monkeypatch):
    monkeypatch.setattr(memory, "pair_from_context",
                        memory.pair_from_context)  # 保持原函数
    import src.favorites.store as fav_store
    monkeypatch.setattr(fav_store, "get_favorite",
                        lambda fid: _fake_favorite())
    key, label = memory.pair_from_context({"favorite_id": "fav_x"})
    assert key == "443922-06-3_341-58-2"
    assert "三氟甲基苯甲醛" in label


def test_pair_from_context_with_smiles_only():
    key, label = memory.pair_from_context(
        {"ald_smiles": "O=Cc1ccccc1", "amine_smiles": "Nc1ccc(N)cc1"})
    assert key.startswith("smiles_")
    assert _KEY_OK(key)


def _KEY_OK(key: str) -> bool:
    return bool(memory._KEY_RE.match(key))


def test_pair_memory_roundtrip(iso):
    key = "443922-06-3_341-58-2"
    assert memory.append_pair_memory(key, "醛 + 胺", ["该组倾向低温长时"]) == 1
    content = memory.read_pair_memory(key)
    assert "label: 醛 + 胺" in content
    assert "该组倾向低温长时" in content
    lst = memory.list_pair_memories()
    assert len(lst) == 1 and lst[0]["key"] == key and lst[0]["entries"] == 1
    assert memory.clear_pair_memory(key) is True
    assert memory.list_pair_memories() == []
    assert memory.clear_pair_memory(key) is False
    assert memory.read_pair_memory("bad/../key") == ""
    assert memory.append_pair_memory("bad key!", "x", ["x"]) == 0


def test_injection_block_prefers_pair(iso, monkeypatch):
    import src.favorites.store as fav_store
    monkeypatch.setattr(fav_store, "get_favorite",
                        lambda fid: _fake_favorite())
    memory.set_enabled(True)
    memory.append_pair_memory("443922-06-3_341-58-2", "组A",
                              ["该组低温更易成膜"])
    memory.append_entries(["全局记忆条目"])
    block = memory.injection_block(
        {"session_id": "sess_x", "context": {"favorite_id": "fav_x"}})
    assert "单体组专属记忆" in block
    assert "该组低温更易成膜" in block
    assert "全局记忆条目" in block  # 全局仍在
    # 无 session：只有全局，无组专属
    block2 = memory.injection_block()
    assert "单体组专属记忆" not in block2
    assert "全局记忆条目" in block2


def test_compile_mirrors_pair(iso, monkeypatch):
    import src.favorites.store as fav_store
    monkeypatch.setattr(fav_store, "get_favorite",
                        lambda fid: _fake_favorite())
    monkeypatch.setattr(llm_bridge, "chat_text",
                        lambda msgs, max_tokens=None:
                        "- 该组实验倾向低温长时\n- 用户偏好均三甲苯体系")
    memory.set_enabled(True)
    sess = {
        "session_id": "sess_x",
        "context": {"favorite_id": "fav_x"},
        "messages": [{"role": "user", "content": "这组怎么调"},
                     {"role": "assistant", "content": "建议低温"}],
    }
    n = memory.compile_session(sess)
    assert n == 2
    assert "该组实验倾向低温长时" in memory.read_pair_memory(
        "443922-06-3_341-58-2")
    assert len(memory.list_pair_memories()) == 1


# ---------------------------------------------------------------- 新失误提醒

def test_new_mistake_nudge(iso, monkeypatch):
    today = datetime.now().astimezone().date().isoformat()
    rec = {"record_id": "rec_20260904_001", "favorite_id": "fav_a",
           "status": "final", "outcome": "failed",
           "mistakes": "温度过高导致爆沸",
           "aldehyde": {"name": "醛A"}, "amine": {"name": "胺A"}}
    monkeypatch.setattr(brief, "_records_with_mtime", lambda: [(rec, today)])
    import src.favorites.store as fav_store
    monkeypatch.setattr(fav_store, "get_favorite", lambda fid: None)
    nudges = brief.compute_new_mistake_nudges()
    assert len(nudges) == 1
    n = nudges[0]
    assert n["kind"] == "new_mistake"
    assert n["record_id"] == "rec_20260904_001"
    assert "爆沸" in n["latest_mistakes"]
    # 草稿 / 无失误 / 非今天 mtime → 不提醒
    monkeypatch.setattr(brief, "_records_with_mtime", lambda: [
        ({**rec, "status": "draft"}, today),
        ({**rec, "mistakes": ""}, today),
        (rec, "2020-01-01"),
    ])
    assert brief.compute_new_mistake_nudges() == []


def test_list_nudges_merges_kinds_and_dismiss(iso, monkeypatch):
    today = datetime.now().astimezone().date().isoformat()
    rec = {"record_id": "rec_1", "favorite_id": "fav_a",
           "status": "final", "mistakes": "搅拌不足",
           "aldehyde": {"name": "醛A"}, "amine": {"name": "胺A"}}
    monkeypatch.setattr(brief, "_records_with_mtime", lambda: [(rec, today)])
    monkeypatch.setattr(brief, "compute_failure_nudges", lambda: [{
        "kind": "consecutive_failure", "favorite_id": "fav_a",
        "monomers": "醛A + 胺A", "consecutive_failures": 2,
        "latest_mistakes": "x", "suggestion": "s"}])
    import src.favorites.store as fav_store
    monkeypatch.setattr(fav_store, "get_favorite", lambda fid: None)
    nudges = brief.list_nudges()
    kinds = {n["kind"] for n in nudges}
    assert kinds == {"new_mistake", "consecutive_failure"}
    # dismiss 后该收藏两类都消失
    brief.dismiss_nudge("fav_a")
    assert brief.list_nudges() == []
