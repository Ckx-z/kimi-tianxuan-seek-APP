"""预测层：GNN 模型封装（旧项目 v5.3）。

策略：通过 subprocess 调用旧项目 predict_pair.py，避免新旧项目 src 包名冲突。
这样新工作台无需安装 torch/torch_geometric，也绝不修改旧项目代码。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

OLD_PROJECT_ROOT = Path(r"C:\Users\ckx\Desktop\tianxuan seek")
DEFAULT_CHECKPOINT = OLD_PROJECT_ROOT / "models" / "v5.3" / "v5_model.pt"
DPHUANJING_PYTHON = Path(r"E:\ANACONDA\envs\dphuanjing\python.exe")


def _find_python() -> Path:
    """优先使用 dphuanjing 环境 Python，否则尝试系统 Python。"""
    if DPHUANJING_PYTHON.exists():
        return DPHUANJING_PYTHON
    # fallback：尝试旧项目环境里的 python（如果配置不同）
    return Path("python")


class GNNFilmPredictor:
    """基于旧项目 GNN 的 COF 成膜概率预测器（subprocess 封装）。"""

    def __init__(self, checkpoint_path: str | Path | None = None):
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else DEFAULT_CHECKPOINT

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
        """预测单个单体对，返回概率 + 不确定性。"""
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"找不到 GNN 模型：{self.checkpoint_path}")

        python = _find_python()
        cmd = [
            str(python),
            str(OLD_PROJECT_ROOT / "predict_pair.py"),
            "--ald", ald_smiles,
            "--amine", amine_smiles,
            "--model", str(self.checkpoint_path),
            "--mc", str(mc_samples),
        ]

        # 在旧项目目录下运行，确保相对路径和 import 正确
        result = subprocess.run(
            cmd,
            cwd=OLD_PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"GNN 预测失败（returncode={result.returncode}）。\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        prob, std = self._parse_probability(result.stdout)
        return {
            "probability": prob,
            "std": std,
            "model": "gnn_v5.3",
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
