# PROJECT_STATE — 项目当前状态

> ⭐ **这是每次会话的第一份必读文件**。会话开头读它进入状态，结尾更新它。
> 最后更新：2026-07-16（全量验证完成：tree_v3 PR-AUC 0.7785，单体 3D 进主模型）

---

## 一、项目定位（一句话）

**输入候选单体组合 + 反应条件，输出"成膜可能性打分 + 排序 + SHAP 理由 + 相似成膜案例"，指导实验师优先试哪些。**

- **任务形态**：推荐 / 打分排序（非二分类，非生成新分子）—— 规避负样本严重缺失
- **模型策略**：**XGBoost/LightGBM + 化学描述符**（主可解释模型）与 **旧项目 GNN（PR-AUC 0.784）**（并行准确率模型）共同发展
- **核心增量**：反应条件首次作为特征；描述符尺度归一化；留一单体法评估；SHAP 可解释

---

## 二、当前阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| **阶段 0** | 搭骨架 + 文档体系 + 可运行基础设施 | ✅ **已完成** |
| 阶段 1 | 数据审计（评估旧数据质量） | ✅ **已完成** |
| 阶段 2 | 结构补全（名称→SMILES 映射） | ✅ **已完成** |
| 阶段 3 | 特征工程（描述符流水线，含条件） | ✅ **已完成** |
| 阶段 4 | 建模+评估（树模型 + GNN 并行） | ✅ **已完成** |
| 阶段 5 | App MVP 交付（Gradio 前端 + Word 报告） | ✅ **已完成** |
| **阶段 6** | **记忆系统与上下文管理建设** | ✅ **已完成** |
| **阶段 7** | **反应条件补全（YAML → CSV）** | ✅ **已完成** |
| **阶段 8** | **模型 v2：精简规则 + Hadamard 交互 + 归因** | ✅ **已完成** |
| **阶段 9** | **3D/二聚体描述符接入与对比** | ✅ **已完成** |
| **阶段 10** | **归因与 App 接入选 tree_v3** | 🔄 **待开始** |

**阶段 6 子任务（记忆系统建设）**：
- [x] 创建 `.agents/` 目录
- [x] 编写 `.agents/AGENTS.md`
- [x] 编写 `.agents/session_index.yaml`
- [x] 编写 `.agents/session_state.yaml`
- [x] 升级 `SESSION_START.md`
- [x] 创建 `scripts/check_project_state.py`
- [x] 更新 `.gitignore`
- [x] 运行检查脚本验证通过
- [x] 写 AI 日报 `DAILY_LOG/2026-07-15.md`
- [x] 写人日报 `DAILY_LOG/2026-07-15_human.md`

**阶段 7 子任务（条件补全）**：
- [x] 分析 YAML 结构（structured_v2/v3）
- [x] 确定匹配键（paper_id + group_id）和字段映射
- [x] 编写 `scripts/fill_conditions_from_yaml.py`
- [x] 运行 YAML 精确匹配（848 行）
- [x] 运行单体推断补全（3179 行）
- [x] 验证覆盖率（溶剂 64.2%，温度 63.2%，合成路线 64.9%）
- [x] 验证无数据覆盖（0 丢失）
- [x] 生成 `data/interim/v5_train_stage1_cond_filled.csv`

---

## 三、反应条件补全（阶段 7，已完成）

### 核心发现

**CSV 条件缺失不是因为原始数据没有，而是因为 YAML → CSV 的映射丢失了。**

- `v5_train_stage1.csv` 条件缺失率：86-90%
- `structured_v2` YAML 条件覆盖率：溶剂 91.9%，温度 90.2%，合成路线 99.0%

### 补全方法

**两阶段补全策略：**

1. **YAML 精确匹配**（848 行）：`paper_id` + `group_id` 匹配 `structured_v2/v3`
2. **单体推断**（3179 行）：`aug_`/`hr_`/`cr_` 行用同醛/同胺的条件众数推断

### 补全结果

| 字段 | 补全前 | 补全后 | 提升 |
|---|---|---|---|
| solvent | 13.3% | **64.2%** | +50.9% |
| temperature | 12.7% | **63.2%** | +50.5% |
| synthesis_route | 13.7% | **64.9%** | +51.3% |
| interface_type | 10.1% | **55.7%** | +45.6% |
| catalyst | 11.4% | **55.2%** | +43.7% |
| time | 0% | **12.0%** | +12.0%（新增字段） |
| catalyst_volume | 0% | **7.1%** | +7.1%（新增字段） |
| atmosphere | 0% | **12.5%** | +12.5%（新增字段） |

- 总样本：6201 行
- YAML 精确匹配：848 行
- 单体推断补全：3179 行
- **数据安全**：0 行原有值被覆盖

### 输出文件

- `data/interim/v5_train_stage1_cond_filled.csv` — 补全后的训练数据
- `data/interim/condition_fill_report.json` — 补全统计报告
- `scripts/fill_conditions_from_yaml.py` — 补全脚本（可复用）

---

## 四、模型 v2 迭代结果（阶段 8，已完成）

在条件补全后的数据上重新训练树模型，完成模型 v2 并做消融与归因。

### 核心决策

- **精简化学规则**：只保留用户确认的 9 条核心硬性规则（官能团有效性 + 对称/对位），移除其余 25 条启发式规则。
- **移除 `hard_rule_sampled` 负样本**：该批负样本由旧规则硬过滤生成，与标签强相关，保留会导致泄漏。
- **加入醛-胺 Hadamard 交互特征**：让醛和胺双向观察，类似 GNN 中的逐元素积交互。
- **反应条件不作为主模型特征**：消融显示条件特征 PR-AUC 高达 0.9112，疑似与标签强相关/泄漏；用户确认条件非刚需，主模型未启用。

### 模型对比

| 模型 | PR-AUC | MAE | 特征数 | 关键配置 |
|---|---|---|---|---|
| `models/tree_baseline.pkl` | 0.7270 | — | 85 | 原始 34 维规则 + 无交互 |
| `models/tree_v2.pkl` | **0.7484** | **0.2552** | 122 | 精简规则 + Hadamard 交互 |

### 输出文件

- `models/tree_v2.pkl` — 主模型权重
- `models/tree_v2_metrics.json` — 验证指标
- `EXPERIMENTS/exp_001.md` — 消融实验报告（A/B/C/D 四组）
- `reports/attribution_v2.md` — SHAP 归因报告

### 关键发现

- 全局特征重要性：`interaction` > `aldehyde` > `amine` > `rules`
- Top 特征：`int_hadamard_tpsa_per_site`、`ald_n_aromatic_rings_per_site`、`ald_aromatic_frac`、`rule_C3对位(非间位)`
- 醛侧特征贡献整体高于胺侧，交互特征有效补全了配对信息。

## 五、下一步（NEXT）

**阶段 9 已完成，进入阶段 10：**

1. **生成 tree_v3 归因报告**：分析单体 3D 特征的 SHAP 贡献
2. **更新 App/predictor**：让前端默认调用 `tree_v3.pkl`
3. **消融实验（可选）**：分别关闭 3D、二聚体、交互特征，分析各自贡献
4. **异常检测再评估**：条件特征实验 D PR-AUC 0.9112 异常高，需单独诊断是否与标签泄漏有关

---

## 六、阻塞点 / 待决策（BLOCKERS）

暂无阻塞。待决策：
- 阶段 9 是否优先做单体 3D，还是单体 3D 与二聚体 3D 同步接入？
- 是否需要在阶段 9 同时尝试 LightGBM，还是等 3D 特征稳定后再对比模型？

---

## 七、关键数据资产（来自旧项目审阅）

| 资产 | 位置 | 规模 | 说明 |
|---|---|---|---|
| 主训练集 | `旧项目/data/processed/v5_train_stage1.csv` | 6,201 行 | **16 列含反应条件**，可直接用 |
| 增广训练集 v2 | `旧项目/data/processed/v5_train_stage1_aug_v2.csv` | 6,392 行 | 含三联苯+F/CF3 增广 |
| 单体池 | `旧项目/data/processed/merged_monomer_pool.csv` | 醛+胺 SMILES 库 | 用于候选生成 |
| 笛卡尔积配对 | `旧项目/data/processed/v4_cartesian_pairs.csv` | ~232K | 筛选候选池 |
| 结构化 YAML | `旧项目/data/structured/` 等 | ~955 篇 | LLM 提取的原始字段 |
| GNN 模型权重 | `旧项目/models/v5.0~v5.3/v5_model.pt` | — | 作为对比基线 |
| 化学描述符代码 | `旧项目/src/chemistry/` | — | 26维linker+10维3D+34维规则，可复用 |
| 知识图谱 | `旧项目/graphrag图谱辅助/graph.gml` | 809单体+954文献 | 可视化+查询 |

---

## 八、旧项目核心教训（必须牢记）

1. **多任务在小数据上梯度冲突**（v3 崩溃，PR-AUC 仅 0.36）→ 新项目禁用多任务
2. **小数据下增广不如外推检测**（Plan C 失败）→ 优先做外推检测而非盲目增广
3. **全负样本文献的配对是假负样本**（v5 关键修复）→ 已在 v5 修复，继承
4. **合成负样本必须分配独立 paper_id**（防 CV 泄漏）→ 继承
5. **SMILES 缺失率 29.5% 是最大丢弃源** → 阶段 2 重点解决
6. **反应条件从未作为特征使用**（旧项目只用结构+规则）→ **新项目最大突破口**
7. **GNN v5 已达 PR-AUC 0.784**，并非"完全没做好"，问题是筛选阶段泛化
8. **规则向量注入 FilmHead 是正确方向**（非硬过滤），继承为树模型特征

---

## 九、会话工作流（每次会话必做）

> 详见 **SESSION_START.md** 完整清单。简版：

1. **开头**：读 `.agents/AGENTS.md` → `PROJECT_STATE.md` → `.agents/session_index.yaml` → 最新 `DAILY_LOG` → `DECISIONS.md` → `DATA_DICT.md` → `.agents/session_state.yaml` → 进入状态
2. **运行**：`python scripts/check_project_state.py` 检查状态一致性
3. **结尾**：更新本文件状态 + 写 AI 日报 + 写人日报 + 记录关键决策到 `DECISIONS.md` + 更新 `.agents/session_state.yaml` + 更新 `.agents/session_index.yaml`
4. **遇到选择**：记入 `DECISIONS.md`（为什么/考虑过什么/何时复盘）
5. **每阶段完成**：写实验记录到 `EXPERIMENTS/`

> 文档体系替代静态 CLAUDE.md，随项目演化持续更新，方便换任何大模型对接。

---

## 十、运行环境说明

### 推荐运行方式

- **树模型训练/脚本**：`.venv\Scripts\python.exe`（Python 3.12，已安装 numpy、pandas、scikit-learn、xgboost、rdkit、pyyaml、shap、joblib）
- **App 前端**：base 环境 Python 3.13（已安装 gradio 6.20、python-docx、xgboost）或 `.venv` 环境
- **GNN 预测**：通过 subprocess 自动调用 dphuanjing 环境 Python 3.8（torch 2.3.1 + PyG 2.6.1）

### 启动 App

方式 1：命令行
```bash
cd "C:\Users\ckx\Desktop\全新机器学习实验"
.venv\Scripts\python.exe app/gradio_app.py
```

方式 2：双击 `start_app.bat`（终端独立运行，不会随关闭）

打开浏览器访问：`http://127.0.0.1:7860`

### 为什么不能直接用 dphuanjing 启动 App？

dphuanjing 是 Python 3.8，gradio 6.20 不支持 Python 3.8（有 pydantic 兼容性问题）。
因此 App 用 base 环境，GNN 用 dphuanjing 环境，通过 subprocess 隔离。
