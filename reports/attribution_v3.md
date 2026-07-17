# 模型 v3 归因报告

**模型**: `models/tree_v3.pkl`  
**PR-AUC**: 0.7785  
**MAE**: 0.2344  
**样本数**: 3094  
**特征数**: 142  
**特征开关**: {'use_rules': True, 'reduced_rules': True, 'use_interaction': True, 'use_3d': True, 'use_dimer': False, 'n_confs': 5}  

## 全局特征分组重要性

| 分组 | 累计 SHAP | 占比 |
|---|---|---|
| interaction | 0.1753 | 27.3% |
| aldehyde | 0.1474 | 23.0% |
| aldehyde_3d | 0.1158 | 18.1% |
| amine_3d | 0.0755 | 11.8% |
| rules | 0.0657 | 10.2% |
| amine | 0.0619 | 9.6% |

## 3D 特征贡献

3D 特征（醛 3D + 胺 3D + 二聚体 3D）累计占全局 SHAP 的 **29.8%**。

| 排名 | 3D 特征 | 分组 | 重要性 | 全局排名 |
|---|---|---|---|---|
| 1 | ald_3d_radius_ratio | aldehyde_3d | 0.0329 | 3 |
| 2 | ald_3d_pmi_i2_i3 | aldehyde_3d | 0.0310 | 4 |
| 3 | ald_3d_mol_volume | aldehyde_3d | 0.0217 | 7 |
| 4 | amine_3d_pmi_i1_i3 | amine_3d | 0.0210 | 8 |
| 5 | ald_3d_pmi_i1_i3 | aldehyde_3d | 0.0176 | 9 |
| 6 | amine_3d_pmi_i2_i3 | amine_3d | 0.0169 | 10 |
| 7 | amine_3d_radius_gyration | amine_3d | 0.0136 | 13 |
| 8 | amine_3d_radius_ratio | amine_3d | 0.0123 | 15 |
| 9 | ald_3d_radius_gyration | aldehyde_3d | 0.0097 | 16 |
| 10 | amine_3d_mol_volume | amine_3d | 0.0080 | 20 |

## Top-20 全局重要特征

| 排名 | 特征 | 分组 | 重要性 |
|---|---|---|---|
| 1 | ald_n_aromatic_rings_per_site | aldehyde | 0.0550 |
| 2 | int_hadamard_tpsa_per_site | interaction | 0.0524 |
| 3 | ald_3d_radius_ratio | aldehyde_3d | 0.0329 |
| 4 | ald_3d_pmi_i2_i3 | aldehyde_3d | 0.0310 |
| 5 | ald_aromatic_frac | aldehyde | 0.0279 |
| 6 | rule_C3邻位(禁) | rules | 0.0220 |
| 7 | ald_3d_mol_volume | aldehyde_3d | 0.0217 |
| 8 | amine_3d_pmi_i1_i3 | amine_3d | 0.0210 |
| 9 | ald_3d_pmi_i1_i3 | aldehyde_3d | 0.0176 |
| 10 | amine_3d_pmi_i2_i3 | amine_3d | 0.0169 |
| 11 | rule_C3对位(非间位) | rules | 0.0151 |
| 12 | rule_F_on_amine | rules | 0.0150 |
| 13 | amine_3d_radius_gyration | amine_3d | 0.0136 |
| 14 | ald_tpsa_per_site | aldehyde | 0.0131 |
| 15 | amine_3d_radius_ratio | amine_3d | 0.0123 |
| 16 | ald_3d_radius_gyration | aldehyde_3d | 0.0097 |
| 17 | amine_has_heterocycle | amine | 0.0085 |
| 18 | ald_logp | aldehyde | 0.0081 |
| 19 | amine_aromatic_frac | amine | 0.0081 |
| 20 | amine_3d_mol_volume | amine_3d | 0.0080 |

## 代表性单体对归因

### Tp + Pa (经典成膜)
- 醛: `O=CC1=C(C=O)C(=O)C(C=O)=C1O`
- 胺: `Nc1ccc(N)cc1`

预测成膜得分: 0.999
主导贡献方: aldehyde（醛/胺）
该单体上的关键官能团: 醛基

**Top-5 正向特征：**
- ald_aromatic_frac (aldehyde): SHAP=0.0622, value=0.0000
- int_hadamard_tpsa_per_site (interaction): SHAP=0.0417, value=575.7576
- ald_3d_radius_ratio (aldehyde_3d): SHAP=0.0320, value=1.1672
- amine_3d_pmi_i1_i3 (amine_3d): SHAP=0.0309, value=0.2112
- ald_n_aromatic_rings_per_site (aldehyde): SHAP=0.0309, value=0.0000

**Top-5 负向特征：**
- ald_is_symmetric (aldehyde): SHAP=-0.0050, value=1.0000
- int_hadamard_mw (interaction): SHAP=-0.0035, value=19478.3566
- int_hadamard_logp (interaction): SHAP=-0.0034, value=-0.6174
- int_ratio_tpsa_per_site (interaction): SHAP=-0.0030, value=0.8504
- int_diff_tpsa_per_site (interaction): SHAP=-0.0027, value=-3.8925

### Tp + 含F二胺
- 醛: `O=CC1=C(C=O)C(=O)C(C=O)=C1O`
- 胺: `Nc1cc(F)cc(N)c1`

预测成膜得分: 0.671
主导贡献方: aldehyde（醛/胺）
该单体上的关键官能团: 醛基

**Top-5 正向特征：**
- ald_aromatic_frac (aldehyde): SHAP=0.0398, value=0.0000
- int_hadamard_tpsa_per_site (interaction): SHAP=0.0371, value=575.7576
- ald_3d_radius_ratio (aldehyde_3d): SHAP=0.0298, value=1.1672
- ald_n_aromatic_rings_per_site (aldehyde): SHAP=0.0276, value=0.0000
- ald_3d_mol_volume (aldehyde_3d): SHAP=0.0173, value=143.3120

**Top-5 负向特征：**
- rule_F_on_amine (rules): SHAP=-0.0607, value=1.0000
- rule_am_e_withdrawing (rules): SHAP=-0.0115, value=1.0000
- rule_非对位(间位) (rules): SHAP=-0.0107, value=1.0000
- amine_3d_pmi_i1_i3 (amine_3d): SHAP=-0.0072, value=0.4942
- amine_mw (amine): SHAP=-0.0071, value=126.1340

### 苯甲醛 + Pa (C2+C3 拓扑不匹配)
- 醛: `O=Cc1ccccc1`
- 胺: `Nc1ccc(N)cc1`

预测成膜得分: 0.770
主导贡献方: aldehyde（醛/胺）
该单体上的关键官能团: 醛基, 苯环

**Top-5 正向特征：**
- int_hadamard_tpsa_per_site (interaction): SHAP=0.1391, value=444.1614
- ald_3d_radius_ratio (aldehyde_3d): SHAP=0.0314, value=1.3064
- ald_tpsa_per_site (aldehyde): SHAP=0.0206, value=17.0700
- amine_tpsa (amine): SHAP=0.0177, value=52.0400
- amine_ring_frac (amine): SHAP=0.0159, value=0.7500

**Top-5 负向特征：**
- ald_n_aromatic_rings_per_site (aldehyde): SHAP=-0.0719, value=1.0000
- ald_aromatic_frac (aldehyde): SHAP=-0.0256, value=0.7500
- int_ratio_n_rings_per_site (interaction): SHAP=-0.0144, value=2.0000
- int_hadamard_mw (interaction): SHAP=-0.0129, value=11476.6739
- int_hadamard_ring_frac (interaction): SHAP=-0.0111, value=0.5625
