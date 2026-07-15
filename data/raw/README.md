# 数据来源说明

> 本目录（`data/raw/`）存放的数据均从旧项目 `C:\Users\ckx\Desktop\tianxuan seek` 复制而来。
> 承诺：**我们只复制旧项目数据，绝不修改旧项目中的任何文件。**

---

## 已复制文件

| 文件名 | 来源路径 | 复制日期 | 说明 |
|---|---|---|---|
| `v5_train_stage1.csv` | `tianxuan seek/data/processed/v5_train_stage1.csv` | 2026-07-07 | 主训练集，6,201 行，含反应条件字段 |
| `merged_monomer_pool.csv` | `tianxuan seek/data/processed/merged_monomer_pool.csv` | 2026-07-07 | 单体池（醛 + 胺 SMILES） |
| `v5_train_stage1_aug_v2.csv` | `tianxuan seek/data/processed/v5_train_stage1_aug_v2.csv` | 2026-07-07 | 增广训练集（可选，用于阶段 4 对比） |

## 数据说明

- `v5_train_stage1.csv` 是旧项目清洗后的主训练集，标签为四分类连续值：
  - 1.0：文献确认连续成膜
  - 0.8：粉末合成成功但非连续膜（弱正）
  - 0.7：增广正样本
  - 0.0：负样本（化学规则生成 + 文献内负样本）
- 数据包含 16 列，其中有 6 个反应条件字段（温度、溶剂、催化剂、当量比、合成路线、界面类型）。

## 后续处理

- `data/interim/`：存放清洗/结构补全后的中间数据
- `data/processed/`：存放最终建模特征矩阵
- 所有处理脚本在新工作台 `src/data/` 下维护，不依赖旧项目路径。
