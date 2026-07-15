# 实验 [EXP_XXX] — [简短描述]

> 实验日期：YYYY-MM-DD
> 状态：进行中 / 已完成 / 已废弃
> 关联决策：DECISIONS DXX

---

## 目标

一句话描述本次实验要验证什么。

## 数据

- 数据集版本：`data/processed/xxx.csv`
- 样本量：XXX 行
- 特征数：XXX 维
- 标签分布：...

## 模型配置

```yaml
model:
  type: xgboost / lightgbm
  params:
    n_estimators: ...
    max_depth: ...
    learning_rate: ...
    # ...
features:
  use_conditions: true/false
  use_rules: true/false
  normalize: true/false
cv:
  method: leave_one_monomer_out / kfold_paper_id
  n_folds: ...
```

## 训练结果

| Fold | PR-AUC | NDCG@10 | Hit@5 | 备注 |
|---|---|---|---|---|
| 0 | 0.xxx | 0.xxx | 0.xxx | |
| 1 | 0.xxx | 0.xxx | 0.xxx | |
| ... | ... | ... | ... | |
| **mean±std** | **0.xxx±0.xxx** | **0.xxx±0.xxx** | **0.xxx±0.xxx** | |

## 消融对比

| 配置 | PR-AUC | NDCG@10 | 变化 |
|---|---|---|---|
| 完整（基线） | 0.xxx | 0.xxx | — |
| 去掉条件特征 | 0.xxx | 0.xxx | Δ=-0.xxx |
| 去掉规则特征 | 0.xxx | 0.xxx | Δ=-0.xxx |
| 不归一化描述符 | 0.xxx | 0.xxx | Δ=-0.xxx |

## SHAP 发现

- 全局 Top-5 重要特征：...
- 化学洞察：...
- 模型是否独立学到了规则模式？是/否

## 结论

一句话总结实验结论。

## 遗留问题 / 下一步

- ...
