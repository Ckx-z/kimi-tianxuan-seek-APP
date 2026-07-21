# COF 成膜单体组合推荐系统

> 预测能形成膜的共价有机框架（COF）单体组合，输出成膜概率、打分理由、化学结构图，推荐实验条件，生成 Word 实验报告。

---

## 项目状态

**阶段 13 进行中**（2026-07-21）：GNN embedding 迁移阶段 A 过门槛（pair_emb 双留出 0.6824→0.844，D24），阶段 B 逐折重训待做；App 双模型路由行为不变。

- ✅ 输入醛 + 胺 SMILES → 输出成膜概率（GNN + 树模型 + 综合）
- ✅ **双模型路由**（D23）：双未见单体 → `tree_v4_noTE`（外推臂）；其余 → `tree_v4`（池内臂），前端显示路由原因
- ✅ **SHAP 打分理由**：全中文解释哪个官能团/特征推高或拉低成膜分（热态 ~0.04s）
- ✅ **化学结构图**：醛/胺单体 2D 结构 + 亚胺缩合产物骨架（RDKit 渲染，非法 SMILES 优雅降级）
- ✅ 推荐实验条件 / 溶液配比（基于规则 + 历史案例）
- ✅ 生成 Word 实验报告（内嵌单体结构图）
- ✅ 双击 `start_app.vbs` 无窗口静默启动，自动打开浏览器
- ✅ GNN v5.3 通过 subprocess 接入（PR-AUC 0.784）
- ✅ 测试 57 项全部通过

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

## 目录结构

```
全新机器学习实验/
├── app/gradio_app.py              # Gradio App 入口（概率 + 打分理由 + 结构图）
├── start_app.vbs                  # 双击无窗口启动（推荐）
├── start_app.bat                  # 调试启动（终端可见日志）
├── silent_launch.py               # 静默启动器（vbs 调用）
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
├── models/                        # 模型权重（.pkl 不入库）+ monomer_pool.json
├── reports/                       # 实验指标 JSON + 归因报告 + 生成的 docx
├── PROJECT_STATE.md               # 项目当前状态（必读）
├── SESSION_START.md               # 会话启动清单
├── DECISIONS.md                   # 关键决策记录（D1-D23）
├── DATA_DICT.md                   # 数据字典
├── DAILY_LOG/                     # 日报（AI 版 + 人版双轨）
└── EXPERIMENTS/                   # 实验记录（exp_001-exp_008）
```

---

## 运行环境

### 双环境设计

| 组件 | 环境 | 说明 |
|---|---|---|
| App 前端（Gradio） | base Python 3.13 | gradio 6.20、python-docx、xgboost、shap |
| GNN 预测 | dphuanjing Python 3.8 | torch 2.3.1 + PyG 2.6.1，通过 subprocess 调用 |
| 树模型训练 | `.venv` Python 3.12 | xgboost、rdkit、shap、scikit-learn |

**为什么用两个环境？** dphuanjing 是旧项目环境，有 GNN 所需的 torch/PyG，但 Python 3.8 装不了新版 Gradio。新 App 用 base 环境，GNN 通过 subprocess 调用 dphuanjing，互不冲突。

### 环境要求

- 已安装 Anaconda
- base 环境：Python 3.13，已安装 `gradio`, `python-docx`, `xgboost`, `rdkit`, `shap`, `pandas`, `joblib`
- dphuanjing 环境：Python 3.8，含 `torch 2.3.1`, `torch_geometric 2.6.1`, `rdkit`

如果缺少依赖：

```bash
pip install gradio python-docx xgboost rdkit shap joblib pandas scikit-learn
```

---

## 快速启动

### 方式 1：双击 `start_app.vbs`（推荐，无窗口启动）

直接双击 `start_app.vbs`：

- **无任何黑色终端窗口**，App 在后台静默运行
- 自动打开浏览器到 `http://127.0.0.1:7860`
- 若 App 已在运行，双击只会再打开一个浏览器标签页，不会重复启动
- 启动失败（依赖缺失、进程异常退出、超时等）会**弹出错误提示框**，详细日志见 `logs/app_launch.log` 和 `logs/gradio_app.log`

### 方式 2：双击 `start_app.bat`（调试用）

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

### 打不开怎么办？

1. **静默启动失败**——看弹窗提示与 `logs/gradio_app.log` 末尾的报错；或改用 `start_app.bat` 在终端里直接看报错
2. **端口被占用**——`start_app.vbs` 会自动复用已运行的实例；如需换端口，改 `app/gradio_app.py` 最后一行 `server_port=7861`
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

当前 **57 项测试全部通过**，覆盖：描述符计算、条件推荐、树模型训练/预测、Word 报告生成、数据导入、SHAP 归因（含 TE 模型）、双模型路由、分子渲染、RDKit 日志行为。

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

1. **双未见单体泛化 0.63–0.68（核心瓶颈）→ 阶段 13 阶段 A 已突破（待确认）**：GNN 配对 embedding 使双留出升至 0.844（+0.16），但全量 GNN 存在记忆泄漏可能（乐观上界）；阶段 B 逐折重训 GNN 排除后才能确认（exp_009 / D24）
2. **fold3 型区域漂移**：大芳香醛等落在训练分布包络外的单体是难折来源，需专项切片跟踪
3. **案例库丰富**：从 `data/experimental_refs/main_template.docx` 自动提取历史案例
4. **GNN 全接入**：目前通过 subprocess 调用，未来可直接导入（需解决 src 包名冲突）

---

## 关键文件

| 文件 | 说明 |
|---|---|
| `app/gradio_app.py` | Gradio 前端 |
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
