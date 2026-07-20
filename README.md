# COF 成膜单体组合推荐系统

> 预测能形成膜的共价有机框架（COF）单体组合，输出成膜概率、推荐实验条件、生成 Word 实验报告。

---

## 项目状态

**App MVP 已完成**（2026-07-08）。

- ✅ 输入醛 + 胺 SMILES → 输出成膜概率
- ✅ 推荐实验条件 / 溶液配比（基于规则 + 历史案例）
- ✅ 生成 Word 实验报告（内嵌单体 2D 结构图）
- ✅ 展示化学结构图：醛/胺单体 2D 结构 + 亚胺缩合产物骨架（RDKit 渲染，非法 SMILES 优雅降级）
- ✅ Gradio 前端可启动
- ✅ GNN v5.3 已通过 subprocess 接入
- ✅ 树模型基线（PR-AUC 0.727）

---

## 目录结构

```
全新机器学习实验/
├── app/gradio_app.py              # Gradio App 入口
├── start_app.bat                  # 一键启动脚本（Windows 双击）
├── start_app.ps1                  # PowerShell 启动脚本
├── src/
│   ├── predictor/                 # 预测层（树模型 + GNN）
│   ├── condition_recommender/     # 条件推荐层（规则 + 案例）
│   ├── report_generator/          # Word 报告生成
│   ├── features/                    # 描述符工程
│   └── data/                        # 数据导入 + 审计
├── data/raw/                      # 从旧项目复制的训练数据
├── data/experimental_refs/        # 从 实验 目录复制的参考文档
├── models/tree_baseline.pkl       # 已训练树模型
├── reports/                        # 生成的报告
├── PROJECT_STATE.md               # 项目当前状态（必读）
├── SESSION_START.md               # 会话启动清单
├── DECISIONS.md                   # 关键决策记录
├── DATA_DICT.md                   # 数据字典
├── DAILY_LOG/                     # 日报
└── EXPERIMENTS/                   # 实验记录
```

---

## 运行环境

### 双环境设计

| 组件 | 环境 | 说明 |
|---|---|---|
| App 前端（Gradio） | base Python 3.13 | 已安装 gradio 6.20、python-docx、xgboost |
| GNN 预测 | dphuanjing Python 3.8 | torch 2.3.1 + PyG 2.6.1，通过 subprocess 调用 |
| 树模型 | base Python 3.13 | 直接在 base 运行 |

**为什么用两个环境？** dphuanjing 是旧项目环境，有 GNN 所需的 torch/PyG，但 Python 3.8 装不了新版 Gradio。新 App 用 base 环境，GNN 通过 subprocess 调用 dphuanjing，互不冲突。

### 环境要求

- 已安装 Anaconda
- base 环境：Python 3.13，已安装 `gradio`, `python-docx`, `xgboost`, `rdkit`, `pandas`, `joblib`
- dphuanjing 环境：Python 3.8，含 `torch 2.3.1`, `torch_geometric 2.6.1`, `rdkit`

如果缺少依赖：

```bash
pip install gradio python-docx xgboost rdkit joblib pandas scikit-learn
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

会打开独立终端窗口运行，可在终端里查看实时日志；关掉窗口即关闭 App。

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
    ├──► [预测层] 树模型 + GNN v5.3 → 成膜概率
    │
    └──► [条件推荐层] 规则引擎 + 案例匹配 → 实验条件
              │
              ▼
        [报告生成层] Word 实验报告
```

- **预测层 ML**：树模型 + GNN 并行，输出综合概率
- **条件推荐**：基于单体类型（F/CF3/酰肼/常规）和拓扑，用规则 + 历史案例推荐
- **报告生成**：python-docx 填充报告

---

## 测试

```bash
cd "C:\Users\ckx\Desktop\全新机器学习实验"
pytest tests/ -v
```

当前测试覆盖：
- 描述符计算
- 条件推荐
- 树模型训练/预测
- Word 报告生成
- 数据导入

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
2. 写/更新 `DAILY_LOG/YYYY-MM-DD.md`
3. 新决策记入 `DECISIONS.md`
4. 实验记入 `EXPERIMENTS/`

---

## 后续优化方向

1. **树模型准确率提升**：PR-AUC 0.727 < GNN 0.784，需改进特征工程（3D 描述符、LightGBM）
2. **GNN 全接入**：目前通过 subprocess 调用，未来可直接导入（需解决 src 包名冲突）
3. **案例库丰富**：从 `data/experimental_refs/main_template.docx` 自动提取历史案例
4. **报告模板优化**：复用现有 Word 模板格式
5. ~~**单体结构图可视化**~~：已完成（2026-07-20，App + Word 报告均内嵌 RDKit 渲染结构图）

---

## 关键文件

| 文件 | 说明 |
|---|---|
| `app/gradio_app.py` | Gradio 前端 |
| `src/predictor/tree_model.py` | XGBoost 树模型 |
| `src/predictor/gnn_model.py` | GNN v5.3 subprocess 封装 |
| `src/condition_recommender/` | 条件推荐规则 + 案例 |
| `src/report_generator/exporter.py` | Word 报告生成 |
| `src/utils/molecule_viz.py` | 分子结构渲染（SMILES→PNG，App/报告复用） |
| `src/features/descriptors.py` | 统一描述符接口（含归一化） |
| `PROJECT_STATE.md` | 项目状态 |
| `SESSION_START.md` | 会话启动清单 |
