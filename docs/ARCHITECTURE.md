# 项目架构

> 代码组织、数据流、模块职责。
> 最后更新：2026-07-02

---

## 一、目录职责

```
src/
├── data/          数据摄入、审计、清洗、结构补全
│   ├── ingest.py      从旧项目导入 v5_train_stage1.csv
│   ├── audit.py       数据审计（覆盖率/缺失/冲突/异常）
│   ├── clean.py       清洗（SMILES修复、条件解析、去重）
│   └── smiles_map.py  名称→SMILES 映射补全
├── features/      特征工程（描述符流水线）
│   ├── molecular.py   分子结构描述符（复用旧项目 src/chemistry/）
│   ├── conditions.py  反应条件描述符（新）
│   ├── interactions.py 交互特征
│   ├── rules.py       规则向量（复用旧项目 hard_rules.py）
│   └── pipeline.py    特征拼装流水线
│
├── models/        模型训练、评估、解释
│   ├── train.py       训练（XGBoost/LightGBM）
│   ├── evaluate.py    评估（留一单体法、NDCG/Hit@K）
│   ├── explain.py     SHAP 解释
│   └── baseline.py    规则基线 + GNN 基线对接
├── recommend/     推荐核心
│   ├── scorer.py      打分（组合+条件→概率）
│   ├── ranker.py      排序（多样性约束）
│   └── guide.py       实验指导（理由+相似案例）
├── utils/
│   ├── chem.py        化学工具（RDKit 封装）
│   ├── viz.py         可视化（分子结构图、SHAP 图）
│   └── io.py          数据读写
```

---

## 二、数据流

```
┌─────────────────────────────────────────────────────────────┐
│ 旧项目 tianxuan seek/ (只读)                                  │
│   data/processed/v5_train_stage1.csv  ────┐                  │
│   src/chemistry/*.py                    ├── 复用             │
│   models/v5.0/v5_model.pt               │                    │
└──────────────────────────────────────────┼────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────┐
   阶段1            │ src/data/ingest.py + audit.py              │
   数据审计          │ → data/raw/ (导入)                          │
                    │ → data/interim/ (清洗后)                    │
                    │ → DATA_DICT.md (字段审计结果)                │
                    └──────────────────────┬──────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────┐
   阶段2            │ src/data/smiles_map.py                     │
   结构补全          │ 名称→SMILES (PubChem/单体库)                │
                    │ → data/interim/ (结构补全后)                │
                    └──────────────────────┬──────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────┐
   阶段3            │ src/features/pipeline.py                   │
   特征工程          │ 分子描述符 + 条件描述符 + 规则向量            │
                    │ → data/processed/ (特征矩阵)                │
                    └──────────────────────┬──────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────┐
   阶段4            │ src/models/train.py + evaluate.py          │
   建模              │ XGBoost/LightGBM + 留一单体 CV + SHAP       │
                    │ → models/ (权重) + EXPERIMENTS/ (记录)      │
                    └──────────────────────┬──────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────┐
   阶段5            │ src/recommend/ + app/                      │
   推荐/工具         │ 输入组合+条件 → 打分+排序+理由+相似案例       │
                    └─────────────────────────────────────────────┘
```

---

## 三、关键设计决策

### 1. 旧项目代码复用方式

**不复制整个旧项目，只复用关键模块**，通过以下方式之一：
- **方式 A（推荐）**：把旧项目 `src/chemistry/` 加入 `sys.path`，import 使用
- **方式 B**：把需要的函数复制到新项目 `src/features/`，独立维护

复用的代码：
| 旧项目文件 | 新项目用途 | 复用方式 |
|---|---|---|
| `src/chemistry/linker_analyzer.py` | 26维分子描述符 | A 或 B |
| `src/chemistry/hard_rules.py` | 34维规则向量 | A 或 B |
| `src/chemistry/conformer.py` | 10维单体3D | A 或 B |
| `src/chemistry/dimer.py` | 10维二聚体3D | A 或 B |

### 2. CV 分层（防止数据泄漏）

```python
# 禁止：随机 K-fold（同文献/同单体会跨 fold）
# 必须：按单体分组的留一法
from sklearn.model_selection import LeaveOneGroupOut
# group = 单体ID（醛或胺），每次留出一个单体的所有配对
```

### 3. 特征归一化（解决相似度退化）

```python
# 禁止：绝对量特征
n_aromatic_rings  # 小单体天然少

# 推荐：归一化特征
aromatic_rings_per_site = n_aromatic_rings / n_reactive_sites
mw_per_site = molecular_weight / n_reactive_sites
ring_size_ratio = ald_rings / amine_rings
```

### 4. SHAP 解释层

每个推荐输出：
```
推荐 #1: Tp + Pa-1，条件=室温/水-DCM/乙酸界面法
  打分: 0.87
  Top-3 理由（SHAP）:
    + C3+C2 拓扑匹配（+0.21）
    + 界面法适合此组合（+0.15）
    + 芳环数适中（+0.11）
    - 邻位位阻略高（-0.04）
  相似成膜案例: 文献 #101（同组合不同条件成膜）
```

---

## 四、配置管理

配置文件放 `configs/`，用 YAML：
```yaml
# configs/default.yaml
model:
  type: xgboost  # 或 lightgbm
  params: {...}
features:
  use_conditions: true  # D03
  use_rules: true       # D05
  normalize: true       # 解决相似度退化
cv:
  method: leave_one_monomer_out
data:
  source: ../tianxuan seek/data/processed/v5_train_stage1.csv
```

---

## 五、测试策略

- `tests/test_smiles.py`：SMILES 解析和修复
- `tests/test_features.py`：描述符计算正确性
- `tests/test_cv.py`：CV 无数据泄漏
- `tests/test_explain.py`：SHAP 输出合理性

---

## 六、旧项目可复用资产速查（完整清单见 PROJECT_STATE.md）

### 代码（生产就绪）
- `src/screening/gnn_v3/featurizer.py` — SMILES→图
- `src/screening/gnn_v4/model.py` — V4Model（GNN 主模型，对比基线）
- `src/chemistry/*.py` — 化学描述符（4 个文件）
- `src/chemistry/negative_sampler.py` — 负样本生成
- `scripts/train_v4.py` — GNN 训练脚本（参考）

### 数据
- `data/processed/v5_train_stage1.csv` — 主训练集 6201 行
- `data/processed/merged_monomer_pool.csv` — 单体池
- `data/structured*/` — 结构化 YAML（955 篇）

### 文档/知识
- `CLAUDE.md` — 旧项目完整规划（336 行，参考）
- `jiyi/` — 项目记忆/决策记录
- `graphrag图谱辅助/graph.gml` — 知识图谱
