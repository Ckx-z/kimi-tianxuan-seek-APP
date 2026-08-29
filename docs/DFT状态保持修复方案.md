# DFT 计算页状态保持修复方案（最终版）

> 问题：DFT 计算耗时较长，用户切换页面（结果查看页 / 用户中心等）后再返回，
> 表单填写内容与计算进度全部丢失。本方案修复该问题。
> 本方案由 DeepSeek 协助完成。
> 实施日期：2026-08-29。

---

## 一、问题诊断（根因，基于代码实测）

| # | 根因 | 证据 |
|---|---|---|
| 1 | 前端表单/任务状态全部是组件内存态，路由切换即卸载销毁 | `webapp/src/pages/Dft.tsx`：`mode/monoA/monoB/xType/currentJobId/running/result` 等全部 `useState`；卸载 cleanup 仅 `clearInterval`，无持久化 |
| 2 | `job_id` 丢失导致无法恢复轮询 | `currentJobId` 为 `useState`，卸载即丢；计算线程实为后端 `daemon` 后台线程，任务本身仍在跑 |
| 3 | 后端任务注册表纯内存 | `src/dft/jobs.py` `_JOBS = {}`；服务重启后任务不保留 |
| 4 | API 响应不含输入参数 | `api/routers/dft.py` `_public_job()` 未透出 `ald_smiles/x_type/solvent_id` 等（job 字典内已有） |
| 5 | 历史仅记完成态 | `dft_log.jsonl` 只在 done/failed 时写入，进行中任务不可从历史找回 |

## 二、方案对比与结论

| 方案 | 要点 | 评价 |
|---|---|---|
| A 前端 localStorage | 表单+job_id 存浏览器 localStorage | 打包版有坑：端口自动选择（18765 起，被占则随机），origin 变化则 localStorage 读不回。否决 |
| B 后端任务落盘 | `_JOBS` 落盘 `dft_jobs.json`，启动恢复，`_public_job` 透出 input | 可靠，纯本机零负担，对分发版所有用户适用 |
| C 混合（最终采用） | B + 后端草稿接口 `PUT/GET /api/dft/draft`（替代 localStorage 存表单草稿） | **推荐**：草稿与任务状态都跟随 `user_data_root()`（`%APPDATA%\COF-Film-Recommend\data`），与端口/浏览器无关；无服务器、无网络、单用户私有 |

> 关键澄清：「后端」= 安装包自带的本机 `cof-backend.exe`，**不是服务器**；落盘 =
> 写本机用户目录下的小 JSON 文件（几十 KB 级）。对下载用户同样生效，各用户数据
> 互相隔离（同 `%APPDATA%` 机制，与收藏夹/实验记录一致）。

## 三、实施内容（已落地）

### 后端

1. `src/dft/jobs.py`：
   - `_job_store_path()` = `user_data_root()/dft_jobs.json`（惰性解析，测试可隔离）；
   - `_persist()`：状态变化时快照落盘（独立持久化锁，tmp+replace 原子写，失败静默）；
   - `load_persisted_jobs()`：启动恢复；遗留 `pending/running` 标为 `interrupted`（附中文原因）；
   - 调用点：建任务（含缓存命中）、`running/done/failed` 状态变迁、任务淘汰后。
2. `api/routers/dft.py`：
   - `_public_job()` 透出 `input`（ald_smiles/amine_smiles/x_type/solvent_id/ald2/amine2/custom/n_samples）；
   - 新增 `GET /api/dft/draft`（读草稿）与 `PUT /api/dft/draft`（存草稿，原样存取，前端定义结构）。
3. `api/main.py`：FastAPI lifespan 启动时调用 `load_persisted_jobs()`。
4. `api/schemas.py`：新增 `DftDraftPut` 请求模型。

### 前端

1. `webapp/src/components/dft/api.ts`：`DftJobStatus` 增 `interrupted`；`DftJob` 增 `input`；
   新增 `DftDraft` 类型与 `fetchDftDraft/saveDftDraft`。
2. `webapp/src/pages/Dft.tsx`：
   - 挂载时（无 URL 预填意图时）`GET /draft` 恢复表单 + `currentJobId`，按任务状态续查/续轮询/展示结果；
   - 表单/任务变化防抖 500ms 自动 `PUT /draft`；提交后**立即**保存（含 job_id，防切页竞态）；
   - `interrupted` 状态处理：提示「任务已中断（服务重启），参数已恢复，可重新提交」；
   - 溶剂默认值改为 `prev || list[0].id`，避免覆盖恢复的草稿溶剂。

## 四、测试与验证

- `tests/conftest.py`：autouse 全局隔离 `_job_store_path`（防测试污染开发数据目录）。
- `tests/test_dft_persistence.py`：落盘写入 / 重启恢复（done 保留结果）/ running→interrupted /
  `input` 透出 / 草稿端点往返与空读。
- 手动验收：填参→提交→切页→返回（表单+进度恢复）；F5 刷新恢复；后端重启→interrupted 提示。
- 前端构建验证：`tsc -b` + `vite build`。

## 五、边界与后续

- 服务重启后 done 任务恢复且保留结果（供导出输入文件）；interrupted 任务参数保留可一键重提。
- 可选后续：任务列表接口、SSE 实时进度、取消任务按钮（见当日会话分析）。
