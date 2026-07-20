"""App 入口 RDKit 日志静默的回归测试。

在子进程中导入 app/gradio_app.py 并解析非法 SMILES，断言 stderr 行为：
- 默认：RDKit Parse Error 告警被静默（不刷屏）；
- 调试入口（COF_RDKIT_DEBUG=1，start_app.bat 已设置）：告警恢复；
- 两种模式下解析行为一致（非法 SMILES 都返回 None，预测逻辑不受影响）。

子进程用当前 pytest 解释器（base 环境含 gradio/rdkit），导入 gradio_app
约需数秒，两个用例各一次子进程调用。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SNIPPET = (
    "import sys; sys.path.insert(0, 'app');"
    "import gradio_app;"
    "from rdkit import Chem;"
    "m1 = Chem.MolFromSmiles('not_a_valid_smiles');"
    "m2 = Chem.MolFromSmiles('Nc1ccc(N)cc1');"
    "assert m1 is None and m2 is not None, '解析行为被改变';"
    "print('PARSE_OK')"
)


def _run_app_subprocess(extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("COF_RDKIT_DEBUG", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", _SNIPPET],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )


def test_rdkit_parse_errors_suppressed_by_default():
    r = _run_app_subprocess()
    assert r.returncode == 0, f"子进程失败：{r.stderr[-500:]}"
    assert "PARSE_OK" in r.stdout  # 解析/预测逻辑不受影响
    assert "Parse Error" not in r.stderr


def test_rdkit_parse_errors_restored_with_debug_env():
    r = _run_app_subprocess({"COF_RDKIT_DEBUG": "1"})
    assert r.returncode == 0, f"子进程失败：{r.stderr[-500:]}"
    assert "PARSE_OK" in r.stdout
    assert "Parse Error" in r.stderr  # 调试模式恢复 RDKit 告警


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
