"""pytest 全局夹具：隔离 DFT 任务落盘路径，避免测试写入开发环境 data/ 目录。

src/dft/jobs.py 的 _persist() 在任何建任务/状态变迁时都会写 dft_jobs.json；
其中「状态变迁」发生在后台工作线程里，可能晚于单个测试结束（fixture 撤销后），
因此本夹具必须是 **session 级**：整个测试会话期间路径保持指向临时目录，
晚完成的工作线程写入的也是临时目录，而不是真实用户数据目录。

生产路径 user_data_root()/dft_jobs.json 仅由真实运行经 FastAPI lifespan 读取
（TestClient 不带 with 时不触发 lifespan）。
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_dft_job_store(tmp_path_factory):
    from src.dft import jobs as dft_jobs
    store = tmp_path_factory.mktemp("dft_job_store") / "dft_jobs.json"
    original = dft_jobs._job_store_path
    dft_jobs._job_store_path = lambda: store
    yield
    dft_jobs._job_store_path = original
