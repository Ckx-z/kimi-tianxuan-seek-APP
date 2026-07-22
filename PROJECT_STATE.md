# PROJECT_STATE — 项目当前状态

> ⭐ **这是每次会话的第一份必读文件**。会话开头读它进入状态，结尾更新它。
> 最后更新：2026-07-22（阶段 21：P4a/b——实验记录四项修复 + LLM 基座（longcat 推理模型适配）+ 单体性质卡 + 方案卡模板系统；299 passed）

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
| **阶段 13** | **GNN embedding 迁移：阶段 A 过门槛 → 阶段 B 闭卷证伪，路线关闭** | ✅ **已完成** |
| **阶段 14** | **真实回测：14 组已做实验三方对照（新模型 vs 旧 GNN 当年预测 vs 真实结果）** | ✅ **已完成** |
| **阶段 15** | **P2：打分 + 不确定性 + OOD 三件套（bagging 集成 / OOD 三级制 / 打分非概率口径）** | ✅ **已完成** |
| **阶段 16** | **App 重设计 P1：五标签页骨架 + 视觉美化 + 页①（SMILES/CAS/内置库查询 + 相似案例）+ 页②批量排序 + 预测日志；minimax RAG 契约对接** | ✅ **已完成** |
| **阶段 17** | **App 双根因修复（旧进程占位 + gradio theme bug）+ 启动器单实例重启语义 + 定制图标/桌面快捷方式；minimax subtree 合并入主仓库（D28）** | ✅ **已完成** |
| **阶段 18** | **App 重设计 P2：页③收藏夹（文献自动匹配）+ 页④实验记录（rag_export 契约落盘）+ 方案卡（侯老师法+防错清单）；minimax LLM 双端点（MiniMax 主 + longcat 备）** | ✅ **已完成** |
| **阶段 19** | **Bug 修复（主分数口径统一为路由树模型分；页③收藏选中 gradio 6 choices 校验 bug）+ P3：页⑤方案迭代展示、收藏去重、游离记录、文献标题映射（1711 条）** | ✅ **已完成** |
| **阶段 20** | **主分数口径改 max(树, GNN) 乐观召回 + 护栏标注（D29）；预测日志 ood=out 置 null 契约修复** | ✅ **已完成** |
| **阶段 21** | **P4a/b：实验记录四项修复（按收藏过滤/实验编号必填/切换刷新/双溶剂+洗脱剂）+ LLM 基座三级配置链 + longcat 推理模型适配 + 单体性质卡 + 方案卡模板系统（内置侯老师默认 + docx 上传 LLM 提取）；用户数据 gitignore（D30）；页⑤自然语言迭代暂缓** | ✅ **已完成** |

**阶段 16 要点（2026-07-21）**：
- 前端全量重写（`app/gradio_app.py` 284→870 行）：`gr.themes.Soft` 深青/石墨学术主题、色彩语义（⛔/⚠️/✓）、五标签页骨架（③收藏夹 ④实验记录 ⑤RAG 迭代为占位，P2/P3 期做）
- 页①：三种输入（SMILES 直输 / CAS 号 PubChem 解析+缓存 / 内置单体库 17 个下拉点选）+ 相似成膜案例（Morgan Tanimoto，含 paper_id）
- 页②：批量排序（库多选/粘贴/CSV → 排序表 → 导出 CSV），逐对预测 + OOD 沉底
- 预测日志 `data/prediction_log.jsonl`（schema_version=1，支撑 D23 复盘点）
- RAG 对接：`data/rag_export/` 三 schema 契约（prediction/experiment_record/suggestion）+ minimax 侧 `docs/COF_APP_CONTRACT.md` + `adapters/cof_app_ingest.py`（**minimax 改动未 commit，待用户审**）
- 测试 77 → **113 passed**；依据文档：`docs/APP_REDESIGN_PROPOSAL.md`

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

**双模型路由（exp_008 上线 D22，routed_strict 切换 D23）——当前 App 行为：**

| 输入 | 路由 | 理由 |
|---|---|---|
| 醛/胺都已见于训练池 | **tree_v4**（144 维含 TE） | TE 先验有效，LOGO 0.8784 池内最强 |
| 一新一熟（任一未见但非双未见） | **tree_v4** | 单侧 TE 仍有强信号：混合桶 PR-AUC +0.024（A）/ +0.031（B），D23 |
| 醛/胺均未见（双未见） | **tree_v4_noTE**（142 维） | 频率降权+弱正则，双留出 3 种子均值 0.6824 全体最强 |

- 路由键：`models/monomer_pool.json`（训练单体池：醛 896 / 胺 1366，与 tree_v4 te_rates 键校验一致，常驻内存）；`src/predictor/routing.py`（MonomerPool + RoutedTreePredictor）；`FilmPredictor` 默认路由模式，显式 `tree_model_path` 或 `use_routing=False` 回退旧单模型行为（向后兼容，GNN 并行输出不变）。
- 预测标注实际模型 + 路由原因（概率区与打分理由双处展示）；打分理由跟随路由模型（v4 走 TE 填充，noTE 走原 v3 路径）。
- 路由验证（exp_008，`reports/routing_eval.json`）：routed 对原默认 v3 **全线不劣**（随机 KFold 整体 0.9383 vs 0.9329、双已见桶 0.9609 vs 0.9536；LOGO 整体 +0.031、胺未见桶 +0.056）——帕累托改进。
- **routed_strict 已复盘切换（D23，D22 第一复盘点关闭）**：仅双未见走 noTE、其余走 v4。证据：全部已测分桶 PR-AUC 不劣于原规定键（混合桶 +0.024，3 种子逐一胜；B 胺已见桶 +0.031 且 MAE 同优）；唯一回退为 A 混合桶 MAE −0.0075（列为 D23 复盘点）；评审不依赖查询占比（占比只影响收益大小、不改变方向），故不待 App 日志直接切换。复盘详见 `EXPERIMENTS/exp_008.md` 复盘小节。
- **双留出逐折报告标配化（D23 配套）**：`stage11_dual_holdout.py` / `stage11_hadamard_ablation.py` / `stage12_fingerprint.py` 输出标配 `per_fold` 逐折明细 + `fold_summary`（fold 级均值±std + 最难折，共享 `fold_summary()` 公式）。实测印证动机：v3_ref 双留出 fold 级 std 0.209，最难折 fold3 仅 0.334（合并指标 0.627 完全掩盖）；hadamard 既有逐折数据已免训练补汇总（`--summarize-existing`），stage12_fingerprint 既有报告下次运行自动继承。
- 池内模型维持 **v4_int**（含交互）：v4_noint LOGO +0.015 在逐折噪声内（配对差 std 0.019），双留出 3 种子一致 −0.014，且交互项是配对级归因解释来源。
- 输出：`scripts/stage12_train_noTE.py`（+`models/tree_v4_noTE.pkl`、`models/monomer_pool.json`）、`scripts/stage12_routing_eval.py`、`tests/test_routing.py`（14 个）、`EXPERIMENTS/exp_008.md`、DECISIONS D22。测试 **54 passed**。

**模型清单（models/）：**

| 模型 | 特征数 | LOGO | 双留出（3 种子） | 状态 |
|---|---|---|---|---|
| `tree_v3.pkl` | 142 | 0.7610 | 0.6530 | 单模型模式默认 / 向后兼容回退 |
| `tree_v4.pkl` | 144（+2 TE） | **0.8784** | 0.6418 | 单模型存档（可显式回退） |
| `tree_v4_noTE.pkl` | 142 | 0.7900 | **0.6824** | 单模型存档（可显式回退） |
| `tree_v4_ens.pkl` | 144（+2 TE） | 0.8853（集成） | 0.6136~0.634（3 划分） | **路由·池内臂（5 种子 bagging，D27）** |
| `tree_v4_noTE_ens.pkl` | 142 | 0.7911（集成） | 0.663~0.706（3 划分） | **路由·外推臂（5 种子 bagging，D27）** |
| `tree_v5.pkl` | 4240（+指纹） | 0.8713 | 0.6366 | 存档（指纹证伪，不入路由） |
| `tree_baseline/tree_v2*` | — | — | — | 历史存档 |

### 阶段 13：GNN embedding 迁移（2026-07-21 阶段 A 完成，D24）

**阶段 A（可行性，exp_009）**：全量 GNN v5.3 embedding 入 XGBoost（v4_mild + 频率降权，noTE 配置），双留出 3 种子 + LOGO 同口径评估：

| variant | 特征 | 维度 | LOGO | 双留出 3 种子均值 | Δ vs 基线 | 判定 |
|---|---|---|---|---|---|---|
| `emb_base`（基线复跑） | X_base | 142 | 0.7900 | 0.6824 | — | 逐位吻合历史 ✓ |
| `emb_pair_only` | 配对 embedding | 512 | **0.9022** | 0.8431 | **+0.1607** | **PASS** |
| `emb_pair_plus` | X_base + 配对 embedding | 654 | 0.8953 | **0.8442** | **+0.1618** | **PASS** |
| `emb_mono_plus` | X_base + 单体 embedding | 398 | 0.7762 | 0.6719 | −0.0105 | 证伪关闭 |

- 提取点（不改旧项目任何文件）：`V4Model._get_features` → `[ea‖eb‖ea⊙eb‖e_pair]` 512 维（交叉注意力后，配对相关）；mono 为 encoder 出口 mean-pool（注意力前）。资产 `data/interim/gnn_emb_v53_{pair,mono}.npy`（不入 git）。
- **泄漏警告**：GNN 训练集与本数据单体几乎完全重叠，阶段 A 是乐观上界——+0.16 与"配对标签记忆"形态量级不可区分（embedding 维 gain 占 81.1%），**不能当作泛化收益证据**。
- 附带发现：seed42 fold3（区域漂移难折，n=203）0.364 → 0.65–0.68，各最难折大幅抬升。
- 脚本：`scripts/stage13_extract_gnn_emb.py`（dphuanjing，GPU ~10s 前向，自检 Tp+Pa 0.6474≈0.665）、`scripts/stage13_gnn_embedding_eval.py`（.venv）；报告 `reports/gnn_embedding_eval.json`；实验记录 `EXPERIMENTS/exp_009.md`。

**阶段 B（2026-07-21 完成，exp_010 / D25）：闭卷证伪，路线关闭**

逐折从零重训 GNN（每折 2.5–6.8 min，3 种子 15 折 ~80 min；绝不加载 v5.3 权重），闭卷 pair_emb 同口径评估：

| variant | s42 | s123 | s7 | 3 种子均值 | Δ vs 闭卷基线 |
|---|---|---|---|---|---|
| base（142 描述符） | 0.6694 | 0.7075 | 0.6703 | **0.6824** | — |
| pair_only（512 闭卷 emb） | 0.6381 | 0.6443 | 0.6698 | 0.6507 | −0.032 |
| pair_plus（654） | 0.6306 | 0.6467 | 0.6668 | 0.6480 | **−0.034（逐种子全负）** |
| gnn_direct（折内 GNN 直接预测） | 0.6661 | 0.5850 | 0.6440 | 0.6317 | −0.051 |

- **判定 FALSIFIED**（事先约定对称门槛）：阶段 A 的 +0.1618 判为**配对标签记忆**，路线关闭。
- **闭卷对照结论**：严格双未见下 GNN 端到端 0.6317 < 描述符树 0.6824——双未见场景**树模型胜**，142 维手工描述符仍是当前最强外推表征。
- **fold3 闭卷全线坍缩**（s42 fold3：树 0.364 / GNN 0.360 / emb 0.37）——阶段 A 的 fold3"拯救"同为记忆；区域漂移折对一切"从已见分子学习"的方法是死穴。
- **基础设施修复**：`dual_holdout_folds` 折分组跨 pandas 版本漂移（dphuanjing py3.8 vs .venv py3.12 实测不一致）→ 折定义单一真源 `data/interim/stage13b_folds.json`（stage11 原版函数生成，跨环境只读）。
- 脚本：`scripts/stage13b_fold_retrain.py`（dphuanjing，断点续跑）、`scripts/stage13b_fold_eval.py`（.venv）；报告 `reports/gnn_embedding_foldb_eval.json`；闭卷资产 `gnn_emb_foldb_s*_f*.npy` + meta（不入 git，留作未来外部预训练的"同源 GNN"对照组）。

### 阶段 14：真实回测（2026-07-21 完成，exp_011 / D26）

对齐建议第 1 条落地：用 `实验ABCDEF.docx` 14 组已做真实实验回测当前路由模型，三方对照旧 GNN v5.3 当年预测与真实结果。

| 口径 | 旧 GNN v5.3 | 新路由树 |
|---|---|---|
| 6 组配对平均分（真实全失败） | 0.687 | **0.254** |
| Brier（真值=0） | 0.489 | **0.112**（4.4 倍改善） |
| 失败组 ≥0.5 虚高 | 5/7（0.59–0.82 全灭） | 2/12（A1/A8，与唯一相对成功组 A5 同组合） |
| A5（唯一 partial）名次 | 无当年预测 | **并列第 1/13**（TAPT+A6） |

- **单体解析 17/21**（化学名构建 SMILES + RDKit MW 校验 + 旧池 canonical 互证）；**H1–H4 待用户确认**（方案结构与记录 CAS 的 MW 冲突，H3：690.28 vs 918.37），确认前 C 与 G5 不出预测。
- 路由：生产 exact-match 与 canonical 判定 21 组合 100% 一致；已做实验全走 tree_v4 臂（无双未见）。
- **不是只会打低分**：方案 13 组合得分 0.031–0.699（8/13 ≥0.5）；低分集中于 TFPT+B5/B4/B3/B6、TAPB+A7，SHAP 显示主导为 TE 文献先验（B5 −0.573、A7 −0.590）——文献独立看衰与用户失败互证。
- 产物：`reports/real_backtest.md/.json`、`real_backtest_monomers.json`、`real_backtest_predictions.json`、4 个 stage14 脚本；`data/experimental_refs/`（两份 docx 副本，ABCDEF 9.6MB 不入 git）。

### 阶段 15：P2 三件套——打分 + 不确定性 + OOD（2026-07-21 完成，exp_012 / D27）

**不确定性（bagging）**：v4_mild 配置 5 种子 [42,123,7,2026,555] 集成，双臂切换为 `models/tree_v4_ens.pkl`（池内，含 TE）/ `tree_v4_noTE_ens.pkl`（外推）；预测 mean ± std（std=认知不确定度）；`TreeFilmPredictor` 自描述 "ensemble" 键扩展，单模型 pkl 向后兼容（std=0，SHAP 归因用成员[0]）。冷启动 3.4s。

**集成 CV 验证**（`reports/stage15_ensemble_cv.json`）：LOGO 两臂不低于单模型（0.8784→0.8853 / 0.7900→0.7911）；双留出 3 划分种子复核，8 格 7 格 ≥成员均值、与 s42 差异 ≤0.011；v4/双留出 s42 −0.047 为 seed42 单模型偏运气单点（该划分成员均值 0.609）——按"不低于单模型平均水平"口径通过，如实披露。

**OOD 三级制**（`src/predictor/ood.py`，none/warning/out + 中文原因）：
- a. 官能团适配性 → **out**：胺侧酰肼/肼/羟胺（H3 案例：腙键非亚胺键）、无伯胺/仲胺、醛侧无醛基；
- b. 双未见新颖性 → **warning**（与 noTE 外推臂联动）；
- c. 特征区域漂移 → **warning**：16 项关键特征超训练 5%–95% 包络 >10%（`models/feature_envelope.json`，入库；对应 fold3 诊断）。

**App/报告**：「成膜概率」→「成膜打分（倾向性）」+ 语义说明（论文口径）；± std 展示；warning ⚠️ 黄条照出分、**out ⛔ 红条不出分**（GNN 同挂）且不出打分理由；Word 报告加 ± std 与 OOD 说明段。`FilmPredictor.predict` 新增 `score_std` / `ood`。

**验证**：TFPT+H3 → out、TAPT+A2/Tp+Pa → none、虚构双未见大芳香 → warning；新增 `tests/test_ood_ensemble.py`（20 个）；**pytest 77 passed**（57+20）。

### 下一步

**阶段 14 候选（2026-07-21 收官刷新）：**

1. **fold3 型"区域漂移大单体"专项切片（任务 2，第一优先）**：闭卷已证实 fold3 对树/GNN/embedding 全线坍缩（~0.36）——做 raw-feature 包络外样本的检测与单独计分（逐折标配已就位，切片挂在 fold_summary 最难折上），目标是"识别出这类单体并降级/提示"，比硬提表征更实际。
2. **外部数据预训练表征（可选立项）**：与本数据无重叠的预训练模型（MolFormer/ChemBERTa 类），用阶段 B 同一闭卷管线直接对比（同源 GNN 对照组已就位）。成本与收益需先评估。
3. **评估与产品化遗留观察点**：双未见 v4/noTE 两协议冲突（噪声量级，表征升级后再复测）；A 混合桶 MAE −0.0075（D23 复盘点，待 App 日志）；实验 D 条件泄漏 0.9112 诊断（一直挂着）。
4. **工程增强（非阻塞）**：案例库自动提取、GNN 免 subprocess 直接导入、LightGBM 对比、仅二聚体 3D 消融（D19 复盘点）。

> 已关闭：GNN embedding 迁移（exp_010 闭卷证伪：mono_emb、pair_emb 同源均无效）、routed_strict 切换评审（D23）、双留出逐折报告标配化（D23）、Morgan/MACCS 指纹入模（exp_006）、概率校准（证伪）。
> 待用户输入：H1–H4 全氟链酰肼结构确认（D26，确认后补 C/G5 回测——将自动走 OOD out 红牌路径，D27 闭环场景）。
> 已完成：OOD/不确定性正式输出（D27，exp_012）。

---

## 六、阻塞点 / 待决策（BLOCKERS）

暂无阻塞。待决策：
- ~~routed_strict 切换评审~~ **已复盘关闭（D23，2026-07-20）**：已切换为仅双未见走 noTE；遗留观察项：A 混合桶 MAE −0.0075、双未见真实占比与 noTE 臂 MAE 兑现
- ~~双未见单体泛化的表征升级路线~~ **已证伪关闭（D25，2026-07-21）**：GNN embedding 迁移闭卷 −0.034（逐种子全负），阶段 A 增益为配对标签记忆；剩余方向：外部预训练 / 配对级手工表征 / fold3 切片（见下一步）
- 双未见场景 v4 vs noTE 两协议方向冲突（双留出网格 noTE 胜、LOGO 胺未见桶 v4 胜，均在噪声量级）：表征升级后两协议同时复测（若无新表征则维持 noTE）
- 是否立项外部数据预训练表征（MolFormer/ChemBERTa 类，与本数据无重叠天然闭卷；阶段 B 管线可直接复用）
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
