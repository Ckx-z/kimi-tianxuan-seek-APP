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


# ---------------------------------------------------- 删除批注的图片清理（v2.0.1）

def test_delete_image_annotation_removes_png(conn, env):
    """删截图批注 → 数据库行与落盘 PNG 一起消失（此前只会删行、图片永久残留）。"""
    a = reading.add_annotation(conn, "1", type_="image", page=2,
                               comment="TEM 截图", image_data=PNG)
    p = Path(a["image_file"])
    assert p.is_file()
    assert reading.delete_annotation(conn, a["annotation_id"]) is True
    assert not p.exists(), "PNG 应被连带删除"
    assert reading.list_annotations(conn, "1") == []
    assert reading.annotation_image_path(conn, a["annotation_id"]) is None
    # 幂等：重复删除返回 False 且不抛错
    assert reading.delete_annotation(conn, a["annotation_id"]) is False


def test_delete_text_annotation_keeps_others(conn):
    """删文字批注不动别人的图片，也不该因为「没有图」而报错。"""
    img = reading.add_annotation(conn, "1", type_="image", page=1,
                                 comment="SEM", image_data=PNG)
    txt = reading.add_annotation(conn, "1", type_="highlight", page=1, text="hi")
    assert reading.delete_annotation(conn, txt["annotation_id"]) is True
    assert Path(img["image_file"]).is_file(), "同文献其他截图不该被误删"
    left = reading.list_annotations(conn, "1")
    assert [x["annotation_id"] for x in left] == [img["annotation_id"]]


def test_delete_refuses_path_outside_annotation_root(conn, env):
    """库里若存了批注目录之外的路径（异常/被改），只删行、绝不越权删文件。"""
    outside = env["data"] / "literature" / "keep_me.png"
    outside.write_bytes(PNG)
    a = reading.add_annotation(conn, "1", type_="image", page=1, image_data=PNG)
    conn.execute("UPDATE item_annotations SET image_file=? WHERE annotation_id=?",
                 (str(outside), a["annotation_id"]))
    conn.commit()
    assert reading.delete_annotation(conn, a["annotation_id"]) is True
    assert outside.is_file(), "根目录之外的文件必须保留"


def test_unlink_failure_does_not_break_delete(conn, monkeypatch):
    """图片删不掉（被杀软/占用锁住）时，批注行仍要删掉、接口不报错。"""
    from pathlib import Path as _Path
    a = reading.add_annotation(conn, "1", type_="image", page=1, image_data=PNG)
    real_unlink = _Path.unlink

    def boom(self, *args, **kwargs):          # noqa: ANN001
        raise OSError("文件被占用")

    monkeypatch.setattr(_Path, "unlink", boom)
    assert reading.delete_annotation(conn, a["annotation_id"]) is True
    monkeypatch.setattr(_Path, "unlink", real_unlink)
    assert reading.list_annotations(conn, "1") == []

