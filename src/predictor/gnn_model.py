"""预测层：GNN 模型封装（旧项目 v5.3）。

策略：通过 subprocess 调用旧项目 predict_pair.py，避免新旧项目 src 包名冲突。
这样新工作台无需安装 torch/torch_geometric，也绝不修改旧项目代码。

分发适配：旧项目根与 dphuanjing 解释器路径不再硬编码为唯一来源，
统一走 src/runtime_config（环境变量 > config/runtime.local.json >
探测 > 不可用）。环境缺失时抛出带明确原因的 RuntimeError，由
FilmPredictor 捕获后记 gnn_error 并继续 tree 预测（优雅降级）。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # src/ 直接上 sys.path 的兜底
    import runtime_config  # type: ignore

OLD_PROJECT_ROOT = runtime_config.gnn_project_root()
# 包内 GNN 运行时（v1.6.1 阶段三：随安装包分发，见 gnn_runtime/ 与
# models/gnn_v5.4/；PyInstaller spec 已带 datas）
BUNDLED_RUNTIME_DIR = runtime_config.resource_root() / "gnn_runtime"
BUNDLED_SCRIPT = BUNDLED_RUNTIME_DIR / "predict_pair.py"
BUNDLED_CHECKPOINT = runtime_config.resource_root() / "models" / "gnn_v5.4" / "v5_model.pt"
# 旧项目回退（开发机）：老项目目录里的 v5.4 模型与脚本
LEGACY_CHECKPOINT = OLD_PROJECT_ROOT / "models" / "v5.4" / "v5_model.pt"
LEGACY_SCRIPT = OLD_PROJECT_ROOT / "predict_pair.py"
DEFAULT_CHECKPOINT = BUNDLED_CHECKPOINT if BUNDLED_CHECKPOINT.exists() \
    else LEGACY_CHECKPOINT
# 模块级常量保留以便排查/测试 monkeypatch；None 表示 GNN 环境不可用
DPHUANJING_PYTHON = runtime_config.gnn_python()

# v1.8.0：反馈微调版本 registry（激活版本优先于基础 v5.4）
try:
    from src.predictor import gnn_jobs as _gnn_jobs
except ImportError:  # pragma: no cover
    from predictor import gnn_jobs as _gnn_jobs  # type: ignore


def _env_checkpoint() -> Path | None:
    """COF_GNN_CHECKPOINT 环境变量钩子（验证闸门/测试用，最优先级）。"""
    val = os.environ.get("COF_GNN_CHECKPOINT", "").strip()
    if val:
        p = Path(val)
        if p.is_file():
            return p
    return None


def _active_retrained_checkpoint() -> Path | None:
    """反馈微调 registry 激活版本（非 base）的 checkpoint；无/缺失返回 None。"""
    try:
        ckpt = _gnn_jobs.active_checkpoint()
        return ckpt if ckpt is not None and ckpt.is_file() else None
    except Exception:
        return None


def _active_model_name() -> str:
    """当前 GNN 版本名（透出给 predict 响应 gnn_model_version）。"""
    try:
        ver = _gnn_jobs.active_version()
        if ver and ver != "gnn_v5.4" and _active_retrained_checkpoint() is not None:
            return ver
    except Exception:
        pass
    return "gnn_v5.4"


def _resolve_runtime() -> tuple[Path | None, Path | None]:
    """解析 GNN 推理运行时 (predict_pair 脚本, checkpoint)。

    优先级：COF_GNN_CHECKPOINT 环境变量（闸门/测试）→ 反馈微调 registry
    激活版本（用户目录）→ 包内 gnn_runtime + 包内模型（随包分发）→
    包内脚本 + 旧项目模型（开发机）→ 旧项目脚本 + 旧项目模型。
    都不可用返回 (None, None)，由调用方降级。
    """
    env = _env_checkpoint()
    if env is not None:
        script = BUNDLED_SCRIPT if BUNDLED_SCRIPT.is_file() else LEGACY_SCRIPT
        return (script, env) if script.is_file() else (None, None)
    active = _active_retrained_checkpoint()
    if active is not None:
        script = BUNDLED_SCRIPT if BUNDLED_SCRIPT.is_file() else LEGACY_SCRIPT
        if script.is_file():
            return script, active
    if BUNDLED_SCRIPT.is_file():
        ckpt = BUNDLED_CHECKPOINT if BUNDLED_CHECKPOINT.exists() \
            else LEGACY_CHECKPOINT
        if ckpt.exists():
            return BUNDLED_SCRIPT, ckpt
    if LEGACY_SCRIPT.is_file() and LEGACY_CHECKPOINT.exists():
        return LEGACY_SCRIPT, LEGACY_CHECKPOINT
    return None, None


def _find_python() -> Path | None:
    """解析 GNN 推理解释器；环境不存在时返回 None（由调用方降级）。"""
    if DPHUANJING_PYTHON is not None and DPHUANJING_PYTHON.exists():
        return DPHUANJING_PYTHON
    return None


def _decode_output(raw: bytes) -> str:
    """解码子进程输出，兼容 UTF-8 / GBK。

    旧项目 dphuanjing 环境（Windows Python 3.8）按 GBK 打印中文，
    若按 UTF-8 强制解码会得到乱码，导致概率正则解析失败。
    """
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class GNNFilmPredictor:
    """基于旧项目 GNN 的 COF 成膜概率预测器（subprocess 封装）。"""

    def __init__(self, checkpoint_path: str | Path | None = None):
        # v1.8.0：只有「显式传入」的路径才算覆盖；缺省时不落 DEFAULT_CHECKPOINT，
        # 让 predict_single 走动态解析（registry 激活版本 / env 钩子 / 包内 /
        # 旧项目），否则包内 v5.4 恒存在会把 registry 解析结果覆盖回去。
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None

    def _parse_probability(self, output: str) -> tuple[float, float]:
        """从 predict_pair.py 的输出中解析成膜概率和不确定性。"""
        prob_match = re.search(r"成膜概率\s*[:：]\s*([0-9.]+)", output)
        std_match = re.search(r"不确定性\s*[:：]\s*±\s*([0-9.]+)", output)
        if not prob_match:
            raise ValueError(f"无法从 GNN 输出中解析概率。输出：\n{output}")
        prob = float(prob_match.group(1))
        std = float(std_match.group(1)) if std_match else 0.0
        return prob, std

    def predict_single(self, ald_smiles: str, amine_smiles: str, mc_samples: int = 10) -> dict:
        """预测单个单体对，返回概率 + 不确定性。

        GNN 环境（dphuanjing 解释器）或运行时（包内 gnn_runtime / 旧项目）
        缺失时抛出带明确原因的 RuntimeError —— FilmPredictor 会捕获并
        降级为仅 tree 预测。
        """
        python = _find_python()
        if python is None:
            raise RuntimeError(
                "GNN 不可用：未找到 dphuanjing 推理环境"
                f"（配置值: {DPHUANJING_PYTHON}）。可通过环境变量 "
                "COF_GNN_PYTHON 或 config/runtime.local.json 指定；"
                "未配置时 GNN 分量自动降级，不影响树模型预测。")
        script, checkpoint = _resolve_runtime()
        if script is None or checkpoint is None:
            raise RuntimeError(
                "GNN 不可用：未找到 GNN 推理运行时与模型"
                f"（包内 gnn_runtime/ + models/gnn_v5.4/ 或旧项目目录 "
                f"{OLD_PROJECT_ROOT}）。GNN 分量自动降级，不影响树模型预测。")
        if self.checkpoint_path is not None and self.checkpoint_path.exists():
            checkpoint = self.checkpoint_path  # 显式传入的 checkpoint 优先

        cmd = [
            str(python),
            str(script),
            "--ald", ald_smiles,
            "--amine", amine_smiles,
            "--model", str(checkpoint),
            "--mc", str(mc_samples),
        ]

        # 在运行时脚本目录下运行，确保相对路径和 import 正确
        # 捕获字节流后手动解码（UTF-8 优先，GBK 回退）
        result = subprocess.run(
            cmd,
            cwd=str(script.parent),
            capture_output=True,
            timeout=120,
        )
        stdout = _decode_output(result.stdout)
        stderr = _decode_output(result.stderr)

        if result.returncode != 0:
            raise RuntimeError(
                f"GNN 预测失败（returncode={result.returncode}）。\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )

        prob, std = self._parse_probability(stdout)
        return {
            "probability": prob,
            "std": std,
            "model": _active_model_name(),  # v1.8.0：实际激活版本名
        }

    def predict(self, ald_smiles: str, amine_smiles: str) -> float:
        """只返回概率。"""
        return self.predict_single(ald_smiles, amine_smiles)["probability"]


if __name__ == "__main__":
    predictor = GNNFilmPredictor()
    try:
        result = predictor.predict_single(
            "O=CC1=C(C=O)C(=O)C(C=O)=C1O",
            "Nc1ccc(N)cc1",
        )
        print(f"GNN 预测结果：{result}")
    except Exception as e:
        print(f"GNN 预测失败：{e}")
