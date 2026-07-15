# 数据审计报告

## 1. 基本信息
- 样本数：6201
- 字段数：16
- 所有必需字段均存在

## 2. 缺失率
- paper_id: 0.00%
- group_id: 86.32%
- source_db: 0.00%
- aldehyde_smiles: 0.03%
- amine_smiles: 0.03%
- aldehyde_name: 38.67%
- amine_name: 50.86%
- stoichiometry: 86.92%
- solvent: 86.71%
- temperature: 87.28%
- catalyst: 88.53%
- synthesis_route: 86.32%
- interface_type: 89.89%
- is_film: 0.00%
- film_quality: 88.42%
- original_is_film: 0.00%

## 3. SMILES 缺失
- 醛缺失：2
- 胺缺失：2
- 任一缺失：3 (0.05%)

## 4. 反应条件缺失率
- stoichiometry: 86.92%
- solvent: 86.71%
- temperature: 87.28%
- catalyst: 88.53%
- synthesis_route: 86.32%
- interface_type: 89.89%

## 5. 标签分布
- 0.0: 4173
- 0.7: 1308
- 0.8: 291
- 1.0: 429

## 6. 标签冲突（同组合+条件在不同文献标签不同）
- 冲突组数：0
- 冲突行数：0

## 7. 标签不一致（original_is_film vs is_film）
- 不一致行数：291 (4.69%)

## 8. 重复样本
- 总数：6201
- 去重后：6201
- 重复数：0

## 9. 异常值
- film_but_missing_smiles: 3
- temperature_parse_fail: 5604
- temperature_extreme: 2

## 10. 建议
- 部分反应条件缺失率较高，需评估是否仍能作为有效特征。
