"""运行时环境配置：外部 Python 解释器与可选能力的统一解析（分发友好）。

目标：项目拷贝到他人机器后，没有 E:\\python3.12、E:\\ANACONDA\\envs\\dphuanjing
这类硬编码路径也能跑；缺失能力优雅降级（返回"不可用"原因），而非崩溃。

解析顺序（高 → 低）：
1. 环境变量（COF_GRAPHRAG_PYTHON / COF_GNN_PYTHON / COF_APP_PYTHONW /
   COF_GNN_PROJECT_ROOT）
2. 项目根 config/runtime.local.json（gitignored，本机覆盖用；
   模板见 config/runtime.example.json）
3. 自动探测：开发机历史路径存在则用；否则 shutil.which("python") 等
4. 返回 None —— 调用方据此标记该能力不可用并优雅降级

注意：本模块只依赖标准库，且不做任何重活（不 import torch/networkx 之外的
包、不加载模型），可被 API 进程、启动脚本、GNN 封装等各处安全 import。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包产物内。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根（models/、webapp/dist、minimax/、内置 data 文件）。

    frozen（onedir）时 PyInstaller 把模块与 datas 放进 sys._MEIPASS
    （即 exe 旁 _internal 目录），各模块以 __file__ 上溯得到的项目根
    同样落在 _MEIPASS，二者一致；源码运行时为本文件上溯的项目根。
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]


def user_app_root() -> Path:
    """可写用户数据的应用根（frozen 时为 %APPDATA%/COF-Film-Recommend）。

    环境变量 COF_DATA_DIR 可显式覆盖（此时即视为应用根本身）。
    源码运行时等同于项目根（历史行为：数据直接写项目目录）。
    """
    env = os.environ.get("COF_DATA_DIR", "").strip()
    if env:
        return Path(env)
    if is_frozen():
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "COF-Film-Recommend"
    return Path(__file__).resolve().parents[1]


def user_data_root() -> Path:
    """可写用户数据目录（favorites/records/suggestions/缓存等的父目录）。"""
    return user_app_root() / "data"


PROJECT_ROOT = resource_root()
# frozen 时优先读用户数据目录里的本机覆盖配置（包内只读，用户改不了），
# 其次退回包内 config/（一般只有 example，不存在时 load_local_config 返回 {}）
_user_cfg = user_app_root() / "config" / "runtime.local.json"
LOCAL_CONFIG_PATH = _user_cfg if (is_frozen() and _user_cfg.exists()) \
    else PROJECT_ROOT / "config" / "runtime.local.json"

# 开发机历史硬编码路径（探测用，不作为分发前提）
LEGACY_PATHS = {
    "graphrag": [r"E:\python3.12\python.exe"],
    "gnn": [r"E:\ANACONDA\envs\dphuanjing\python.exe"],
    "app": [r"E:\ANACONDA\pythonw.exe", r"E:\ANACONDA\python.exe"],
}
LEGACY_GNN_PROJECT_ROOT = Path(r"C:\Users\ckx\Desktop\tianxuan seek")

ENV_VARS = {
    "graphrag": "COF_GRAPHRAG_PYTHON",
    "gnn": "COF_GNN_PYTHON",
    "app": "COF_APP_PYTHONW",
}

_LOCAL_CACHE: dict | None = None


def load_local_config(refresh: bool = False) -> dict:
    """读 config/runtime.local.json；不存在或损坏返回 {}。"""
    global _LOCAL_CACHE
    if _LOCAL_CACHE is not None and not refresh:
        return _LOCAL_CACHE
    cfg: dict = {}
    try:
        if LOCAL_CONFIG_PATH.exists():
            data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = data
    except Exception:
        cfg = {}
    _LOCAL_CACHE = cfg
    return cfg


def _configured_path(capability: str) -> Path | None:
    """环境变量 > runtime.local.json 的显式配置（不做存在性检查）。"""
    env_var = ENV_VARS[capability]
    val = os.environ.get(env_var, "").strip()
    if val:
        return Path(val)
    pys = load_local_config().get("pythons")
    if isinstance(pys, dict):
        val = str(pys.get(capability) or "").strip()
        if val:
            return Path(val)
    return None


def resolve_python(capability: str,
                   probe_names: tuple[str, ...] = ("python",)) -> Path | None:
    """解析某能力的外部 Python 解释器；找不到返回 None（能力不可用）。

    capability ∈ {"graphrag", "gnn", "app"}。显式配置（环境变量/本地配置）
    优先但要求路径存在；随后依次尝试开发机历史路径与 PATH 探测。
    """
    explicit = _configured_path(capability)
    if explicit is not None:
        return explicit if explicit.exists() else None

    for legacy in LEGACY_PATHS.get(capability, []):
        p = Path(legacy)
        if p.exists():
            return p

    for name in probe_names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def graphrag_python() -> Path | None:
    """页⑤ 迭代编排器（GraphRAG 链路）的外部解释器；None 表示未配置。"""
    return resolve_python("graphrag")


def gnn_python() -> Path | None:
    """GNN 推理环境（py3.8 + torch + PyG）解释器；None 表示 GNN 不可用。"""
    # GNN 依赖专用环境，探测系统 python 无意义（多半没装 torch_geometric），
    # 故不做 PATH 探测，避免误用导致 subprocess 报一堆 import 错。
    return resolve_python("gnn", probe_names=())


def app_pythonw() -> Path | None:
    """桌面启动器用的 pythonw/python（Gradio App 进程解释器）。"""
    return resolve_python("app", probe_names=("pythonw", "python"))


def gnn_project_root() -> Path:
    """旧 GNN 项目根（predict_pair.py 所在目录）。

    环境变量 COF_GNN_PROJECT_ROOT > runtime.local.json 的 gnn_project_root
    > 开发机历史路径。不保证存在，调用方需自行 exists() 判断。
    """
    val = os.environ.get("COF_GNN_PROJECT_ROOT", "").strip()
    if val:
        return Path(val)
    val = str(load_local_config().get("gnn_project_root") or "").strip()
    if val:
        return Path(val)
    return LEGACY_GNN_PROJECT_ROOT


def graphrag_inprocess_available() -> tuple[bool, str]:
    """主进程内直接 import GraphRAG 链路是否可行（轻量检查，不加载图）。

    返回 (可用, 原因)。判定：编排器脚本存在 + graph.pkl 就位 + networkx 可
    import（缺 networkx 时编排器自身会降级跳过图检索，仍属"可用但降级"，
    故 networkx 缺失只在原因里注明，不判否）。
    """
    script = PROJECT_ROOT / "minimax" / "adapters" / "iterate_suggest.py"
    if not script.exists():
        return False, f"编排器脚本缺失: {script}"
    graph = PROJECT_ROOT / "minimax" / "bridge" / "graphrag" / "graph.pkl"
    if not graph.exists():
        return False, "GraphRAG 图资产 minimax/bridge/graphrag/graph.pkl 缺失"
    try:
        import networkx  # noqa: F401
    except Exception:
        return True, "可用（缺 networkx，图检索将自动降级跳过）"
    return True, "ok"


def capability_status() -> dict:
    """各能力可用性汇总，供 /api/.../env-status 与设置页展示。"""
    status: dict = {}

    models = PROJECT_ROOT / "models"
    tree_ok = (models / "tree_v4.pkl").exists() and \
        (models / "tree_v4_noTE.pkl").exists() and \
        (models / "monomer_pool.json").exists()
    if not tree_ok:
        # 路由资产不全时 FilmPredictor 会回退单模型 tree_v3
        tree_ok = (models / "tree_v3.pkl").exists()
    status["tree"] = "ok" if tree_ok else "disabled: 树模型资产缺失（models/ 下无 tree_v4/tree_v3）"

    gp = gnn_python()
    groot = gnn_project_root()
    if gp is None:
        status["gnn"] = "disabled: 未找到 GNN 推理环境（dphuanjing）；可设环境变量 COF_GNN_PYTHON 或 config/runtime.local.json"
    elif not groot.exists():
        status["gnn"] = f"disabled: GNN 旧项目目录不存在: {groot}"
    else:
        status["gnn"] = "ok"

    ep = graphrag_python()
    ip_ok, ip_reason = graphrag_inprocess_available()
    if ep is not None:
        status["graphrag"] = f"ok（外部解释器: {ep}）"
    elif ip_ok:
        status["graphrag"] = "ok（主进程内 import，未配置外部解释器）"
    else:
        status["graphrag"] = f"disabled: 未找到 python3.12 解释器且主进程内不可用（{ip_reason}）"
    return status
