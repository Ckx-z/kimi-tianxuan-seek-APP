"""成膜打分阶段三（v1.6.1）测试：GNN v5.4 随包运行时（gnn_runtime/ + models/gnn_v5.4/）。

覆盖：包内运行时优先 / 旧项目回退 / 全缺失降级、子进程调用参数
（脚本、checkpoint、cwd）、概率解析，以及包内资产就位护栏（防打包漏件）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.predictor import gnn_model  # noqa: E402

BUNDLED_SCRIPT = PROJECT_ROOT / "gnn_runtime" / "predict_pair.py"
BUNDLED_CKPT = PROJECT_ROOT / "models" / "gnn_v5.4" / "v5_model.pt"
CALIBRATOR = PROJECT_ROOT / "models" / "gnn_v5.4" / "calibrator.pkl"


@pytest.fixture(autouse=True)
def _isolate_dynamic_resolution(monkeypatch):
    """v1.8.0：隔离 registry/env 动态解析（测试只针对包内/旧项目回退逻辑）。"""
    monkeypatch.setattr(gnn_model, "_env_checkpoint", lambda: None)
    monkeypatch.setattr(gnn_model, "_active_retrained_checkpoint", lambda: None)


# ---------------------------------------------------------------- 包内资产护栏

def test_bundled_assets_present():
    """打包护栏：包内 GNN 运行时与 v5.4 权重/校准器必须就位。"""
    assert BUNDLED_SCRIPT.is_file(), "gnn_runtime/predict_pair.py 缺失"
    for rel in ("src/screening/gnn_v3/featurizer.py",
                "src/screening/gnn_v4/model.py",
                "src/screening/gnn_v4/encoder.py",
                "src/screening/gnn_v4/attention.py",
                "src/screening/gnn_v4/pooling.py",
                "src/screening/gnn_v4/heads.py",
                "src/chemistry/hard_rules.py"):
        assert (PROJECT_ROOT / "gnn_runtime" / rel).is_file(), f"{rel} 缺失"
    assert BUNDLED_CKPT.is_file(), "models/gnn_v5.4/v5_model.pt 缺失"
    assert CALIBRATOR.is_file(), "models/gnn_v5.4/calibrator.pkl 缺失"


def test_default_checkpoint_prefers_bundled():
    if BUNDLED_CKPT.exists():
        assert gnn_model.DEFAULT_CHECKPOINT == BUNDLED_CKPT


# ---------------------------------------------------------------- _resolve_runtime

def _mk(tmp_path: Path, rel: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


def test_resolve_runtime_prefers_bundled(monkeypatch, tmp_path):
    bscript = _mk(tmp_path, "gnn_runtime/predict_pair.py")
    bckpt = _mk(tmp_path, "models/gnn_v5.4/v5_model.pt")
    lscript = _mk(tmp_path, "old/predict_pair.py")
    lckpt = _mk(tmp_path, "old/models/v5.4/v5_model.pt")
    monkeypatch.setattr(gnn_model, "BUNDLED_SCRIPT", bscript)
    monkeypatch.setattr(gnn_model, "BUNDLED_CHECKPOINT", bckpt)
    monkeypatch.setattr(gnn_model, "LEGACY_SCRIPT", lscript)
    monkeypatch.setattr(gnn_model, "LEGACY_CHECKPOINT", lckpt)
    script, ckpt = gnn_model._resolve_runtime()
    assert script == bscript
    assert ckpt == bckpt


def test_resolve_runtime_bundled_script_with_legacy_checkpoint(monkeypatch, tmp_path):
    """包内脚本 + 包内模型缺失 → 回退旧项目模型（开发机场景）。"""
    bscript = _mk(tmp_path, "gnn_runtime/predict_pair.py")
    lscript = _mk(tmp_path, "old/predict_pair.py")
    lckpt = _mk(tmp_path, "old/models/v5.4/v5_model.pt")
    monkeypatch.setattr(gnn_model, "BUNDLED_SCRIPT", bscript)
    monkeypatch.setattr(gnn_model, "BUNDLED_CHECKPOINT",
                        tmp_path / "models" / "gnn_v5.4" / "v5_model.pt")
    monkeypatch.setattr(gnn_model, "LEGACY_SCRIPT", lscript)
    monkeypatch.setattr(gnn_model, "LEGACY_CHECKPOINT", lckpt)
    script, ckpt = gnn_model._resolve_runtime()
    assert script == bscript
    assert ckpt == lckpt


def test_resolve_runtime_legacy_fallback(monkeypatch, tmp_path):
    lscript = _mk(tmp_path, "old/predict_pair.py")
    lckpt = _mk(tmp_path, "old/models/v5.4/v5_model.pt")
    monkeypatch.setattr(gnn_model, "BUNDLED_SCRIPT",
                        tmp_path / "gnn_runtime" / "predict_pair.py")
    monkeypatch.setattr(gnn_model, "BUNDLED_CHECKPOINT",
                        tmp_path / "models" / "gnn_v5.4" / "v5_model.pt")
    monkeypatch.setattr(gnn_model, "LEGACY_SCRIPT", lscript)
    monkeypatch.setattr(gnn_model, "LEGACY_CHECKPOINT", lckpt)
    script, ckpt = gnn_model._resolve_runtime()
    assert script == lscript
    assert ckpt == lckpt


def test_resolve_runtime_all_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(gnn_model, "BUNDLED_SCRIPT",
                        tmp_path / "gnn_runtime" / "predict_pair.py")
    monkeypatch.setattr(gnn_model, "BUNDLED_CHECKPOINT",
                        tmp_path / "models" / "gnn_v5.4" / "v5_model.pt")
    monkeypatch.setattr(gnn_model, "LEGACY_SCRIPT",
                        tmp_path / "old" / "predict_pair.py")
    monkeypatch.setattr(gnn_model, "LEGACY_CHECKPOINT",
                        tmp_path / "old" / "models" / "v5.4" / "v5_model.pt")
    assert gnn_model._resolve_runtime() == (None, None)


# ---------------------------------------------------------------- predict_single

def test_predict_single_missing_python_raises_graceful(monkeypatch):
    monkeypatch.setattr(gnn_model, "_find_python", lambda: None)
    p = gnn_model.GNNFilmPredictor(checkpoint_path=Path("/nope.pt"))
    with pytest.raises(RuntimeError, match="GNN 不可用"):
        p.predict_single("O=Cc1ccccc1", "Nc1ccccc1")


def test_predict_single_missing_runtime_raises_graceful(monkeypatch):
    monkeypatch.setattr(gnn_model, "_find_python", lambda: Path("/py/python.exe"))
    monkeypatch.setattr(gnn_model, "_resolve_runtime", lambda: (None, None))
    p = gnn_model.GNNFilmPredictor(checkpoint_path=Path("/nope.pt"))
    with pytest.raises(RuntimeError, match="GNN 不可用"):
        p.predict_single("O=Cc1ccccc1", "Nc1ccccc1")


def test_predict_single_uses_resolved_runtime(monkeypatch, tmp_path):
    """子进程调用必须用 _resolve_runtime 给出的脚本+checkpoint，cwd=脚本目录。"""
    script = _mk(tmp_path, "gnn_runtime/predict_pair.py")
    ckpt = _mk(tmp_path, "models/gnn_v5.4/v5_model.pt")
    python = tmp_path / "py" / "dphuanjing" / "python.exe"
    monkeypatch.setattr(gnn_model, "_find_python", lambda: python)
    monkeypatch.setattr(gnn_model, "_resolve_runtime", lambda: (script, ckpt))

    captured = {}

    def fake_run(cmd, cwd, capture_output, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        out = ("成膜预测结果\n成膜概率  : 0.2000\n不确定性  : ±0.0500\n"
               "判定      : 极低概率\n").encode("gbk")
        return SimpleNamespace(returncode=0, stdout=out, stderr=b"")

    monkeypatch.setattr(gnn_model.subprocess, "run", fake_run)
    p = gnn_model.GNNFilmPredictor(checkpoint_path=tmp_path / "nope.pt")
    res = p.predict_single("O=Cc1ccccc1", "Nc1ccccc1", mc_samples=10)

    assert [Path(x) for x in captured["cmd"]] == [
        python, script, Path("--ald"), Path("O=Cc1ccccc1"),
        Path("--amine"), Path("Nc1ccccc1"),
        Path("--model"), ckpt, Path("--mc"), Path("10"),
    ]
    assert Path(captured["cwd"]) == script.parent
    assert res["probability"] == pytest.approx(0.2)
    assert res["std"] == pytest.approx(0.05)
    assert res["model"] == "gnn_v5.4"


def test_predict_single_explicit_checkpoint_overrides(monkeypatch, tmp_path):
    """显式传入且存在的 checkpoint 优先于 _resolve_runtime 的解析结果。"""
    script = _mk(tmp_path, "gnn_runtime/predict_pair.py")
    explicit = _mk(tmp_path, "custom/v5.4.pt")
    monkeypatch.setattr(gnn_model, "_find_python",
                        lambda: tmp_path / "py" / "python.exe")
    monkeypatch.setattr(gnn_model, "_resolve_runtime",
                        lambda: (script, tmp_path / "pkg" / "v5_model.pt"))

    captured = {}

    def fake_run(cmd, cwd, capture_output, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0,
                               stdout="成膜概率: 0.9\n不确定性: ±0.01\n".encode(),
                               stderr=b"")

    monkeypatch.setattr(gnn_model.subprocess, "run", fake_run)
    p = gnn_model.GNNFilmPredictor(checkpoint_path=explicit)
    p.predict_single("O=Cc1ccccc1", "Nc1ccccc1")
    assert Path(captured["cmd"][7]) == explicit  # [py, script, --ald, ald, --amine, amine, --model, CKPT, ...]


def test_predict_single_nonzero_exit_raises_with_output(monkeypatch, tmp_path):
    monkeypatch.setattr(gnn_model, "_find_python",
                        lambda: tmp_path / "py" / "python.exe")
    monkeypatch.setattr(gnn_model, "_resolve_runtime",
                        lambda: (_mk(tmp_path, "gnn_runtime/predict_pair.py"),
                                 _mk(tmp_path, "pkg/v5.pt")))

    def fake_run(cmd, cwd, capture_output, timeout):
        return SimpleNamespace(returncode=1, stdout="错误: 模型文件不存在\n".encode(),
                               stderr="boom".encode())

    monkeypatch.setattr(gnn_model.subprocess, "run", fake_run)
    p = gnn_model.GNNFilmPredictor(checkpoint_path=tmp_path / "nope.pt")
    with pytest.raises(RuntimeError, match="GNN 预测失败"):
        p.predict_single("O=Cc1ccccc1", "Nc1ccccc1")
