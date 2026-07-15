# DATA_DICT — 数据字典

> 每个字段的定义、来源、已知问题。每次数据变更时更新。
> 最后更新：2026-07-02

---

## 一、主数据源：v5_train_stage1.csv

**来源**：旧项目 `tianxuan seek/data/processed/v5_train_stage1.csv`
**规模**：6,201 行 × 16 列
**生成过程**：1719 篇文献 → LLM(MiniMax M2.7) 提取 → 清洗去重 → 化学规则负样本生成

### 字段映射

| 列名 | 类型 | 含义 | 缺失情况 | 备注/已知问题 |
|---|---|---|---|---|
| `paper_id` | int | 文献 ID | 无 | ⚠️ CV 必须按此分层（防泄漏） |
| `group_id` | int | 文献内实验组 ID | 无 | 同文献多组独立实验 |
| `source_db` | str | 数据来源 | 无 | 如 v3_db_full |
| `aldehyde_smiles` | str | 醛单体 SMILES | **~29.5% 缺失** | 🔴 最大丢弃源，阶段2重点 |
| `amine_smiles` | str | 胺单体 SMILES | **~29.5% 缺失** | 🔴 同上 |
| `aldehyde_name` | str | 醛单体名称 | 少量 | 用于阶段2名称→SMILES补全 |
| `amine_name` | str | 胺单体名称 | 少量 | 同上 |
| `stoichiometry` | str | 当量比 | 较多 | ⭐ **反应条件特征**，如 "1:1 (Tp:Pa)" |
| `solvent` | str | 溶剂 | 少量 | ⭐ **反应条件特征**，如 "water/CH₂Cl₂" |
| `temperature` | str | 温度 | 少量 | ⭐ **反应条件特征**，需解析为数值，如 "室温(25°C)" |
| `catalyst` | str | 催化剂 | 较多 | ⭐ **反应条件特征**，如 "乙酸水溶液" |
| `synthesis_route` | str | 合成路线 | 较多 | ⭐ **反应条件特征**，如 interfacial |
| `interface_type` | str | 界面类型 | 较多 | ⭐ **反应条件特征**，如 liquid-liquid |
| `is_film` | float | **标签** | 无 | 四分类连续：1.0/0.8/0.7/0.0（见DECISIONS D04） |
| `film_quality` | str | 成膜质量 | 仅正样本有 | high/medium/low |
| `original_is_film` | int | 原始标签 | 无 | LLM 提取的原始 0/1，用于追溯 |

### 标签分布（旧项目统计）

| 标签值 | 含义 | 样本数 | 占比 |
|---|---|---|---|
| 1.0 | 文献确认成膜 | 429 | 6.9% |
| 0.8 | 粉末（弱正） | 291 | 4.7% |
| 0.7 | 增广正样本 | 1,308 | 21.1% |
| 0.0 | 负样本 | 4,173 | 67.3% |

⚠️ **注意**：这里"负样本占 67%"看似不缺负样本，但绝大多数是**化学规则合成**的（非文献真实负样本）。文献真实负样本极少 → 这正是用户问题 1 的本质（发表偏差）。

---

## 二、其他数据资产

### 单体池：merged_monomer_pool.csv
- **来源**：旧项目 `data/processed/merged_monomer_pool.csv`
- **内容**：醛 + 胺 SMILES 库（商业可购 + LLM 提取）
- **用途**：阶段 5 候选生成（推荐时从池中选组合）
- **已知问题**：待审计 SMILES 有效性

### 笛卡尔积配对：v4_cartesian_pairs.csv
- **来源**：旧项目 `data/processed/v4_cartesian_pairs.csv`（29MB）
- **规模**：~232,000 对
- **生成**：醛 433 × 胺 538，排除自配对和训练集配对
- **用途**：旧项目筛选阶段的候选池，新项目可参考

### 结构化 YAML：structured/、structured_v2/、structured_v3/ 等
- **来源**：旧项目 `data/structured*`（4 个版本，~955 篇文献）
- **格式**：每篇文献一个 YAML，含 21+ 字段
- **字段示例**：journal, system, reagent, catalyst, solvent, reaction_temperature, synthesis_route, interface_type, fluorine_monomer 等
- **已知问题**：⚠️ LLM 提取可能有噪声（用户问题 4），需审计置信度

---

## 三、待审计项（阶段 1 重点）

> 这些数字来自旧项目记录，新项目需独立验证。

- [ ] SMILES 缺失率是否仍为 29.5%？
- [ ] 反应条件 6 个字段的各自缺失率是多少？
- [ ] 多篇文献报告同一组合时，标签冲突率是多少？
- [ ] `original_is_film` 与 `is_film` 不一致的样本有多少？（量化标签噪声）
- [ ] 笛卡尔积配对中，跨实验体系假配对的比例？（旧项目已知问题）
- [ ] 温度字段格式混乱程度（"室温25°C" / "120°C" / "ambient" 等）

---

## 四、新项目数据流（规划）

```
旧项目 v5_train_stage1.csv
        │
        ▼  [阶段1: 审计]
data/raw/ (复制或软链接)
        │
        ▼  [阶段1: 清洗]
data/interim/ (修复SMILES、解析条件、去重、审计报告)
        │
        ▼  [阶段2: 结构补全]
data/interim/ (名称→SMILES映射，补全缺失结构)
        │
        ▼  [阶段3: 特征工程]
data/processed/ (分子描述符+反应描述符+条件描述符+规则向量)
        │
        ▼  [阶段4: 建模]
models/ (训练好的树模型) + EXPERIMENTS/ (实验记录)
        │
        ▼  [阶段5: 推荐]
app/ (实验指导工具，输入组合+条件→打分排序+SHAP理由)
```

---

## 五、置信度体系（解决用户问题4，规划）

为每个样本标注：

| 字段 | 取值 | 含义 |
|---|---|---|
| `evidence_type` | explicit/table/inferred | 原文明确陈述/表格数据/推断 |
| `confidence` | high/medium/low | 标签置信度 |
| `source_snippet` | str | 原文片段（可回溯） |

高置信度用于训练，低置信度单独评估或人工抽检。
