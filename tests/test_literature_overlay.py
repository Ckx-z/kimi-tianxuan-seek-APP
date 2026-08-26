"""文献库 frozen overlay 读写测试（PyInstaller onedir 打包兼容性）。

overlay 策略（src/references/titles.py）：
- 读：用户库（user_data_root/literature/paper_titles.json）存在则整体优先，
  否则回退内置库（打包资源/源码 data/）；
- 写：永远写用户库，首次写入先全量复制内置库再追加（copy-on-first-write）；
- 审计流水 literature_intake.jsonl 在 user_data_root/literature/ 下；
- 源码开发态（非 frozen 且无 COF_DATA_DIR）行为不变，直接读写 data/ 下。

测试用 COF_DATA_DIR 指向 tmp 目录模拟分发语义；内置库用 tmp 副本，
全程断言其字节不被修改（frozen 下它是只读的）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from literature import resolver  # noqa: E402
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

MINI_LIB = {
    "1": {"doi": "10.1021/abc", "title": "Paper One"},
    "5": {"doi": "", "title": "Paper Five No Doi"},
}


def _titles_modules():
    """references.titles 可能以裸名与 src.* 两种形式各被 import 一次（双实例），
    两个实例的 BUNDLED_PATH / TITLES_PATH / 缓存都要隔离。"""
    mods = []
    for name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(name)
        if mod is not None and mod not in mods:
            mods.append(mod)
    return mods


@pytest.fixture()
def overlay_env(tmp_path, monkeypatch):
    """分发语义环境：COF_DATA_DIR 指向独立用户目录，内置库为 tmp 只读副本。"""
    bundled = tmp_path / "bundled" / "paper_titles.json"
    bundled.parent.mkdir(parents=True)
    bundled.write_text(json.dumps(MINI_LIB, ensure_ascii=False), encoding="utf-8")
    user_root = tmp_path / "user_data"
    monkeypatch.setenv("COF_DATA_DIR", str(user_root))
    for mod in _titles_modules():
        monkeypatch.setattr(mod, "BUNDLED_PATH", bundled)
        monkeypatch.setattr(mod, "TITLES_PATH", None)
        mod.reload()
    # 审计流水静态路径也隔离到用户目录（默认位置另有专项断言）
    monkeypatch.setattr(
        resolver, "INTAKE_PATH",
        user_root / "data" / "literature" / "literature_intake.jsonl")
    yield {
        "bundled": bundled,
        "user_root": user_root,
        # COF_DATA_DIR 视为应用根，user_data_root() 再下钻一层 data/
        "user_lib": user_root / "data" / "literature" / "paper_titles.json",
    }
    for mod in _titles_modules():
        mod.reload()


# ---------------------------------------------------------------- 读 overlay

class TestOverlayRead:
    def test_read_falls_back_to_bundled_when_no_user_lib(self, overlay_env):
        assert not overlay_env["user_lib"].exists()
        assert titles.titles_path() == overlay_env["bundled"]
        assert titles.resolve_title("1") == "Paper One"
        assert titles.resolve_title("5") == "Paper Five No Doi"

    def test_read_prefers_user_lib_when_present(self, overlay_env):
        user_lib = overlay_env["user_lib"]
        user_lib.parent.mkdir(parents=True)
        user_lib.write_text(
            json.dumps({"1": {"title": "User Override", "doi": "10.1/u"}},
                       ensure_ascii=False),
            encoding="utf-8")
        titles.reload()
        assert titles.titles_path() == user_lib
        assert titles.resolve_title("1") == "User Override"
        # 逐条合并：用户库条目覆盖/新增，内置库其余条目仍可见
        # （未来包内内置库升级新增条目时老用户不丢失）
        assert titles.resolve_title("5") == "Paper Five No Doi"


# ---------------------------------------------------------------- 写 overlay

class TestOverlayWrite:
    def test_first_write_copies_bundled_then_appends(self, overlay_env):
        bundled_before = overlay_env["bundled"].read_bytes()
        pid = resolver.append_paper({"title": "New Paper", "doi": "10.5555/new"})
        assert pid == "6"  # 复制后的最大 id 5 → 新 id 6
        user_lib = overlay_env["user_lib"]
        assert user_lib.exists()
        lib = json.loads(user_lib.read_text(encoding="utf-8"))
        # 内置库条目全量保留 + 新条目
        assert lib["1"] == MINI_LIB["1"]
        assert lib["5"] == MINI_LIB["5"]
        assert lib["6"] == {"title": "New Paper", "doi": "10.5555/new"}
        # 内置库（frozen 只读语义）字节级未变
        assert overlay_env["bundled"].read_bytes() == bundled_before
        # 写后读取自动切到用户库
        titles.reload()
        assert titles.titles_path() == user_lib
        assert titles.resolve_title("6") == "New Paper"
        assert resolver.resolve_paper("1")["title"] == "Paper One"

    def test_write_does_not_recopy_when_user_lib_exists(self, overlay_env):
        resolver.append_paper({"title": "A", "doi": ""})
        # 模拟用户在用户库里的既有修改
        user_lib = overlay_env["user_lib"]
        lib = json.loads(user_lib.read_text(encoding="utf-8"))
        lib["1"]["title"] = "User Edited"
        user_lib.write_text(json.dumps(lib, ensure_ascii=False), encoding="utf-8")
        titles.reload()
        resolver.append_paper({"title": "B", "doi": ""})
        after = json.loads(user_lib.read_text(encoding="utf-8"))
        assert after["1"]["title"] == "User Edited"  # 不被内置库重复制覆盖
        assert after["7"]["title"] == "B"

    def test_confirm_api_writes_user_lib_and_audit(self, overlay_env):
        r = client.post("/api/literature/confirm", json={
            "title": "Overlay 入库文献", "doi": "10.5555/overlay",
            "reviewed_by": "user",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["paper_id"] == "6"
        assert body["audit_written"] is True
        lib = json.loads(overlay_env["user_lib"].read_text(encoding="utf-8"))
        assert lib["6"]["title"] == "Overlay 入库文献"
        assert lib["1"] == MINI_LIB["1"]  # 内置条目随复制保留
        # 审计流水落在用户目录 literature/ 下
        intake = resolver.INTAKE_PATH
        assert intake.parent == overlay_env["user_root"] / "data" / "literature"
        audit = json.loads(intake.read_text(encoding="utf-8").strip().splitlines()[0])
        assert audit["action"] == "confirm_intake"
        assert audit["paper_id"] == "6"
        # 内置库未被写入
        assert json.loads(overlay_env["bundled"].read_text(encoding="utf-8")) == MINI_LIB

    def test_intake_default_location_under_literature_dir(self, monkeypatch):
        """审计流水默认位置：user_data_root/literature/literature_intake.jsonl。"""
        monkeypatch.delenv("COF_DATA_DIR", raising=False)
        try:
            from src import runtime_config
        except ImportError:
            import runtime_config  # type: ignore
        assert resolver.INTAKE_PATH == (
            runtime_config.user_data_root() / "literature" / "literature_intake.jsonl")


# ---------------------------------------------------------------- 源码开发态不变

class TestDevModeUnchanged:
    def test_no_overlay_without_frozen_or_env(self, monkeypatch):
        """非 frozen 且无 COF_DATA_DIR：读写都直接指向内置库（历史行为）。"""
        monkeypatch.delenv("COF_DATA_DIR", raising=False)
        mods = _titles_modules()
        for mod in mods:
            monkeypatch.setattr(mod, "TITLES_PATH", None)
            mod.reload()
        assert titles._overlay_active() is False
        assert titles.titles_path() == Path(titles.BUNDLED_PATH)
        assert titles.writable_titles_path() == Path(titles.BUNDLED_PATH)
        for mod in mods:
            mod.reload()
