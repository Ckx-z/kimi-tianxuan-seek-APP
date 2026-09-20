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
    yield
    if old is None:
        os.environ.pop("COF_DATA_DIR", None)
    else:
        os.environ["COF_DATA_DIR"] = old


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
