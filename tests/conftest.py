"""pytest 全局夹具：隔离 DFT 任务落盘路径，避免测试写入开发环境 data/ 目录。

src/dft/jobs.py 的 _persist() 在任何建任务/状态变迁时都会写 dft_jobs.json；
其中「状态变迁」发生在后台工作线程里，可能晚于单个测试结束（fixture 撤销后），
因此本夹具必须是 **session 级**：整个测试会话期间路径保持指向临时目录，
晚完成的工作线程写入的也是临时目录，而不是真实用户数据目录。

生产路径 user_data_root()/dft_jobs.json 仅由真实运行经 FastAPI lifespan 读取
（TestClient 不带 with 时不触发 lifespan）。
"""

import os

import pytest

# 注意（v1.9.8 排查结论）：**不要**在这里（模块导入期）设置 COF_DATA_DIR ——
# 实测会立刻破坏 8 个其他用例（1 failed + 7 errors），因为 gnn_jobs / gnn_feedback /
# dft jobs / p4b 模板等模块在导入时就按 user_data_root() 固化了路径。
# v2.0.1 起：下方 session 夹具设置 COF_DATA_DIR 之后，用 `_refresh_module_level_paths()`
# 把**所有**导入期固化常量（gnn 两处 + literature 六个模块的写盘路径）重算到隔离目录；
# tests/test_test_isolation_redline.py 会守住这条红线（一旦漏掉某常量就失败）。


@pytest.fixture(autouse=True, scope="session")
def _isolate_dft_job_store(tmp_path_factory):
    from src.dft import jobs as dft_jobs
    store = tmp_path_factory.mktemp("dft_job_store") / "dft_jobs.json"
    original = dft_jobs._job_store_path
    dft_jobs._job_store_path = lambda: store
    yield
    dft_jobs._job_store_path = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_literature_library(tmp_path_factory):
    """v1.9.8：把文献库写入隔离到临时目录，**杜绝测试污染随包文献库**。

    背景（事故）：`titles` 的 overlay 仅在「frozen 或显式设置 COF_DATA_DIR」时生效；
    否则 `resolver.append_paper` 写的是**仓库里的 data/paper_titles.json**（随包库）。
    本轮曾因此把 3 篇测试文献冻进 v1.9.8 安装包（已还原 + 重新冻结），排查还发现
    13 个用例长期在写随包库。此处 session 级设置 COF_DATA_DIR：
      · 写入 → 用户 overlay（临时目录），首次写入自动全量复制内置库；
      · 读取 → 内置库打底 + overlay 覆盖/新增（`titles._load()` 的合并语义）。
    """
    data_dir = tmp_path_factory.mktemp("cof_data_root")
    old = os.environ.get("COF_DATA_DIR")
    os.environ["COF_DATA_DIR"] = str(data_dir)
    # v1.9.8 修 test_routing 偶发：若干模块在**导入时**就把 user_data_root() 固化成了
    # 模块级常量（gnn_jobs.GNN_MODELS_DIR/JOBS_DIR/REGISTRY_PATH、gnn_feedback.FEEDBACK_PATH）。
    # 本夹具在 collection/import **之后**才执行，于是这些常量可能仍指向**真实数据目录** ——
    # "能否找到 GNN 模型/反馈文件"随导入顺序漂移，表现为偶发失败。
    # 这里把已导入模块的常量**显式刷新**到隔离目录，使整场测试路径确定。
    _refresh_module_level_paths()
    yield
    if old is None:
        os.environ.pop("COF_DATA_DIR", None)
    else:
        os.environ["COF_DATA_DIR"] = old


def _refresh_module_level_paths() -> None:
    """把导入期固化的数据路径常量重算到当前 COF_DATA_DIR（失败即忽略）。

    v2.0.1 扩容（事故驱动）：此前只覆盖 gnn 两处，于是 `literature.resolver.INTAKE_PATH`
    等**导入期常量**仍指向真实数据目录 —— 跑一次 `test_literature_autobackfill_v197.py`
    就会往仓库 `data/literature/literature_intake.jsonl` 追加审计行、并把占位文献
    mirror 进真实 `library.db`（实测真实库被覆盖了 paper 1 / paper 2 的标题，
    审计流水累积 335 行全是测试垃圾）。本函数现在覆盖文献链路所有写盘常量。
    """
    import sys

    try:
        from src import runtime_config
    except ImportError:  # pragma: no cover
        return
    root = runtime_config.user_data_root()
    lit = root / "literature"
    targets = {
        "src.predictor.gnn_jobs": {
            "GNN_MODELS_DIR": root / "gnn_models",
            "JOBS_DIR": root / "gnn_jobs",
        },
        "src.predictor.gnn_feedback": {
            "FEEDBACK_PATH": root / "feedback" / "gnn_feedback.jsonl",
        },
    }
    # 文献链路：这些常量都写成 user_data_root()/... ，必须逐个重算
    lit_targets = {
        "resolver": {"INTAKE_PATH": lit / "literature_intake.jsonl"},
        "figures": {"FIGURES_DIR": lit / "figures",
                    "INDEX_PATH": lit / "figures_index.json"},
        "pdf_figures": {"STAGING_DIR": lit / "figure_staging"},
        "knowledge": {"ENTRIES_PATH": lit / "knowledge_entries.jsonl"},
        "attachments": {"PDFS_DIR": lit / "pdfs",
                        "INDEX_PATH": lit / "pdfs_index.jsonl"},
        "embedding": {"EMB_PATH": root / "graphrag_user" / "literature_emb.jsonl"},
    }
    for base in ("", "src."):
        for short, attrs in lit_targets.items():
            targets[f"{base}literature.{short}"] = attrs
    for mod_name, attrs in targets.items():
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr, value in attrs.items():
            try:
                setattr(mod, attr, value)
            except Exception:  # pragma: no cover
                pass
        if mod_name.endswith("gnn_jobs") and hasattr(mod, "GNN_MODELS_DIR"):
            try:  # REGISTRY_PATH 派生自 GNN_MODELS_DIR
                mod.REGISTRY_PATH = mod.GNN_MODELS_DIR / "registry.json"
            except Exception:  # pragma: no cover
                pass


@pytest.fixture(autouse=True, scope="session")
def _no_docker_autostart():
    """测试会话禁用 CREST Docker 探测的 Docker Desktop 自动拉起
    （docker_engine_ready 检测到引擎未运行时会尝试启动 Docker Desktop，
    测试环境不允许这种副作用）。"""
    old = os.environ.get("COF_CREST_DOCKER_AUTOSTART")
    os.environ["COF_CREST_DOCKER_AUTOSTART"] = "0"
    yield
    if old is None:
        os.environ.pop("COF_CREST_DOCKER_AUTOSTART", None)
    else:
        os.environ["COF_CREST_DOCKER_AUTOSTART"] = old
