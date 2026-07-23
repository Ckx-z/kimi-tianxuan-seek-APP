# 阶段二：新旧双版对照验证报告

- 生成时间：2026-07-23 16:08:35
- 链路 A（旧版）：`app/gradio_app.py` 直调 `src/` 后端
- 链路 B（新版）：FastAPI `api/`（TestClient 进程内调用）封装同一 `src/` 后端
- 数值容差：0.01（GNN subprocess 两次调用的允许波动）
- 隔离方式：预测日志 / 收藏 / 记录全部重定向到 `C:\Users\ckx\AppData\Local\Temp\phase2_parity_d8rknj0j`；真实 `data/` 只读

## 结果总览

| 用例 | 结果 | 说明 |
|---|---|---|
| P1 内置单体对 A1×对苯二胺 | ⚠️ | score g=0.625 a=0.6105 \| tree g=0.597 a=0.5967134237289429 \| gnn g=0.625 a=0.6105 \| ood g=none a=none　GNN 抖动探针：gradio 三次样本极差=0.0140 → 固有波动，非链路问题 |
| P2 内置单体对 A2×B1 | ⚠️ | score g=0.688 a=0.6880062818527222 \| tree g=0.688 a=0.6880062818527222 \| gnn g=0.656 a=0.6396 \| ood g=none a=none　GNN 抖动探针：gradio 三次样本极差=0.0400 → 固有波动，非链路问题 |
| P3 内置单体对 TFPB×TAPT | ✅ | score g=0.497 a=0.49695736169815063 \| tree g=0.497 a=0.49695736169815063 \| gnn g=0.26 a=0.2617 \| ood g=warning a=warning |
| P4 双未见单体对（合法但结构怪异） | ✅ | OOD gradio=none api=none；score gradio=0.543 api=0.5441 |
| P5 酰肼类胺（应 OOD=out） | ✅ | OOD gradio=out api=out；score 两侧均为 null：True |
| P6 非法醛 SMILES | ✅ | 两侧均不出分且 OOD=out：gradio=True（ood=out）；API=True（HTTP 200, ood=out） |
| P7 非法胺 SMILES | ✅ | 两侧均不出分且 OOD=out：gradio=True（ood=out）；API=True（HTTP 200, ood=out） |
| P8 空输入 | ✅ | gradio 空输入警告=True；API HTTP 400（期望 400） |
| C1 收藏 创建→读取→删除 | ✅ | id=fav_20260723_001；真实 data/favorites 未出现该文件 |
| C2 记录 创建→读取→删除 | ✅ | rec=rec_20260723_001 挂在 fav=fav_20260723_001 下，过滤查询命中 |
| C3 真实 data/ 零污染 | ✅ | favorites 未变=True；records 未变=True；prediction_log.jsonl 未变=True（日志重定向到 C:\Users\ckx\AppData\Local\Temp\phase2_parity_d8rknj0j\prediction_log.jsonl） |
| L1 方案卡 steps/checklist | ✅ | 完全一致 |
| I1 页⑤ suggestions 契约 | ✅ | 共 16 条；含顶层 batch=4 条、payload.confidence=2 条、payload.unverified_refs=2 条（旧批次建议可能缺新字段） |

## 数值对照明细

### P1 内置单体对 A1×对苯二胺

```json
{
  "gradio": {
    "score": 0.625,
    "tree_score": 0.597,
    "gnn_score": 0.625,
    "ood_level": "none",
    "raw_head": "<div class=\"score-big\" style=\"color:#0f766e\">0.625<span class=\"score-std\"> ± 0.008</span></div><div class=\"score-tag\">两模",
    "empty_warning": false
  },
  "api_status": 200,
  "api": {
    "score": 0.6105,
    "tree_score": 0.5967134237289429,
    "gnn_score": 0.6105,
    "ood": {
      "level": "none",
      "reasons": [],
      "checks": {
        "functional_group": {
          "level": "none",
          "reasons": [],
          "details": {
            "aldehyde": {
              "n_aldehyde_groups": 2
            },
            "amine": {
              "n_primary_amine": 2,
              "n_secondary_amine": 0,
              "hydrazide": false,
              "hydrazine": false,
              "hydroxylamine": false
            }
          }
        },
        "novelty": {
          "level": "none",
          "reasons": [],
          "details": {
            "ald_seen": false,
            "amine_seen": true
          }
        },
        "feature_drift": {
          "level": "none",
          "reasons": [],
          "details": {
            "n_checked": 16,
            "n_out": 0,
            "out_ratio": 0.0,
            "threshold": 0.1,
            "out_features": []
          }
        }
      }
    }
  },
  "elapsed_s": 7.3
}
```

### P2 内置单体对 A2×B1

```json
{
  "gradio": {
    "score": 0.688,
    "tree_score": 0.688,
    "gnn_score": 0.656,
    "ood_level": "none",
    "raw_head": "<div class=\"score-big\" style=\"color:#0f766e\">0.688<span class=\"score-std\"> ± 0.006</span></div><div class=\"score-tag\">两模",
    "empty_warning": false
  },
  "api_status": 200,
  "api": {
    "score": 0.6880062818527222,
    "tree_score": 0.6880062818527222,
    "gnn_score": 0.6396,
    "ood": {
      "level": "none",
      "reasons": [],
      "checks": {
        "functional_group": {
          "level": "none",
          "reasons": [],
          "details": {
            "aldehyde": {
              "n_aldehyde_groups": 2
            },
            "amine": {
              "n_primary_amine": 2,
              "n_secondary_amine": 0,
              "hydrazide": false,
              "hydrazine": false,
              "hydroxylamine": false
            }
          }
        },
        "novelty": {
          "level": "none",
          "reasons": [],
          "details": {
            "ald_seen": true,
            "amine_seen": false
          }
        },
        "feature_drift": {
          "level": "none",
          "reasons": [],
          "details": {
            "n_checked": 16,
            "n_out": 0,
            "out_ratio": 0.0,
            "threshold": 0.1,
            "out_features": []
          }
        }
      }
    }
  },
  "elapsed_s": 6.6
}
```

### P3 内置单体对 TFPB×TAPT

```json
{
  "gradio": {
    "score": 0.497,
    "tree_score": 0.497,
    "gnn_score": 0.26,
    "ood_level": "warning",
    "raw_head": "<div class=\"score-big\" style=\"color:#b45309\">0.497<span class=\"score-std\"> ± 0.011</span></div><div class=\"score-tag\">两模",
    "empty_warning": false
  },
  "api_status": 200,
  "api": {
    "score": 0.49695736169815063,
    "tree_score": 0.49695736169815063,
    "gnn_score": 0.2617,
    "ood": {
      "level": "warning",
      "reasons": [
        "单体尺寸/骨架超出训练分布（4/16 项关键特征超出训练 5%–95% 包络：ald_mw、ald_n_aromatic_rings、ald_3d_mol_volume、ald_3d_radius_gyration），打分可信度降低（对应 fold3 区域漂移诊断）"
      ],
      "checks": {
        "functional_group": {
          "level": "none",
          "reasons": [],
          "details": {
            "aldehyde": {
              "n_aldehyde_groups": 3
            },
            "amine": {
              "n_primary_amine": 3,
              "n_secondary_amine": 0,
              "hydrazide": false,
              "hydrazine": false,
              "hydroxylamine": false
            }
          }
        },
        "novelty": {
          "level": "none",
          "reasons": [],
          "details": {
            "ald_seen": true,
            "amine_seen": true
          }
        },
        "feature_drift": {
          "level": "warning",
          "reasons": [
            "单体尺寸/骨架超出训练分布（4/16 项关键特征超出训练 5%–95% 包络：ald_mw、ald_n_aromatic_rings、ald_3d_mol_volume、ald_3d_radius_gyration），打分可信度降低（对应 fold3 区域漂移诊断）"
          ],
          "details": {
            "n_checked": 16,
            "n_out": 4,
            "out_ratio": 0.25,
            "threshold": 0.1,
            "out_features": [
              {
                "feature": "ald_mw",
                "value": 390.4380000000001,
                "p05": 0.0,
                "p95": 376.20085000000006
              },
              {
                "feature": "ald_n_aromatic_rings",
                "value": 4.0,
                "p05": 0.0,
                "p95": 3.0
              },
              {
                "feature": "ald_3d_mol_volume",
                "value": 358.66933333333344,
                "p05": 0.0,
                "p95": 333.6886
              },
              {
                "feature": "ald_3d_radius_gyration",
                "value": 5.038924617094984,
                "p05": 0.0,
                "p95": 4.578416314431134
              }
            ]
          }
        }
      }
    }
  },
  "elapsed_s": 6.5
}
```

### P4 双未见单体对（合法但结构怪异）

```json
{
  "gradio": {
    "score": 0.543,
    "tree_score": 0.0,
    "gnn_score": 0.543,
    "ood_level": "none",
    "raw_head": "<div class=\"score-big\" style=\"color:#b45309\">0.543<span class=\"score-std\"> ± 0.017</span></div><div class=\"score-tag\">两模",
    "empty_warning": false
  },
  "api_status": 200,
  "api": {
    "score": 0.5441,
    "tree_score": 0.0,
    "gnn_score": 0.5441,
    "ood": {
      "level": "none",
      "reasons": [],
      "checks": {
        "functional_group": {
          "level": "none",
          "reasons": [],
          "details": {
            "aldehyde": {
              "n_aldehyde_groups": 2
            },
            "amine": {
              "n_primary_amine": 2,
              "n_secondary_amine": 0,
              "hydrazide": false,
              "hydrazine": false,
              "hydroxylamine": false
            }
          }
        },
        "novelty": {
          "level": "none",
          "reasons": [],
          "details": {
            "ald_seen": false,
            "amine_seen": true
          }
        },
        "feature_drift": {
          "level": "none",
          "reasons": [],
          "details": {
            "n_checked": 16,
            "n_out": 0,
            "out_ratio": 0.0,
            "threshold": 0.1,
            "out_features": []
          }
        }
      }
    }
  },
  "elapsed_s": 6.6
}
```

### P5 酰肼类胺（应 OOD=out）

```json
{
  "gradio": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood_level": "out",
    "raw_head": "### 成膜打分（倾向性）  > 四级软标签上的倾向性打分，非严格概率；对反应条件不敏感。  > 主分数取两模型较高者，属乐观召回口径，高分请结合 OOD 与不确定度判断。  > ⛔ **模型不适用**（OOD 检出）：胺侧为酰肼/肼类非标",
    "empty_warning": false
  },
  "api_status": 200,
  "api": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood": {
      "level": "out",
      "reasons": [
        "胺侧为酰肼/肼类非标准官能团：醛+酰肼成腙键而非亚胺键，模型按醛-胺缩聚场景训练，不适用（对应 H 系单体案例）"
      ],
      "checks": {
        "functional_group": {
          "level": "out",
          "reasons": [
            "胺侧为酰肼/肼类非标准官能团：醛+酰肼成腙键而非亚胺键，模型按醛-胺缩聚场景训练，不适用（对应 H 系单体案例）"
          ],
          "details": {
            "aldehyde": {
              "n_aldehyde_groups": 2
            },
            "amine": {
              "n_primary_amine": 2,
              "n_secondary_amine": 0,
              "hydrazide": true,
              "hydrazine": true,
              "hydroxylamine": false
            }
          }
        },
        "novelty": {
          "level": "none",
          "reasons": [],
          "details": {
            "ald_seen": false,
            "amine_seen": true
          }
        },
        "feature_drift": {
          "level": "none",
          "reasons": [],
          "details": {
            "n_checked": 16,
            "n_out": 0,
            "out_ratio": 0.0,
            "threshold": 0.1,
            "out_features": []
          }
        }
      }
    }
  },
  "elapsed_s": 6.6
}
```

### P6 非法醛 SMILES

```json
{
  "gradio": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood_level": "out",
    "raw_head": "### 成膜打分（倾向性）  > 四级软标签上的倾向性打分，非严格概率；对反应条件不敏感。  > 主分数取两模型较高者，属乐观召回口径，高分请结合 OOD 与不确定度判断。  > ⛔ **模型不适用**（OOD 检出）：醛单体 SMILES",
    "empty_warning": false
  },
  "api_status": 200,
  "api": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood": {
      "level": "out",
      "reasons": [
        "醛单体 SMILES 无法解析，无法判断官能团适配性"
      ],
      "checks": {
        "functional_group": {
          "level": "out",
          "reasons": [
            "醛单体 SMILES 无法解析，无法判断官能团适配性"
          ],
          "details": {
            "aldehyde": "unparsable",
            "amine": {
              "n_primary_amine": 2,
              "n_secondary_amine": 0,
              "hydrazide": false,
              "hydrazine": false,
              "hydroxylamine": false
            }
          }
        },
        "novelty": {
          "level": "none",
          "reasons": [],
          "details": {
            "ald_seen": false,
            "amine_seen": true
          }
        },
        "feature_drift": {
          "level": "none",
          "reasons": [],
          "details": {
            "n_checked": 0,
            "n_out": 0,
            "out_ratio": 0.0,
            "threshold": 0.1,
            "out_features": []
          }
        }
      }
    }
  },
  "elapsed_s": 2.9
}
```

### P7 非法胺 SMILES

```json
{
  "gradio": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood_level": "out",
    "raw_head": "### 成膜打分（倾向性）  > 四级软标签上的倾向性打分，非严格概率；对反应条件不敏感。  > 主分数取两模型较高者，属乐观召回口径，高分请结合 OOD 与不确定度判断。  > ⛔ **模型不适用**（OOD 检出）：胺单体 SMILES",
    "empty_warning": false
  },
  "api_status": 200,
  "api": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood": {
      "level": "out",
      "reasons": [
        "胺单体 SMILES 无法解析，无法判断官能团适配性",
        "醛/胺单体均未在训练集中出现过（双未见）→ 外推模式，打分可信度降低（走 noTE 外推臂）"
      ],
      "checks": {
        "functional_group": {
          "level": "out",
          "reasons": [
            "胺单体 SMILES 无法解析，无法判断官能团适配性"
          ],
          "details": {
            "aldehyde": {
              "n_aldehyde_groups": 2
            },
            "amine": "unparsable"
          }
        },
        "novelty": {
          "level": "warning",
          "reasons": [
            "醛/胺单体均未在训练集中出现过（双未见）→ 外推模式，打分可信度降低（走 noTE 外推臂）"
          ],
          "details": {
            "ald_seen": false,
            "amine_seen": false
          }
        },
        "feature_drift": {
          "level": "none",
          "reasons": [],
          "details": {
            "n_checked": 0,
            "n_out": 0,
            "out_ratio": 0.0,
            "threshold": 0.1,
            "out_features": []
          }
        }
      }
    }
  },
  "elapsed_s": 0.0
}
```

### P8 空输入

```json
{
  "gradio": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood_level": "none",
    "raw_head": "⚠️ 请先填写醛和胺的 SMILES（可用 CAS 解析或内置库点选自动填入）。",
    "empty_warning": true
  },
  "api_status": 400,
  "api": {
    "score": null,
    "tree_score": null,
    "gnn_score": null,
    "ood": null
  },
  "elapsed_s": 0.0
}
```

### C1 收藏 创建→读取→删除

```json
{
  "fav_id": "fav_20260723_001"
}
```

### C2 记录 创建→读取→删除

```json
{
  "rec_id": "rec_20260723_001",
  "fav_id": "fav_20260723_001"
}
```

### L1 方案卡 steps/checklist

```json
{
  "g_steps_n": 7,
  "a_steps_n": 7,
  "g_checklist_n": 5,
  "a_checklist_n": 5
}
```

### I1 页⑤ suggestions 契约

```json
{
  "total": 16,
  "batch": 4,
  "confidence": 2,
  "unverified_refs": 2
}
```

## 结论

- ✅ 11 组　⚠️ 2 组　❌ 0 组

**结论：可切换**（存在警告项，建议切换前确认）。

### 需关注/修复清单

- P1 内置单体对 A1×对苯二胺：score g=0.625 a=0.6105 | tree g=0.597 a=0.5967134237289429 | gnn g=0.625 a=0.6105 | ood g=none a=none　GNN 抖动探针：gradio 三次样本极差=0.0140 → 固有波动，非链路问题
- P2 内置单体对 A2×B1：score g=0.688 a=0.6880062818527222 | tree g=0.688 a=0.6880062818527222 | gnn g=0.656 a=0.6396 | ood g=none a=none　GNN 抖动探针：gradio 三次样本极差=0.0400 → 固有波动，非链路问题