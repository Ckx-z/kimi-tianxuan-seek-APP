"""指标名同义词归一化测试：双向展开 / 缩写 / 去重 / 边界 + 与真实检索的联动。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import library_db, metric_aliases  # noqa: E402


# ---------------------------------------------------------------- 纯函数

def test_expand_chinese_hits_english_aliases():
    got = metric_aliases.expand_metric_name("比表面积")
    low = [g.lower() for g in got]
    assert "比表面积" in got
    assert "langmuir surface area" in low and "bet surface area" in low
    assert any("surface area" in g for g in low)


def test_expand_english_hits_chinese_canonical():
    """英文输入也应展开到中文规范名（反向可用）。"""
    got = metric_aliases.expand_metric_name("pore size")
    assert "孔径" in got
    assert any("pore diameter" in g.lower() for g in got)


def test_expand_partial_and_abbreviation():
    # 部分匹配（"surface area" 是别名之一）
    assert "比表面积" in metric_aliases.expand_metric_name("surface area")
    # 缩写（大小写不敏感）
    assert "比表面积" in metric_aliases.expand_metric_name("SSA")
    assert "带隙" in metric_aliases.expand_metric_name("Eg")


def test_expand_dedupes_and_handles_blank():
    got = metric_aliases.expand_metric_name("比表面积")
    assert len(got) == len({g.lower() for g in got}), "不应有重复项"
    assert metric_aliases.expand_metric_name("   ") == [""]
    # 未收录词 → 只返回原词（不误扩）
    assert metric_aliases.expand_metric_name("某种没收录的指标") == ["某种没收录的指标"]


# ---------------------------------------------------------------- 与检索联动

PAPERS = {"1": {"title": "A", "doi": "10.1/a", "source": "builtin"},
          "2": {"title": "B", "doi": "10.1/b", "source": "builtin"}}
ENTRIES = [
    {"entry_id": "e1", "paper_id": "1", "kind": "characterization",
     "technique": "BET",
     "metrics": [{"name": "Langmuir surface area", "value": 3336, "unit": "m2 g-1"},
                 {"name": "pore size", "value": 3.26, "unit": "nm"}]},
    {"entry_id": "e2", "paper_id": "2", "kind": "characterization",
     "technique": "XRD",
     "metrics": [{"name": "N-H stretching peak", "value": 3326, "unit": "cm-1"}]},
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, res = tmp_path / "data", tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
    (data / "literature" / "knowledge_entries.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in ENTRIES), encoding="utf-8")
    patched = []
    for name in ("src.runtime_config", "runtime_config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "user_data_root"):
            monkeypatch.setattr(mod, "user_data_root", lambda: data)
            monkeypatch.setattr(mod, "resource_root", lambda: res)
            patched.append(name)
    assert patched
    monkeypatch.setattr(library_db, "db_path", lambda: data / "literature" / "library.db")
    monkeypatch.setenv("COF_DATA_DIR", str(data))
    library_db.reset_ready_cache()
    yield {"data": data}
    library_db.reset_ready_cache()


def test_search_metrics_chinese_query_hits_english_rows(env):
    """**这是本次修复的核心价值**：中文查询命中英文指标名。"""
    library_db.ensure_ready()
    conn = library_db.connect(env["data"] / "literature" / "library.db")
    hits = library_db.search_metrics(conn, name="比表面积")
    names = {h["name"] for h in hits}
    assert "Langmuir surface area" in names, f"应命中英文指标名，实际 {names}"

    pore = library_db.search_metrics(conn, name="孔径")
    assert {h["name"] for h in pore} == {"pore size"}

    # 英文查询仍照旧工作，且不会误命中无关指标
    peak = library_db.search_metrics(conn, name="peak")
    assert {h["name"] for h in peak} == {"N-H stretching peak"}
    # 数值过滤与别名同时生效
    hot = library_db.search_metrics(conn, name="比表面积", min_value=3000)
    assert len(hot) == 1 and hot[0]["value"] == 3336
    conn.close()
