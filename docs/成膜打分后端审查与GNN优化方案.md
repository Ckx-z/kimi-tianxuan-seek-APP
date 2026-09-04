# 成膜打分模块后端审查报告 + GNN 优化方案

> 产出日期：2026-09-04
> 范围：数据 → 特征 → 模型 → 推理 → 后处理全链路审查；GNN 优先。
> 性质：**诊断与方案设计**（本次不改业务代码）。所有结论均基于代码实读
> 与 2026-09-04 真机实测（dev 后端 8001，tree_v4_ens + gnn_v5.3）。

---

## 1. 审查总结

### 1.1 实测证据（问题复现）

| 单体对 | tree | GNN | 主分（max 融合） | OOD | 化学预期 |
|---|---|---|---|---|---|
| 苯甲醛 + 苯胺（单官能×单官能） | 0.447 | **0.647** | **0.647** | none | 极难成膜（只能形成端基席夫碱小分子，无法成网成膜） |
| 苯甲醛 + 对苯二胺 | 0.694 | 0.463 | **0.694** | none | 仍难（醛单官能） |
| 苯甲醛 + 乙二胺（脂肪胺） | **0.778** | 0.490 | **0.778** | none | 应接近 0 |
| Tp（三醛）+ 对苯二胺（经典良对） | 0.821 | 0.598 | 0.821 | none | 高 ✓ |

结论：**不是单一模型的问题，而是「特征口径 + 标签数据 + max 融合」三处缺陷叠加**，
且两模型在简单分子对上都存在系统性高估（GNN 在苯甲醛+苯胺 0.647、
树模型在苯甲醛+乙二胺 0.778）。

### 1.2 数据预处理问题

1. **训练集含「化学上不可能成膜」的样本且标注为成膜**：
   `data/interim/v5_train_stage1_cond_filled.csv` 中苯甲醛（单官能醛，
   只能形成端基亚胺，不能成网）出现 6 条、成膜率 0.533；苯胺 17 条、
   成膜率 0.376。这些「成膜」标签大概率是标注噪声（苯甲醛在文献里作为
   反应物出现但不可能成 COF 膜）——**模型从这些样本学到「小芳醛/芳胺
   可以成膜」的错误先验**。
2. **硬负样本缺失**：is_film 分布为 0→4173（67%）、0.7→1308、0.8→291、
   1.0→429。负样本虽多，但都是「文献里做了且失败的复杂单体对」；
   **没有「化学上显然不可成膜」的简单分子对**（苯甲醛+苯胺 0 条、
   苯甲醛+乙二胺 0 条），模型从未被要求对"结构简单/低交联度"输出低分。
3. **标签语义混杂**：is_film ∈ {0, 0.7, 0.8, 1.0}——把「成膜质量等级」
   直接当回归目标，模型输出被 clip 到 [0,1] 当概率用，无校准
   （无 Platt/Isotonic）。
4. **特征口径的 per-site 归一化制造假阳性**：
   `src/features/descriptors.py` 的 `mw_per_site / n_aromatic_rings_per_site /
   n_rings_per_site` 系列——苯甲醛 1 反应位点 + 1 芳环 → `rings_per_site=1.0`，
   与训练池高成膜单体的典型值一致 → 树模型把它学成强正特征。真机 SHAP
   证实：苯甲醛+苯胺的推高特征正是 `int_ratio_n_aromatic_rings_per_site`、
   `int_hadamard_ring_frac` 等芳香性比值特征。
5. **无「交联度」特征/规则**：COF 成膜的必要条件是**至少一方单体官能度
   ≥3 或双方 ≥2**（能成 2D 网络）。当前特征里没有"能否成网"的显式
   表达；OOD 官能团检查只验"存在醛基/伯胺"（苯甲醛 1 醛基、苯胺 1 胺基
   → 判 none），不验数量。

### 1.3 模型架构问题

- **GNN（gnn_v5.3，旧项目 `src/screening/gnn_v4/`）**：Siamese GIN+GINE
  编码器（3 层、hidden 128、dropout 0.15）+ CrossGraphAttention（4 头）
  + PairPooling（4 queries）+ 可选 3D 构象分支 + FilmHead（拼接规则向量）。
  架构本身**合理、规模适中**——问题不在架构，在输入特征与标签
  （见 1.2）。原子 37 维特征含元素 one-hot(10)、芳香性、杂化、醛碳/胺氮
  标记、BFS 反应位点距离编码；**缺形式电荷、缺全局"位点数/交联度"**；
  芳香性由键型反推（sanitize=False 时偶发 kekulize 失败，训练日志有
  记录）。
- **树模型（tree_v4_ens / tree_v4_noTE_ens）**：XGBoost 回归（500 树、
  depth 5），特征 = RDKit 描述符 + hard_rules 规则向量 + 醛胺 Hadamard
  交互 + TE 历史成膜率 + 指纹；tree_v4 有 TE 先验、noTE 无。回归目标为
  0/0.7/0.8/1.0 混合标签，本质是"质量分级"而非概率。
- 两模型都**无概率校准**、输出直接当概率比较。

### 1.4 训练策略问题

- GNN v5 已用 **Focal Loss + quality_weight**（`v4_trainer.py` /
  `v4_loss.py`），训练侧损失设计已较先进；**但 Focal 救不了"数据里没有
  硬负样本"**——损失再会加权，也无法凭空学到"简单分子对=低分"。
- 数据增强存在（`v5_train_stage1_aug*.csv`）；MC Dropout 推理不确定度已
  实现（`--mc`）。
- 树模型按 LeaveOneGroupOut（按单体留一）评估，但**未见"组合级"
  留出**（新单体组合的泛化）——苯甲醛+苯胺这种"两单体都见过、组合
  没见过"的场景正是盲区。

### 1.5 推理 / 后处理问题

1. **融合策略 `max(tree, gnn)`（`api/deps.py` 的 `headline_score`，D29
   「乐观召回」口径）是高分现象的直接放大器**：任一模型高估即抬升主分，
   两个模型的高估互相接力（苯甲醛+苯胺靠 GNN 0.647、苯甲醛+乙二胺靠
   tree 0.778）。`FilmPredictor.ensemble_probability` 内部用的是均值，
  但 API 层用 max 覆盖——两处口径不一致。
2. **OOD 红线缺失**：`src/predictor/ood.py` 只做官能团存在性 + novelty +
   特征漂移检查，没有「低交联度 / 单官能」红线 → 苯甲醛类输入完全放行。
3. **novelty 路由盲区**：苯甲醛、苯胺都在 monomer_pool 里 → 路由
   `in_pool` 走 tree_v4（TE 先验 0.533），但该**组合从未出现在训练集**，
   路由按"单体是否见过"而非"组合是否见过"决策。
4. **未见组合的 GNN 外推无保护**：GNN 对训练集外的组合直接输出 0.647，
   无"外推不确定性"闸门（MC std 0.043 很小，模型自以为很有把握）。

### 1.6 最可能造成苯甲醛/苯胺高分的原因（排序）

1. **训练数据污染 + 硬负样本缺失**（根因）：苯甲醛/苯胺被标注过"成膜"，
   且没有"简单分子对必须低分"的样本 → 两模型都学到错误的先验。
2. **特征口径把"苯环密度"当正信号**：per-site 归一化让单官能芳醛的
   `rings_per_site=1.0` 与高成膜单体对齐；GNN 外推时主要靠芳香性/环特征
   → 0.647。
3. **max 融合**：让任一高估者定主分（放大器，非根因）。
4. **OOD/规则层无交联度红线**：单官能×单官能本应被直接拦下，但没有。

---

## 2. GNN 模型改进方案（详细）

> 原则：架构不动大手术（现有 Siamese GIN+GINE + CrossAttention 已够用），
> **优先修数据与特征，其次训练与融合，最后才考虑换架构/预训练**。

### 2.1 数据增强 / 扩充建议

| 项 | 做法 | 优先级 |
|---|---|---|
| 硬负样本合成 | 规则生成器 `make_hard_negatives()`：单官能醛×任意胺、任意醛×单官能胺、无醛基/无胺基分子、烷烃/链状脂肪分子对、大位阻组合（邻位双取代），标注 is_film=0，约 300–500 条注入训练集 | P0 |
| 标签审计 | 人工复核"化学上不可能成膜却 is_film>0"的样本（重点：苯甲醛 6 条、苯胺 17 条、所有 min(官能度)<2 的对），修正为 0 | P0 |
| 标签口径统一 | 决定 is_film 语义：保留四档但训练时**分两类损失**（成膜与否 BCE + 质量回归仅在正样本上），或直接二值化（≥0.7→1）重训 | P1 |
| 组合级留出 | 评估/训练 split 增加「pair 组合未见」桶（单体见过但组合未见），专门监测外推表现 | P0 |
| SMILES 标准化 | 推理与训练统一：去盐（largest fragment）、电荷中和、互变异构/立体异构 canonical、去除溶剂杂质（RDKit `SaltRemover` + `CanonSmiles`） | P1 |

### 2.2 特征工程改进

树模型（`src/features/`）：

- 新增**成网能力特征**：`min_functionality = min(n_sites_ald, n_sites_amine)`、
  `max_functionality`、`can_network = (ald_sites>=2 and amine_sites>=2) or
  (ald_sites>=3 and amine_sites>=1) or (amine_sites>=3 and ald_sites>=1)`
  （0/1 规则特征直接喂模型）。
- 修正 per-site 比值的坑：`n_aromatic_rings_per_site` 保留但**同时保留
  绝对环数与位点数的原值**，避免"1 位点 1 环"与"3 位点 3 环"同值；
  删除或降权 `int_ratio_n_aromatic_rings_per_site` 这类比值交互。
- 可选反应性描述符：Fukui/pKa 计算成本高，先上**便宜代理**——氢键供受体
  数、HOMO-LUMO 半经验估计（复用 DFT 模块的 gfnff 单点，秒级）、
  醛的 α-位取代数（位阻代理）。

GNN（旧项目 `src/screening/gnn_v3/featurizer.py`）：

- 原子特征加：形式电荷 one-hot、是否在芳香环（已有）、是否杂原子、
  **到最近反应位点的距离分桶**（已有 BFS 距离，扩展为分桶）。
- **图级全局特征（虚拟节点 / global node）**：`n_reactive_sites`、
  `n_aromatic_rings`、`n_heavy_atoms`、`ring_frac`、`can_network` 作为
  全局节点与所有原子相连（1 层即达），让 GNN 显式看到交联度——这是
  "苯甲醛类低分"最关键的一根杠杆。
- 边特征保持（键型/共轭/环），可加键长（需 3D，v5.3 已有 3D 分支可选）。

### 2.3 模型架构调整（按收益排序，均非必须）

1. **全局虚拟节点**（强烈建议，改动小）：见 2.2。
2. 层数 3→4、hidden 128→192 的小幅加大（配硬负样本后重训验证）。
3. 若重训后仍不满意：替换聚合为 **Set2Set/GlobalAttention** 或换
   **GINE+edge-feature 的 GIN-conv**（已在用）；再不行才考虑
   **预训练迁移**——优先 MolCLR（2D 图自监督，与现有 PyG 栈兼容，
   可在 dphuanjing py3.8 环境跑）；GROVER 参数大、依赖重，仅当 MolCLR
   收益不足时评估。
4. 3D 分支保留（`use_3d` 已有）；对未见组合可**关闭 3D/规则分支重估一次
   做分歧检测**。

### 2.4 训练策略优化

- Focal Loss 已就位（`v4_loss.py`），保留；**增加组合级加权**：对
  `min_functionality<2` 的合成负样本给 weight×2，强化"低交联度→低分"。
- 早停 + 验证集按「单体留一 + 组合未见」双桶监控（避免只在单体留一上
  过拟合）。
- 训练数据 = 清洗后原集 + 硬负样本 + 现有 aug（去掉与硬负样本重复的）。
- 输出校准：训练后用 **Isotonic/Platt** 在验证集上校准（保存 calibrator
  进 checkpoint，推理时套用）；MC Dropout std 继续输出。
- 交叉验证：5 折按单体分组；超参用 Optuna（学习率/dropout/层数/头数，
  约 20–40 次试验量级）。

### 2.5 融合策略调整（快速且高收益）

`api/deps.py::headline_score` 与 `src/predictor/__init__.py::predict` 两处：

```
if ood_out: score=None
elif not can_network(pair): score=clip(min(tree,gnn), 0, 0.25)   # 红线
elif |tree-gnn| > 0.25: score=0.5*min+0.5*mean; flag="模型分歧大"
else: score=mean(tree,gnn)                                        # 或 0.6*mean+0.4*min
```

- **放弃 max 乐观口径**（或仅在"明确高官能度对"上保留 max 作为召回档）；
  默认改**均值 + 分歧保守收缩**。
- 中期（阶段二）：**Stacking 元学习器**——特征 = [tree, gnn, tree_std,
  gnn_std, min_functionality, can_network, ood_flags]，标签 = 清洗后
  is_film，训练一个浅层 LightGBM 融合器，替代手工规则。
- GNN 不可用时维持 tree-only（现有降级逻辑保留）。

### 2.6 推理与监控

- 输入有效性检查：单官能/无官能/解析失败 → 低分 + OOD 提示
  （`ood.py` 增加 `networkability` 检查项）。
- 预测日志（`data/prediction_log.jsonl`）已全量落盘 → 写
  `scripts/eval_film_scoring.py` 定期对金标准集重算指标，与实验记录
  outcome 做滞后比对（记录转正时对账）。

### 2.7 预期改进效果

- 苯甲醛/苯胺、苯甲醛/乙二胺类：主分 ≤0.25 且带"低交联度"提示
  （红线直接拦截，不依赖模型）。
- 金标准坏样本集 top-1 排序错误消除；好样本（Tp+Pa 等）分数降幅
  ≤0.05（回归护栏）。
- 未见组合的 GNN 外推 std 增大（虚拟节点 + 硬负样本后模型对
  "没见过"更保守）→ 分歧标注触发率上升、误导性高分下降。

---

## 3. 实施步骤

### 阶段一（快速修复，约 1–2 天，不动训练）

1. `src/predictor/ood.py`：新增 `networkability` 检查（min 官能度/成网
   判定），命中 → ood.level=warning + 主分钳制 ≤0.25 + 前端提示。
2. `api/deps.py::headline_score`：max → 均值/保守规则（见 2.5），同步
   `app/gradio_app.py` 口径（注释已声明需同步）。
3. GNN 外推闸门：pair 组合未见（novelty 明细可判）时 gnn 分 ×0.8 收缩
   并附注。
4. 补回归测试 + 金标准冒烟（苯甲醛×苯胺/乙二胺 ≤0.25，Tp+Pa 不降）。

### 阶段二（深度优化，约 1–2 周）

5. 训练数据清洗与硬负样本生成（脚本 `scripts/make_hard_negatives.py`
   + `scripts/audit_film_labels.py`），产出 v6 训练集。
6. 树模型：新增成网特征重训 tree_v5（含 TE/fp 配置），评估对比 v4。
7. GNN v5.4：全局虚拟节点 + 增强原子特征 + 组合级加权 Focal → 重训 →
   Isotonic 校准入 checkpoint；更新 `predict_pair.py` 输出校准分。
8. 融合：Stacking LightGBM（阶段二末，若手工规则已达标可延后）。
9. 全链路测试 + 打包（GNN 仍走 subprocess，无打包影响）。

### 阶段三（持续迭代）

10. `scripts/eval_film_scoring.py`：金标准集（人工标注 ≥30 对）+ 月度
    重算 + 与实验记录 outcome 对账。
11. 预训练迁移评估（MolCLR）；贝叶斯超参（Optuna）例行化。
12. 在线监控：预测日志新增 `policy/flags` 字段，统计分歧率与红线触发率。

---

## 4. 验证方案

### 4.1 金标准测试集

- `data/film_gold_standard.json`：人工标注 30–50 对，分三类——
  A 明确可成膜（Tp+Pa、TFPT+BD、三醛×二胺等）、B 边界（双官能但位阻/
  柔性链）、C 明确不可成膜（单官能×单官能、无官能、脂肪链小分子、
  大位阻邻位对）。标签为 {0, 0.5, 1} 三档。

### 4.2 评估指标

| 指标 | 目标 |
|---|---|
| C 类 max score | **≤0.25**（红线后应全部命中） |
| A 类 min score | ≥0.60（旧版 0.82 的下降 ≤0.05 为回归护栏） |
| 排序 Spearman（A+B+C 全量） | ≥0.85（当前实测苯甲醛+苯胺 0.647 超过部分 B 类，排序明显错） |
| 分歧标注触发率 | 对未见组合上升、对池内组合不误报 |
| MAE（对 0/0.5/1 金标准） | ≤0.20 |

### 4.3 回归测试

- 现有 pytest 全量（895 passed 基线）保持通过；新增
  `tests/test_film_scoring_redline.py`（红线/融合策略单测）。
- 历史批次排序稳定性：`data/prediction_log.jsonl` 最近 N 条重放，A 类
  对排名不后移超过 2 位。
- 前端行为：OOD 提示、分数来源标注、分歧标注文案不破页。

---

## 5. 需要检查的文件 / 函数清单（审查落点）

| 层 | 文件 / 函数 |
|---|---|
| 数据 | `data/interim/v5_train_stage1_cond_filled.csv`（标签分布、苯甲醛/苯胺样本）、`data/raw/v5_train_stage1.csv`、旧项目 `data/processed/v5_train_stage1_aug*.csv`、`scripts/stage12_train_noTE.py`（monomer_pool 生成） |
| 树特征 | `src/features/descriptors.py::compute_pair_features`（per-site 比值）、`src/features/hard_rules.py::get_rule_vector`、`src/features/target_encoding.py::apply_film_rates`、`src/features/interaction.py`、`src/features/fingerprints.py` |
| 树模型 | `src/predictor/tree_model.py::TreeFilmPredictor`、`src/predictor/routing.py::RoutedTreePredictor`（novelty 路由） |
| GNN | 旧项目 `src/screening/gnn_v3/featurizer.py::mol_to_graph`（37 维原子特征）、`gnn_v4/{model,encoder,attention,pooling,heads}.py`、`v4_trainer.py`（Focal）、`v4_loss.py`、`scripts/train_v4.py`、`predict_pair.py`、`models/v5.3/v5_model.pt` |
| OOD/融合 | `src/predictor/ood.py::check_ood`（缺 networkability）、`api/deps.py::headline_score`（max 口径）、`src/predictor/__init__.py::FilmPredictor.predict`（mean 口径，与 deps 不一致）、`app/gradio_app.py`（需同步） |
| 归因 | `models/attribution.py::explain_pair_for_app`（SHAP 推/拉特征） |

---

## 附：GNN 关键伪代码（v5.4 改动示意）

```python
# 1) 全局虚拟节点（featurizer / collate）
def mol_to_graph(mol, role):
    x = stack([_atom_features(a, role, ...) for a in atoms])   # 37 维
    edge_index, edge_attr = bonds(mol)
    g = {
        "n_sites": count_reactive_sites(mol, role),   # 位点数
        "n_aromatic_rings": CalcNumAromaticRings(mol),
        "ring_frac": ring_atoms / n_atoms,
        "can_network": functionality_flag(mol, role),
    }
    # 虚拟节点：与全图相连
    x = cat([x, zeros(1, 37 + DIM_GLOBAL)])           # 或 concat 全局向量
    x[0, 37:] = encode(g)                             # 全局特征挂在虚拟节点上
    edge_index += [(0, i) for i in 1..n]
    return Data(x, edge_index, edge_attr)

# 2) 组合级加权 Focal（训练）
def group_weight(pair):
    f = min(n_sites_ald, n_sites_amine)
    return 2.0 if f < 2 else (1.5 if f < 3 else 1.0)

loss = FocalLoss(logits, label, quality_weight * group_weight(pair))

# 3) 融合（api/deps.py 替代 headline_score）
def headline_score(tree, gnn, ood, can_network, t_std, g_std):
    if ood.level == "out": return None
    if not can_network: return min(tree, gnn) * 0.5          # 红线收缩
    if abs(tree - gnn) > 0.25: return 0.5*min+0.5*mean       # 分歧保守
    return mean(tree, gnn)
```
