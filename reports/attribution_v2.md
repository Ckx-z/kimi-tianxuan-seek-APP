# 模型 v2 归因报告

**模型**: `models/tree_v2.pkl`  
**PR-AUC**: 0.7484  
**MAE**: 0.2552  
**样本数**: 3094  
**特征数**: 122  

## 全局特征分组重要性

| 分组 | 累计 SHAP |
|---|---|
| interaction | 0.2617 |
| aldehyde | 0.2091 |
| amine | 0.1085 |
| rules | 0.0628 |

## Top-20 全局重要特征

| 排名 | 特征 | 分组 | 重要性 |
|---|---|---|---|
| 1 | int_hadamard_tpsa_per_site | interaction | 0.0581 |
| 2 | ald_n_aromatic_rings_per_site | aldehyde | 0.0511 |
| 3 | ald_aromatic_frac | aldehyde | 0.0243 |
| 4 | rule_C3对位(非间位) | rules | 0.0221 |
| 5 | amine_aromatic_frac | amine | 0.0181 |
| 6 | ald_mw | aldehyde | 0.0175 |
| 7 | ald_tpsa_per_site | aldehyde | 0.0162 |
| 8 | rule_C3邻位(禁) | rules | 0.0159 |
| 9 | ald_is_symmetric | aldehyde | 0.0151 |
| 10 | amine_tpsa | amine | 0.0140 |
| 11 | ald_has_heterocycle | aldehyde | 0.0124 |
| 12 | ald_logp | aldehyde | 0.0124 |
| 13 | amine_mw | amine | 0.0115 |
| 14 | ald_mw_per_site | aldehyde | 0.0106 |
| 15 | ald_n_rings_per_site | aldehyde | 0.0103 |
| 16 | rule_F_on_amine | rules | 0.0096 |
| 17 | amine_mw_per_site | amine | 0.0095 |
| 18 | pair_logp_diff | interaction | 0.0082 |
| 19 | amine_logp | amine | 0.0081 |
| 20 | ald_tpsa | aldehyde | 0.0080 |

## 代表性单体对归因

### Tp + Pa (经典成膜)
- 醛: `O=CC1=C(C=O)C(=O)C(C=O)=C1O`
- 胺: `Nc1ccc(N)cc1`

预测成膜得分: 0.983
主导贡献方: aldehyde（醛/胺）
该单体上的关键官能团: 醛基

**Top-5 正向特征：**
- ald_aromatic_frac (aldehyde): SHAP=0.0589, value=0.0000
- int_hadamard_tpsa_per_site (interaction): SHAP=0.0451, value=575.7576
- ald_n_aromatic_rings_per_site (aldehyde): SHAP=0.0337, value=0.0000
- amine_aromatic_frac (amine): SHAP=0.0235, value=0.7500
- rule_F_on_amine (rules): SHAP=0.0217, value=0.0000

**Top-5 负向特征：**
- int_ratio_logp (interaction): SHAP=-0.0161, value=-0.8525
- ald_is_symmetric (aldehyde): SHAP=-0.0140, value=1.0000
- int_ratio_n_rotatable (interaction): SHAP=-0.0041, value=0.0000
- int_diff_n_aromatic_rings_per_site (interaction): SHAP=-0.0034, value=-0.5000
- pair_tpsa_diff (interaction): SHAP=-0.0029, value=36.4700

### Tp + 含F二胺
- 醛: `O=CC1=C(C=O)C(=O)C(C=O)=C1O`
- 胺: `Nc1cc(F)cc(N)c1`

预测成膜得分: 0.641
主导贡献方: aldehyde（醛/胺）
该单体上的关键官能团: 醛基

**Top-5 正向特征：**
- ald_aromatic_frac (aldehyde): SHAP=0.0483, value=0.0000
- int_hadamard_tpsa_per_site (interaction): SHAP=0.0422, value=575.7576
- ald_n_aromatic_rings_per_site (aldehyde): SHAP=0.0299, value=0.0000
- rule_C3邻位(禁) (rules): SHAP=0.0160, value=0.0000
- int_hadamard_aromatic_frac (interaction): SHAP=0.0140, value=0.0000

**Top-5 负向特征：**
- rule_F_on_amine (rules): SHAP=-0.0598, value=1.0000
- rule_非对位(间位) (rules): SHAP=-0.0416, value=1.0000
- amine_mw (amine): SHAP=-0.0191, value=126.1340
- ald_is_symmetric (aldehyde): SHAP=-0.0157, value=1.0000
- rule_am_e_withdrawing (rules): SHAP=-0.0128, value=1.0000

### 苯甲醛 + Pa (C2+C3 拓扑不匹配)
- 醛: `O=Cc1ccccc1`
- 胺: `Nc1ccc(N)cc1`

预测成膜得分: 0.777
主导贡献方: aldehyde（醛/胺）
该单体上的关键官能团: 醛基, 苯环

**Top-5 正向特征：**
- int_hadamard_tpsa_per_site (interaction): SHAP=0.1345, value=444.1614
- ald_tpsa_per_site (aldehyde): SHAP=0.0408, value=17.0700
- amine_aromatic_frac (amine): SHAP=0.0335, value=0.7500
- int_ratio_n_aromatic_rings_per_site (interaction): SHAP=0.0229, value=2.0000
- ald_tpsa (aldehyde): SHAP=0.0207, value=17.0700

**Top-5 负向特征：**
- ald_n_aromatic_rings_per_site (aldehyde): SHAP=-0.0454, value=1.0000
- ald_aromatic_frac (aldehyde): SHAP=-0.0237, value=0.7500
- ald_mw_per_site (aldehyde): SHAP=-0.0139, value=106.1240
- ald_n_rings_per_site (aldehyde): SHAP=-0.0139, value=1.0000
- int_ratio_tpsa (interaction): SHAP=-0.0093, value=0.3280
