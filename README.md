# COF 成膜单体组合推荐系统

> 预测能形成膜的共价有机框架（COF）单体组合，输出成膜打分与不确定度、SHAP 理由、化学结构图，设计实验方案，沉淀实验记录并对接 RAG 迭代。

---

## 项目状态

**阶段 22 已收官**（2026-07-22）：页⑤ GraphRAG 迭代引擎全量对接（自然语言提问 → GraphRAG 取证 → LLM 建议写回 → 采纳生成编号方案卡闭环）；阶段 22b 落地 FastAPI 地基（`api/` 六路由，未来独立前端对接层）。基线测试 **332 项全部通过**。

- ✅ **五标签页 App**：① 查询打分（SMILES / CAS 号 / 内置单体库三种输入 + 相似成膜案例 + LLM 单体性质卡）② 批量排序（多组打分排序表 + 导出 CSV）③ 收藏夹（文献自动匹配 + 真实论文标题）④ 实验记录（预测 vs 实际时间线 + 记录管理：删除/放大查看）⑤ 方案迭代（GraphRAG 迭代引擎：建议展示 + 采纳生成编号方案卡）
- ✅ **FastAPI 后端**（`api/`）：打分 / 收藏 / 实验记录 / 单体 / 方案卡 / LLM 六路由，`uvicorn api.main:app --port 8000` 启动，与 Gradio App 共用 `src/` 后端互不影响
- ✅ **打分三件套**：主分数 max(树, GNN) 乐观召回口径（D29，带标注与分量溯源）± bagging 不确定度 + OOD 三级标记（⛔ 非标准官能团不出分 / ⚠️ 外推警告）
- ✅ **双模型路由**（D23）：双未见单体 → `tree_v4_noTE`（外推臂）；其余 → `tree_v4`（池内臂）
- ✅ **SHAP 打分理由**：全中文解释哪个官能团/特征推高或拉低成膜分（热态 ~0.04s）
- ✅ **化学结构图**：醛/胺单体 2D 结构 + 亚胺缩合产物骨架（RDKit 渲染，非法 SMILES 优雅降级）
- ✅ **实验方案卡**：模板系统（内置侯老师法默认模板 + 上传 docx 由 LLM 提取自定义模板）+ 防错清单（来自真实失败教训）；页⑤建议采纳后自动生成带编号的方案卡
- ✅ 生成 Word 实验报告（内嵌单体结构图）
- ✅ 桌面快捷方式 `COF成膜推荐.lnk`（定制图标）/ `启动COF推荐.vbs` 静默启动，双击=重启到最新代码
- ✅ GNN v5.3 通过 subprocess 接入（PR-AUC 0.784）
- ✅ 测试 332 项全部通过
- ✅ **minimax RAG 模块已合并**（`minimax/`，subtree 保留全部提交历史），经 `data/rag_export/` 契约对接；LLM 双端点（MiniMax 主 + longcat 备，密钥仅本地）

### 当前模型一览

| 模型 | 角色 | LOGO PR-AUC | 双留出 PR-AUC（3 种子均值） |
|---|---|---|---|
| `tree_v4` | 池内臂（App 路由默认） | **0.878** | 0.642 |
| `tree_v4_noTE` | 外推臂（双未见单体） | 0.790 | **0.682** |
| `tree_v3` | 存档（前默认模型） | 0.761 | 0.653 |
| `tree_v5`（指纹） | 存档（已证伪，见 exp_006） | 0.871 | 0.637 |
| GNN v5.3 | 并行概率输出 | — | — |

> 评估协议：LOGO = 留醛分组 CV；双留出 = 醛胺均不见于训练折（更严格的外推考核，须报多种子）。
> 频率基线（胺历史成膜率单特征）LOGO 0.864，双留出坍缩至 0.523（证实统计泄漏）。

---

## 项目组成

本仓库是两个模块合并后的唯一主线（GitHub: `kimi-tianxuan-seek-APP`）：

| 模块 | 定位 | 说明 |
|---|---|---|
| 主模块（仓库根） | **打分与交付** | Gradio App + FastAPI（`api/`）+ XGBoost 双模型路由 + GNN v5.3 对照，输出成膜概率、打分理由、结构图、Word 报告 |
| `minimax/` | **RAG 迭代** | 实验迭代 RAG 项目（原 `shiyandiedai` 仓库，subtree 合并保留全部历史），含 `predict/`（预测）、`experiment/`（实验）、`bridge/`（GraphRAG v2 检索与方案生成）、`adapters/`（契约摄入适配器） |

两模块经 **`data/rag_export/` 数据契约**对接：App 侧按契约导出预测/反馈数据，minimax 侧 `adapters/cof_app_ingest.py` 摄入，用于 RAG 检索与实验方案迭代。契约文档见 `minimax/docs/COF_APP_CONTRACT.md`。

---

## 目录结构

```
全新机器学习实验/
├── app/gradio_app.py              # Gradio App 入口（概率 + 打分理由 + 结构图）
├── api/                           # FastAPI 后端（六路由，uvicorn 启动，未来独立前端对接层）
├── 启动COF推荐.vbs                # 双击无窗口启动（推荐）
├── 调试启动.bat                   # 调试启动（终端可见日志）
├── silent_launch.py               # 静默启动器（vbs 调用）
├── minimax/                       # 实验迭代 RAG 模块（原 shiyandiedai 仓库）
│   ├── predict/                   # 预测脚本与模型权重
│   ├── experiment/                # 实验记录与迭代
│   ├── bridge/                    # GraphRAG v2 索引/检索/方案生成 + 集成测试
│   ├── adapters/                  # COF App 数据契约摄入适配器
│   ├── docs/                      # 含 COF_APP_CONTRACT 契约文档
│   └── 知识库/                    # 本地文献 PDF（~850MB，不入 git）
├── src/
│   ├── predictor/                 # 预测层（树模型路由 + GNN subprocess）
│   ├── condition_recommender/     # 条件推荐层（规则 + 案例）
│   ├── report_generator/          # Word 报告生成
│   ├── models/                    # 训练管线 + SHAP 归因
│   ├── features/                  # 描述符 / target encoding / 指纹
│   ├── utils/                     # molecule_viz 分子渲染等
│   └── data/                      # 数据导入 + 审计
├── scripts/                       # 评估/消融/校准/补全脚本（stage11/12 系列）
├── data/raw/                      # 训练数据（复制自旧项目）
├── data/interim/                  # 中间产物（条件补全 CSV、特征缓存）
├── data/rag_export/               # App → minimax 数据契约导出目录
├── models/                        # 模型权重（.pkl 不入库）+ monomer_pool.json
├── reports/                       # 实验指标 JSON + 归因报告 + 生成的 docx
├── PROJECT_STATE.md               # 项目当前状态（必读）
├── SESSION_START.md               # 会话启动清单
├── DECISIONS.md                   # 关键决策记录（D1-D27）
├── DATA_DICT.md                   # 数据字典
├── DAILY_LOG/                     # 日报（AI 版 + 人版双轨）
└── EXPERIMENTS/                   # 实验记录（exp_001-exp_012）
```

---

## 运行环境

### 三解释器分工

| 组件 | 解释器 | 说明 |
|---|---|---|
| App 前端 / 测试 / 主后端（FastAPI） | `E:\ANACONDA\python.exe`（base，Python 3.13） | gradio 6.20、fastapi、uvicorn、python-docx、xgboost、shap；基线测试与 `api/` 均用它 |
| minimax GraphRAG 运行时 | `E:\python3.12\python.exe`（Python 3.12） | 页⑤ GraphRAG 迭代引擎（networkx 图谱检索 + LLM 调用），依赖见 `minimax/requirements-minimax.txt` |
| GNN 推理 | `E:\ANACONDA\envs\dphuanjing\python.exe`（Python 3.8） | torch 2.3.1 + PyG 2.6.1，主后端通过 subprocess 调用 |

**为什么用多个解释器？** dphuanjing 是旧项目环境，有 GNN 所需的 torch/PyG，但 Python 3.8 装不了新版 Gradio；minimax GraphRAG 运行时固定在独立的 Python 3.12（networkx 图谱运行时），与主后端隔离。新 App / API 用 base 环境，GNN 与 GraphRAG 均通过 subprocess 调用，互不冲突。

### 环境要求

- 已安装 Anaconda
- base 环境（`E:\ANACONDA\python.exe`）：Python 3.13，依赖见 `requirements.txt`（gradio、fastapi、uvicorn、python-docx、xgboost、rdkit、shap、pandas、joblib 等）
- GraphRAG 环境（`E:\python3.12\python.exe`）：Python 3.12，依赖见 `minimax/requirements-minimax.txt`（networkx、requests、python-docx 等）
- dphuanjing 环境（`E:\ANACONDA\envs\dphuanjing`）：Python 3.8，含 `torch 2.3.1`, `torch_geometric 2.6.1`, `rdkit`

如果缺少依赖：

```bash
# 主后端 / App / 测试（base）
E:\ANACONDA\python.exe -m pip install -r requirements.txt

# minimax GraphRAG 运行时（python3.12）
E:\python3.12\python.exe -m pip install -r minimax/requirements-minimax.txt
```

---

## 快速启动

### 方式 1：双击 `启动COF推荐.vbs`（推荐，无窗口启动）

直接双击 `启动COF推荐.vbs`（或桌面快捷方式 `COF成膜推荐.lnk`，带定制图标）：

- **无任何黑色终端窗口**，App 在后台静默运行
- 自动打开浏览器到 `http://127.0.0.1:7860`
- 若 App 已在运行，双击只会再打开一个浏览器标签页，不会重复启动
- 启动失败（依赖缺失、进程异常退出、超时等）会**弹出错误提示框**，详细日志见 `logs/app_launch.log` 和 `logs/gradio_app.log`

### 方式 2：双击 `调试启动.bat`（调试用）

会打开独立终端窗口运行，可在终端里查看实时日志（含 RDKit 调试信息）；关掉窗口即关闭 App。

### 方式 3：命令行

```bash
cd "C:\Users\ckx\Desktop\全新机器学习实验"
python app/gradio_app.py
```

等待输出：

```
Running on local URL:  http://127.0.0.1:7860
```

浏览器打开：`http://127.0.0.1:7860`

### 启动 API 服务（FastAPI）

```bash
cd "C:\Users\ckx\Desktop\全新机器学习实验"
E:\ANACONDA\python.exe -m uvicorn api.main:app --port 8000
```

- 交互文档：`http://127.0.0.1:8000/docs`
- 与 Gradio App 并存，共用 `src/` 后端与 `data/` 数据，互不影响；是未来 React/Tauri 独立前端的对接层
- 路由：`/api/predict` 打分、`/api/favorites` 收藏、`/api/records` 实验记录、`/api/monomers` 单体、`/api/plan` 方案卡、`/api/llm` LLM

### 打不开怎么办？

1. **静默启动失败**——看弹窗提示与 `logs/gradio_app.log` 末尾的报错；或改用 `调试启动.bat` 在终端里直接看报错
2. **端口被占用**——`启动COF推荐.vbs` 会自动复用已运行的实例；如需换端口，改 `app/gradio_app.py` 最后一行 `server_port=7861`
3. **防火墙拦截**——允许 Python 访问本地网络
4. **首次启动慢**——等 10-30 秒（模型加载期间浏览器可能暂时打不开，稍等刷新即可）

---

## 核心架构

```
用户输入：醛 SMILES + 胺 SMILES
    │
    ├──► [预测层] 树模型双路路由（池内 tree_v4 / 外推 tree_v4_noTE）
    │       + GNN v5.3 并行 → 成膜概率 + 路由原因
    │       + SHAP 打分理由（中文，跟随路由模型）
    │       + 化学结构图（醛/胺/缩合产物骨架）
    │
    └──► [条件推荐层] 规则引擎 + 案例匹配 → 实验条件
              │
              ▼
        [报告生成层] Word 实验报告（内嵌结构图）
```

- **双模型路由**：训练单体池（醛 896 / 胺 1366，`models/monomer_pool.json`）为路由键——醛胺均未见 → 外推臂（无统计先验，双留出最强）；其余 → 池内臂（含单体历史成膜率先验）
- **打分理由**：TreeExplainer 按模型缓存，特征名→中文映射，分组贡献（醛/胺/交互/规则/3D/先验）
- **条件推荐**：基于单体类型（F/CF3/酰肼/常规）和拓扑，用规则 + 历史案例推荐

---

## 测试

```bash
cd "C:\Users\ckx\Desktop\全新机器学习实验"
E:\ANACONDA\python.exe -m pytest tests/ -v
```

当前 **332 项测试全部通过**，覆盖：描述符计算、条件推荐、树模型训练/预测、Word 报告生成、数据导入、SHAP 归因（含 TE 模型）、双模型路由、分子渲染、RDKit 日志行为、CAS 查询、批量排序、收藏夹/实验记录存储、方案卡、主分数口径（D29）、suggestions/文献标题/游离记录、rag_export 契约、LLM 性质卡/方案卡模板、FastAPI 六路由、页⑤ GraphRAG 迭代闭环。minimax 模块自带集成测试见 `minimax/bridge/test_integration.py`（独立运行，12/12）。

---

## 数据与随仓库分发说明

- **用户数据本地独立、不入 git**（D30）：`config/llm_settings.local.json`（LLM 密钥，仅本地）、`data/favorites/`、`data/rag_export/records|suggestions/`、`data/plan_templates/`、`data/generated_plans/`、`data/llm_cache/` 等均为各用户独立实例，已被 `.gitignore` 覆盖；`*.local.json` 为兜底规则。
- **GraphRAG 检索底座随仓库分发**：`minimax/bridge/graphrag/`（图谱 `graph.pkl` / `graph_v2.pkl`、节点/边 jsonl、文献 embedding 等）与 `minimax/bridge/graphrag_v2/` 代码，连同 `minimax/` 其余已追踪文件共约 **75MB**，是页⑤ GraphRAG 迭代引擎的运行底座，**刻意保留在 git 追踪中**（决策：克隆即用，避免每位用户重建图谱）。它们虽命中根 `.gitignore` 的 `*.pkl` 规则，但已被强制追踪（`git add -f`），请勿新增针对它们的忽略规则。
- **不入库的大文件**：`minimax/知识库/`（本地文献 PDF，~850MB）、tianxuan 二进制向量索引（`tianxuan_vectors.bin` 1.7GB 等）、模型权重 `models/*.pkl`——见根 `.gitignore`。

---

## 与旧项目的关系

- **旧项目** `C:\Users\ckx\Desktop\tianxuan seek\`：14G，只读复用
- **本项目**：复制了数据、化学描述符代码，通过 subprocess 调用 GNN
- **绝不修改旧项目任何文件**

---

## 会话协作规范

每次继续项目前，AI 必须先读：
1. `PROJECT_STATE.md` — 当前状态
2. `SESSION_START.md` — 会话启动清单
3. 最新 `DAILY_LOG/YYYY-MM-DD.md`
4. `DECISIONS.md`

每次会话结束必须：
1. 更新 `PROJECT_STATE.md`
2. 写/更新 `DAILY_LOG/YYYY-MM-DD.md`（AI 版 + 人版双轨）
3. 新决策记入 `DECISIONS.md`
4. 实验记入 `EXPERIMENTS/`

---

## 当前主要问题（详见 PROJECT_STATE.md 遗留事项）

1. **双未见单体泛化 0.63–0.68（核心瓶颈，学习型表征已全部证伪）**：Morgan 指纹（exp_006）、GNN 单体/配对 embedding（exp_009/010，闭卷证伪，阶段 A 增益为记忆）均无效；严格双未见下 GNN 端到端 0.6317 < 描述符树 0.6824。剩余方向：外部数据预训练表征 / 配对级手工表征 / fold3 型区域漂移切片与检测（下一优先）
2. **fold3 型区域漂移**：大芳香醛等落在训练分布包络外的单体是难折来源，需专项切片跟踪
3. **案例库丰富**：从 `data/experimental_refs/main_template.docx` 自动提取历史案例
4. **GNN 全接入**：目前通过 subprocess 调用，未来可直接导入（需解决 src 包名冲突）

---

## 关键文件

| 文件 | 说明 |
|---|---|
| `app/gradio_app.py` | Gradio 前端 |
| `api/main.py` | FastAPI 入口（六路由，uvicorn 启动） |
| `src/predictor/__init__.py` | FilmPredictor（双模型路由入口） |
| `src/predictor/routing.py` | 路由规则（routed_strict，D23） |
| `src/predictor/tree_model.py` | XGBoost 树模型（自描述 pkl 加载） |
| `src/predictor/gnn_model.py` | GNN v5.3 subprocess 封装 |
| `src/models/attribution.py` | SHAP 归因 + 中文打分理由 |
| `src/utils/molecule_viz.py` | 分子结构渲染（SMILES→PNG，App/报告复用） |
| `src/condition_recommender/` | 条件推荐规则 + 案例 |
| `src/report_generator/exporter.py` | Word 报告生成 |
| `src/features/descriptors.py` | 统一描述符接口（含归一化） |
| `src/features/target_encoding.py` | 单体历史成膜率先验（CV 安全） |
| `scripts/stage11_dual_holdout.py` | 双留出/LOGO 评估基础设施（逐折报告标配） |
| `PROJECT_STATE.md` | 项目状态 |
| `SESSION_START.md` | 会话启动清单 |
