# COF 成膜推荐系统

> 输入两组单体（醛 / 胺）的 SMILES 或 CAS 号，预测 COF 成膜概率，输出打分理由、化学结构图与实验方案建议。面向课题组实验人员的桌面应用。

- **最新版本**：**v0.2.0** → 📥 [GitHub Release 下载页](https://github.com/Ckx-z/kimi-tianxuan-seek-APP/releases/latest)
- **分发形态**：Windows NSIS 安装包，开箱即用，无需安装 Python；支持自动更新（v0.2.0 起）
- **使用文档**：📖 [docs/用户手册.md](docs/用户手册.md)（实验人员）｜📦 [docs/分发说明.md](docs/分发说明.md)（负责转发的同学）
- **核心能力**：成膜概率预测（tree 模型内置必可用，GNN 可选增强自动降级）+ 打分理由 + OOD 三级判定 + 批量排序 + 收藏夹 + 实验记录（草稿/时间线/附件）+ LLM 实验方案卡 + GraphRAG 方案迭代

---

## 同学快速上手（5 分钟）

> 面向第一次拿到本软件的课题组成员，照着做即可。

**第 1 步：下载安装（1 分钟）**

1. 打开 [Release 下载页](https://github.com/Ckx-z/kimi-tianxuan-seek-APP/releases/latest)
2. 下载 `COF成膜推荐系统-Setup-x.x.x-win-x64.zip`（约 356MB），解压得到安装程序
3. 双击安装：可自定义安装目录，完成后桌面出现「COF成膜推荐系统」图标

**第 2 步：首次启动（1 分钟）**

- 双击图标，首次启动约 5–8 秒（模型加载中），属正常现象
- 若杀毒软件提示，选择"允许"；建议把安装目录加入杀软白名单
- 打开后先到「设置」页看一眼底部"运行能力自检"：`tree 模型: 可用` 即核心功能正常；`GNN: 不可用` **不是故障**（可选增强，自动降级）

**第 3 步：配置 LLM（可选，2 分钟）**

> LLM 用于：单体性质中文解读（含毒性安全提示）、实验方案卡生成、方案迭代问答。**不配也能用全部核心功能**（打分、排序、收藏、记录）。

1. 准备一个 OpenAI 兼容的 API（课题组提供的 MiniMax / LongCat，或你自己的 OpenAI 等）
2. 应用内「设置」页 → 填入三项：
   - `Base URL`：如 `https://api.longcat.chat/openai`（MiniMax 则填对应地址）
   - `API Key`：你的密钥
   - `模型名`：如 `LongCat-2.0`
3. 点「测试连接」显示成功即完成。配置只存在你自己电脑 `%APPDATA%` 下，不会外泄

**第 4 步：开始用（1 分钟）**

1. 「查询打分」页输入醛、胺单体的 SMILES 或 CAS 号 → 得出膜概率、打分理由、结构图
2. 点「收藏」保存这组单体（已收藏会显示状态，不会重复收藏）
3. 做完实验到「实验记录」页回填结果：未完成可先**存草稿**；可写完整实验流程、按时间点记录过程并上传照片
4. 「方案迭代」页用自然语言提问（如"这个组合成膜失败了怎么改"），AI 给出下一步建议，采纳后自动生成编号方案卡

**遇到问题**：先看 [docs/用户手册.md](docs/用户手册.md) 的 FAQ（打不开、GNN 不可用、数据在哪等）；解决不了联系课题组内分发负责人。

---

## 更新与数据说明

- **自动更新**：v0.2.0 起，软件启动时自动检查新版本，弹窗确认后自动下载安装，无需手动卸载重装
- **手动更新**：直接下载新安装包覆盖安装即可，不用先卸载
- **数据安全**：收藏、实验记录、草稿、附件、LLM 配置全部存于本机 `%APPDATA%\COF-Film-Recommend\`，卸载/覆盖安装/自动更新**都不会清除**；换电脑时拷贝该文件夹即可迁移

---

## 安装（普通用户·详细版）

1. 从 [GitHub Release](https://github.com/Ckx-z/kimi-tianxuan-seek-APP/releases/latest) 或课题组内部渠道获取安装包
2. 双击安装，按向导完成；安装后从开始菜单或桌面快捷方式启动
3. **首次启动约 5–8 秒**（模型加载），属正常现象
4. 建议将安装目录加入杀软白名单，避免扫描 `_internal` 目录拖慢首启（详见用户手册 FAQ）
5. 8000 端口被占用**不影响使用**，应用会自动选择可用端口

详细操作步骤请直接阅读 **[docs/用户手册.md](docs/用户手册.md)**。

## 首次使用指引（速览）

1. **配置 LLM（可选）**：LLM 功能（单体性质卡、实验方案卡、方案迭代问答）需自配 API。打开应用内「设置」页填写 base_url / api_key / model，或编辑 `%APPDATA%\COF-Film-Recommend\config\llm_settings.local.json`（模板见 [config/llm_settings.example.json](config/llm_settings.example.json)）。任何 OpenAI 兼容端点均可（MiniMax / LongCat / OpenAI / 本地 vLLM）。不配 LLM 不影响打分、排序、收藏、记录等全部核心功能。
2. **查询打分**：输入醛 / 胺单体的 SMILES 或 CAS 号（也可从内置单体库选择），得到成膜概率、SHAP 中文打分理由、化学结构图与 OOD 标记。
3. **批量排序**：一次提交多组单体组合，按成膜分排序，可导出 CSV。
4. **实验记录**：做完实验后回填实际结果，形成"预测 vs 实际"时间线。
5. **方案迭代**：用自然语言向 GraphRAG 迭代引擎提问（如"这个组合成膜失败了，下一步怎么改"），采纳建议后自动生成带编号的方案卡。

## 数据与隐私

- **用户数据全部存于本机** `%APPDATA%\COF-Film-Recommend\`：收藏夹、实验记录、方案卡、LLM 配置（含密钥），**不随安装包分发、卸载后可选择保留**。
- **随包内置**：tree 模型、GraphRAG 检索底座（`graph.pkl`）、内置单体库。
- **能力分级**：tree 模型必可用；GNN 为可选增强，无对应环境时自动降级为仅 tree 分，界面会标注，**不是故障**。

---

## 研究背景

共价有机框架（COF）能否成膜高度依赖单体组合。本系统用机器学习预测醛-胺单体组合的成膜概率：

- **双模型路由**：以训练单体池（醛 896 / 胺 1366）为路由键——醛胺均未见 → 外推臂 `tree_v4_noTE`（无统计先验，双留出最强）；其余 → 池内臂 `tree_v4`（含单体历史成膜率先验）。
- **主分数口径**：max(树, GNN) 乐观召回口径，带分量溯源；± bagging 不确定度 + OOD 三级标记（⛔ 非标准官能团不出分 / ⚠️ 外推警告）。
- **打分理由**：TreeExplainer SHAP 按模型缓存，特征名→中文映射，分组贡献（醛/胺/交互/规则/3D/先验）。

### 当前模型性能

| 模型 | 角色 | LOGO PR-AUC | 双留出 PR-AUC（3 种子均值） |
|---|---|---|---|
| `tree_v4` | 池内臂（默认） | **0.878** | 0.642 |
| `tree_v4_noTE` | 外推臂（双未见单体） | 0.790 | **0.682** |
| GNN v5.3 | 可选并行增强 | — | PR-AUC 0.784 |

> 评估协议：LOGO = 留醛分组 CV；双留出 = 醛胺均不见于训练折（更严格的外推考核，须报多种子）。
> 频率基线（胺历史成膜率单特征）LOGO 0.864，双留出坍缩至 0.523（证实统计泄漏）。
> 已知局限：双未见单体泛化 0.63–0.68 仍是核心瓶颈；落在训练分布包络外的单体（如大芳香醛）会触发外推警告，请结合 OOD 标记使用分数。

---

## 开发模式（开发者）

仅需要改代码的同学阅读本节；普通用户请忽略。

### 仓库组成

| 模块 | 定位 | 说明 |
|---|---|---|
| `api/` | FastAPI 后端 | 六路由（打分 / 收藏 / 实验记录 / 单体 / 方案卡 / LLM），`uvicorn api.main:app --port 8000` |
| `webapp/` | React 前端 + Electron 桌面壳 | Vite + React + TS + Tailwind；`electron/main.cjs` 为桌面入口；NSIS 打包产物即安装包 |
| `src/` | 共享 Python 后端 | 预测层（双模型路由 + GNN subprocess）、条件推荐、Word 报告、SHAP 归因、分子渲染 |
| `minimax/` | GraphRAG 迭代模块 | 含 `bridge/`（GraphRAG 检索与方案生成）、`adapters/`（数据契约摄入）；经 `data/rag_export/` 契约与主模块对接 |
| `app/gradio_app.py` | 旧版 Gradio 界面 | 保留维护，与 FastAPI 共用 `src/` |

### 启动开发环境

```bash
git clone <repo> && cd 全新机器学习实验

# 1. FastAPI 后端（base 环境）
E:\ANACONDA\python.exe -m pip install -r requirements.txt
E:\ANACONDA\python.exe -m uvicorn api.main:app --port 8000   # 文档: http://127.0.0.1:8000/docs

# 2. React 前端（另开终端）
cd webapp
npm install
npm run dev        # Vite dev server，代理到 8000 端口后端
```

桌面壳调试：`webapp/` 下 `npm run electron` 相关脚本（见 `webapp/package.json`）。

### 三个 Python 环境的分工

| 组件 | 解释器 | 说明 |
|---|---|---|
| FastAPI / 测试 / 主后端 | base（Python 3.13） | 依赖见 `requirements.txt` |
| minimax GraphRAG 运行时 | 独立 Python 3.12 | 依赖见 `minimax/requirements-minimax.txt`，subprocess 调用 |
| GNN 推理 | dphuanjing（Python 3.8，torch 2.3.1 + PyG 2.6.1） | subprocess 调用；缺失时自动降级 |

多解释器原因：GNN 依赖旧环境 torch/PyG（Python 3.8 装不了新版框架），GraphRAG 运行时独立隔离；均通过 subprocess 调用，互不冲突。路径配置见 `config/runtime.example.json`（复制为 `config/runtime.local.json`，已 gitignore）。

### 测试

```bash
E:\ANACONDA\python.exe -m pytest tests/ -v    # 基线 373 项
```

---

## 目录结构

```
全新机器学习实验/
├── api/                     # FastAPI 后端（六路由）
├── webapp/                  # React 前端 + Electron 桌面壳（分发安装包来源）
│   ├── src/pages|sections/  # 页面：查询打分/批量排序/收藏夹/实验记录/方案迭代/设置
│   └── electron/main.cjs    # Electron 主进程
├── app/gradio_app.py        # 旧版 Gradio 界面（保留维护）
├── src/
│   ├── predictor/           # 预测层（树模型路由 + GNN subprocess）
│   ├── condition_recommender/  # 条件推荐（规则 + 案例）
│   ├── report_generator/    # Word 报告生成
│   ├── models/              # 训练管线 + SHAP 归因
│   ├── features/            # 描述符 / target encoding / 指纹
│   └── utils/               # 分子渲染等
├── minimax/                 # GraphRAG 迭代模块（predict/ experiment/ bridge/ adapters/）
├── config/                  # 配置模板（llm_settings / runtime，*.local.json 不入库）
├── scripts/                 # 评估/消融/校准脚本
├── data/                    # 训练数据、中间产物、rag_export 契约目录
├── models/                  # 模型权重（*.pkl 不入库）+ monomer_pool.json
├── docs/                    # 用户手册、分发说明、架构与契约文档
├── PROJECT_STATE.md         # 项目当前状态
├── DECISIONS.md             # 关键决策记录
└── EXPERIMENTS/             # 实验记录（exp_001+）
```

## 技术栈

- **后端**：Python / FastAPI / XGBoost / SHAP / RDKit /（可选）PyTorch + PyG（GNN）
- **前端**：React + TypeScript + Vite + Tailwind CSS
- **桌面**：Electron + NSIS 安装包
- **LLM / RAG**：OpenAI 兼容接口（MiniMax / LongCat 等）+ GraphRAG（networkx 图谱，底座随包分发）
- **数据契约**：App ↔ minimax 经 `data/rag_export/` 对接（见 `minimax/docs/COF_APP_CONTRACT.md`）

## 许可与课题组信息

- 本项目为课题组内部研究与使用软件，**不对外公开分发**；安装包与源码仅限课题组内流转。
- 用户本地数据（含 LLM 密钥）归各使用者所有，不上传、不收集。
- 研究背景与方法细节见 `docs/background.md`、`docs/methodology.md`；架构见 `docs/ARCHITECTURE.md`。
