"""区域截图批注（R2 增强）测试：图片落盘 / 取图 / 无图语义 / 尺寸上限 / 老库迁移。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import library_db, reading  # noqa: E402

PAPERS = {"1": {"title": "T", "doi": "10.1/a", "source": "builtin"}}
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, res = tmp_path / "data", tmp_path / "res"
    (data / "literature").mkdir(parents=True)
    (res / "data").mkdir(parents=True)
    (res / "data" / "paper_titles.json").write_text(
        json.dumps(PAPERS, ensure_ascii=False), encoding="utf-8")
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


@pytest.fixture()
def conn(env):
    library_db.ensure_ready()
    c = library_db.connect(env["data"] / "literature" / "library.db")
    yield c
    c.close()


def test_image_annotation_saved_and_served(conn, env):
    a = reading.add_annotation(conn, "1", type_="image", page=3,
                              comment="SEM 截图", image_data=PNG,
                              position={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1})
    assert a["image_file"], "应记录图片路径"
    p = Path(a["image_file"])
    assert p.is_file() and p.read_bytes() == PNG
    # 落在约定的按文献分目录下
    assert p.parent.name == "1" and p.parent.parent.name == "annotations"
    # 可取回（供 /annotations/{id}/image 使用）
    got = reading.annotation_image_path(conn, a["annotation_id"])
    assert got is not None and got.read_bytes() == PNG
    # 列表里也能看到 image_file 与坐标
    listed = reading.list_annotations(conn, "1")[0]
    assert listed["image_file"] and listed["position"]["w"] == 0.3


def test_non_image_annotation_has_no_file(conn):
    b = reading.add_annotation(conn, "1", type_="highlight", page=1, text="hi")
    assert b["image_file"] is None
    assert reading.annotation_image_path(conn, b["annotation_id"]) is None


def test_image_size_limit(conn):
    with pytest.raises(ValueError):
        reading.add_annotation(conn, "1", type_="image",
                               image_data=b"x" * (8 * 1024 * 1024 + 1))


def test_legacy_table_gets_column_added(env):
    """老库（item_annotations 无 image_file）→ ensure_schema 自动加列（PRAGMA 守卫）。"""
    path = env["data"] / "literature" / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE item_annotations (
        annotation_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, attachment_id TEXT,
        type TEXT NOT NULL, page INTEGER, position_json TEXT, color TEXT,
        text TEXT, comment TEXT, created_at TEXT, updated_at TEXT)""")
    conn.commit()
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(item_annotations)")}
    assert "image_file" not in cols_before
    reading.ensure_schema(conn)
    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(item_annotations)")}
    assert "image_file" in cols_after
    # 幂等：再跑一次不报错
    reading.ensure_schema(conn)
    assert {r["name"] for r in conn.execute("PRAGMA table_info(item_annotations)")} == \
        cols_after
    conn.close()
