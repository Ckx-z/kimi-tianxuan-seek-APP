# PROJECT_STATE — 项目当前状态

> ⭐ **这是每次会话的第一份必读文件**。会话开头读它进入状态，结尾更新它。
> 最后更新：2026-07-20（阶段 12 完成：双模型路由上线——tree_v4 池内 + tree_v4_noTE 外推；指纹/校准路线证伪关闭）

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
| **阶段 10** | **归因与 App 接入选 tree_v3** | ✅ **已完成** |
| **阶段 11** | **统计先验入模 + 双留出评估 + 前端打分理由** | ✅ **已完成** |
| **阶段 12** | **表征升级检验（指纹证伪/交互消融/校准证伪）+ 双模型路由落地** | ✅ **已完成** |

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

## 五、阶段 9-11 小结与下一步（NEXT）

### 阶段 9：3D 描述符接入与全量验证（2026-07-16 完成）

| 模型 | PR-AUC | MAE | NDCG@10 | 特征数 | 关键配置 |
|---|---|---|---|---|---|
| `models/tree_v2.pkl` | 0.7484 | 0.2552 | 0.8318 | 122 | 精简规则 + Hadamard 交互 |
| `models/tree_v3.pkl` | **0.7785** | **0.2344** | 0.9055 | 142 | + 单体 3D 描述符（D19） |
| `models/tree_v3_dimer3d_full.pkl` | 0.7716 | 0.2395 | **0.9216** | 166 | + 二聚体 3D（保留为可选配置） |

- 3D 构象缓存：`data/interim/3d_cache/`（D18），全量特征化从 ~42 分钟降到秒级
- 实验报告：`EXPERIMENTS/exp_002.md`（500 对）、`EXPERIMENTS/exp_003.md`（全量）

### 阶段 10：tree_v3 归因与 App 接入（2026-07-17 完成）

**归因报告 `reports/attribution_v3.md` 关键结论：**
- 3D 特征累计占全局 SHAP 的 **29.8%**（醛 3D 18.1% + 胺 3D 11.8%）
- Top 3D 特征：`ald_3d_radius_ratio`（全局第 3）、`ald_3d_pmi_i2_i3`（第 4）、`ald_3d_mol_volume`（第 7）
- 全局前二仍为 `ald_n_aromatic_rings_per_site`、`int_hadamard_tpsa_per_site`

**App 接入（D20）：**
- `FilmPredictor` 默认树模型：`tree_baseline.pkl` → `models/tree_v3.pkl`
- 特征开关从模型 pkl 的 metrics 自动恢复，旧模型向后兼容
- 预测特征用 `reindex` 对齐，3D 计算失败的样本补 0

### 阶段 11：统计先验入模 + 双留出评估 + 前端打分理由（2026-07-20 完成）

**建模实验（exp_005，D21）：**

| 模型 | LOGO PR-AUC | 双留出 PR-AUC（3 种子均值） | in-sample 间隙 | 特征数 | 状态 |
|---|---|---|---|---|---|
| 胺频率基线 F1 | 0.8640 | 0.5231（≈正例率） | — | 1 | 基线 |
| `models/tree_v3.pkl` | 0.7610 | **0.6530** | 0.2154 | 142 | **App 默认（保持）** |
| `models/tree_v4.pkl` | **0.8784** | 0.6418 | **0.1209** | 144（+2 TE） | 保存为可选模型 |

- tree_v4 = TE（CV 安全 target encoding）+ 频率降权 + mild 正则：LOGO +0.117 并**首次超过频率基线 0.864**，in-sample 间隙减半——对"已知单体池内推荐"的 App 主场景是实质进步。
- 但切换门槛"双留出 PR-AUC 明确优于 v3"**未达成**（0.642 vs 0.653；单种子 +0.033 不可复现）→ **App 默认模型保持 tree_v3**（`src/predictor/__init__.py` 未动）。
- 双留出协议证实 exp_004 的泄漏判断：验证集胺全未见时 F1 基线坍缩到正例率，LOGO 的 0.864 在"单体全新"场景不存在。
- 输出：`scripts/stage11_dual_holdout.py`、`src/features/target_encoding.py`、`reports/stage11_dual_holdout.json`、`models/tree_v4.pkl`（+metrics，predictor 已兼容 `te_rates`）、`EXPERIMENTS/exp_005.md`。

**前端打分理由（App）：**
- `app/gradio_app.py` 新增「打分理由」板块（`_explain_tree_score`）：基于实际加载的树模型做 SHAP 归因，中文标签映射（`feature_label_zh` / `format_explanation_zh`），explainer 缓存（热态 0.03-0.05s）。
- 缺 shap 的环境仅降级本板块、不影响预测主流程；**base 环境已补装 shap 0.52.0**（2026-07-20），冒烟测试确认打分理由实际产出（见 `DAILY_LOG/2026-07-20.md`）。
- 测试：`tests/test_attribution_app.py`（无 shap 环境整文件跳过）。

**静默启动器：**
- `start_app.vbs` + `silent_launch.py`：pythonw 无窗口静默启动 + 自动开浏览器 + 失败弹 msgbox + `logs/` 日志。README 快速启动已更新，**推荐双击 `start_app.vbs`**。

### 阶段 12：表征升级检验 + 双模型路由落地（2026-07-20 完成，D22）

**表征路线检验（exp_006 / exp_007 / 校准实验）：**
- **Morgan/MACCS 指纹入模证伪**（exp_006，tree_v5）：LOGO 无增量（+0.003），双留出全部变体（0.62-0.64）低于无指纹的 v4_mild_noTE（0.6824）——指纹位在样本内"记单体身份"（gain 占 53%）但不迁移。tree_v5.pkl 仅存档，**不纳入路由**。
- **Hadamard 交互排除疑点**（exp_007）：去交互双留出不升反降（两族一致 −0.014），交互**不是**"背配对"记忆通道，确认保留；fold3 崩溃诊断为难折（先验反转 + 区域漂移大芳香单体），非泄漏/噪声。
- **概率校准证伪**：双留出 MAE 与 PR-AUC 背离的校准修复尝试未获收益（`reports/calibration.json`），路线关闭。

**双模型路由上线（exp_008，D22）——当前 App 行为：**

| 输入 | 路由 | 理由 |
|---|---|---|
| 醛/胺都已见于训练池 | **tree_v4**（144 维含 TE） | TE 先验有效，LOGO 0.8784 池内最强 |
| 任一单体未见 | **tree_v4_noTE**（142 维） | 频率降权+弱正则，双留出 3 种子均值 0.6824 全体最强 |

- 路由键：`models/monomer_pool.json`（训练单体池：醛 896 / 胺 1366，与 tree_v4 te_rates 键校验一致，常驻内存）；`src/predictor/routing.py`（MonomerPool + RoutedTreePredictor）；`FilmPredictor` 默认路由模式，显式 `tree_model_path` 或 `use_routing=False` 回退旧单模型行为（向后兼容，GNN 并行输出不变）。
- 预测标注实际模型 + 路由原因（概率区与打分理由双处展示）；打分理由跟随路由模型（v4 走 TE 填充，noTE 走原 v3 路径）。
- 路由验证（exp_008，`reports/routing_eval.json`）：routed 对原默认 v3 **全线不劣**（随机 KFold 整体 0.9383 vs 0.9329、双已见桶 0.9609 vs 0.9536；LOGO 整体 +0.031、胺未见桶 +0.056）——帕累托改进。
- **已知代价**（D22 第一复盘点）：规定键在"一新一熟"混合查询上让位于单一 v4（混合桶 −0.024、LOGO 胺已见桶 −0.031）；对照策略 routed_strict（仅双未见走 noTE）全部桶不劣于规定键，待 App 查询日志定型后评审切换。
- 池内模型维持 **v4_int**（含交互）：v4_noint LOGO +0.015 在逐折噪声内（配对差 std 0.019），双留出 3 种子一致 −0.014，且交互项是配对级归因解释来源。
- 输出：`scripts/stage12_train_noTE.py`（+`models/tree_v4_noTE.pkl`、`models/monomer_pool.json`）、`scripts/stage12_routing_eval.py`、`tests/test_routing.py`（14 个）、`EXPERIMENTS/exp_008.md`、DECISIONS D22。测试 **54 passed**。

**模型清单（models/）：**

| 模型 | 特征数 | LOGO | 双留出（3 种子） | 状态 |
|---|---|---|---|---|
| `tree_v3.pkl` | 142 | 0.7610 | 0.6530 | 单模型模式默认 / 向后兼容回退 |
| `tree_v4.pkl` | 144（+2 TE） | **0.8784** | 0.6418 | **路由·池内臂** |
| `tree_v4_noTE.pkl` | 142 | 0.7900 | **0.6824** | **路由·外推臂** |
| `tree_v5.pkl` | 4240（+指纹） | 0.8713 | 0.6366 | 存档（指纹证伪，不入路由） |
| `tree_baseline/tree_v2*` | — | — | — | 历史存档 |

### 下一步

**阶段 13 候选（源自阶段 12 遗留）：**

1. **双未见单体泛化（真正瓶颈，未解决）**：双留出 0.63-0.68；指纹已证伪，剩余方向：(a) **GNN 表征迁移**（embedding 蒸馏/迁移入树模型，而非手工位）；(b) 配对/界面级表征；(c) 外部数据预训练。
2. **routed_strict 切换评审**（D22 第一复盘点）：待 App 查询日志显示"一新一熟"占比后决定；exp_008 已给出收益上限 +0.024~0.031。
3. **双留出逐折报告标配化**：fold 级 std≈0.21，合并指标会掩盖难折（exp_007 fold3）；今后外推评估标配逐折 PR-AUC + 按折方差 + 多种子。
4. **fold3 型"区域漂移大单体"专项切片**：表征升级评估时对 raw-feature 包络外样本单独计分。
5. **更早的候选（仍有效）**：异常检测再评估（实验 D 条件泄漏 0.9112）、SMILES 清洗诊断、LightGBM 对比、仅二聚体 3D 消融（D19 复盘点）。

---

## 六、阻塞点 / 待决策（BLOCKERS）

暂无阻塞。待决策：
- **routed_strict 切换评审**（D22 第一复盘点）：exp_008 证据显示"仅双未见走 noTE"在全部已测桶不劣于现规定键（混合桶 +0.024~0.031），待 App 查询日志定型后决定
- 双未见单体泛化（0.63-0.68）的表征升级路线：指纹已证伪 → GNN embedding 迁移 / 配对级表征 / 外部预训练
- 双未见场景 v4 vs noTE 两协议方向冲突（双留出网格 noTE 胜、LOGO 胺未见桶 v4 胜，均在噪声量级）：表征升级后两协议同时复测
- 是否做仅二聚体 3D（无单体 3D）的消融，确认二聚体的独立排序价值（D19 复盘点）
- 是否引入 LightGBM 与 XGBoost 对比

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
- **App 前端**：base 环境 Python 3.13（已安装 gradio 6.20、python-docx、xgboost、**shap 0.52.0** — 2026-07-20 补装，打分理由可用）或 `.venv` 环境
- **GNN 预测**：通过 subprocess 自动调用 dphuanjing 环境 Python 3.8（torch 2.3.1 + PyG 2.6.1）

### 启动 App

方式 1（推荐）：双击 `start_app.vbs` —— pythonw 静默启动，无黑色终端窗口，自动打开浏览器；失败弹 msgbox 提示，日志写入 `logs/`（由 `silent_launch.py` 实现）

方式 2：命令行
```bash
cd "C:\Users\ckx\Desktop\全新机器学习实验"
.venv\Scripts\python.exe app/gradio_app.py
```

方式 3：双击 `start_app.bat`（终端独立运行，不会随关闭）

打开浏览器访问：`http://127.0.0.1:7860`

### 为什么不能直接用 dphuanjing 启动 App？

dphuanjing 是 Python 3.8，gradio 6.20 不支持 Python 3.8（有 pydantic 兼容性问题）。
因此 App 用 base 环境，GNN 用 dphuanjing 环境，通过 subprocess 隔离。
