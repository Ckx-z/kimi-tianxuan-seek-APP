# minimax — COF 实验方案迭代系统

把 **tianxuan-seek (GNN 成膜预测)** 和 **实验反馈** 整合到一个人机协同工作流。

> 模型的归模型，实验的归实验。
> 模型的输出"成膜概率"是**先验估计**，不是对实验结果的承诺。
> 实验成败不反噬模型 PR-AUC，可解释性是核心交付。

---

## 目录结构

```
minimax/
├── predict/                    # 从 tianxuan-seek 复制的最小运行集 (待建)
│   ├── predict_pair.py
│   ├── src/
│   ├── models/v5.3/
│   ├── config/
│   └── requirements.txt
│
├── experiment/                 # 实验反馈层 (本系统的核心)
│   ├── feedback_db.csv         # 实验反馈 (中文表头, 见下)
│   ├── failure_criteria.md     # 失败判据手册 (Class A-G)
│   ├── reagent_db.json         # 试剂库 (CAS → 结构式/已买状态)
│   ├── history/                # 现有方案索引
│   │   └── index.json
│   ├── structure/              # 32 张 CAS 结构式 PNG
│   └── proposals/              # 生成的实验方案 docx (待建)
│
├── bridge/                     # 两系统的连接层
│   ├── cas_image_map.json      # CAS → 结构式图片映射
│   ├── llm_config.yaml         # LLM 路由 (主 MiniMax + 备 Kimi 2.7)
│   ├── _build_reagent_db.py    # 从 xlsx 重建 reagent_db.json 的工具
│   ├── search_local_pdfs.py    # (待建) 本地 PDF RAG 检索
│   ├── fetch_external.py       # (待建) 外部文献 web 检索
│   └── generate_proposal.py    # (待建) 主生成器: 拼装 docx
│
└── README.md                   # 本文件
```

---

## 核心工作流

```
[tianxuan-seek GNN]                    [实验文件夹]
  predict_pair.py  ─── 成膜概率 ──→      feedback_db.csv
  v5.3  PR-AUC 0.7635                    ↑ ↓ (每天上传, 攒批回流)
        ↓
  [MiniMax RAG]  ─── 检索本地 PDF + Kimi 2.7 推理 ──→  实验方案 docx
        ↑                                                        ↓
  bridge/llm_config.yaml                            ───→  化学家单点选样
        ↓
  [失败诊断 + 下一轮建议]
        ↓
  反馈回 feedback_db.csv
```

**关键原则**：
- **tianxuan-seek 是只读工具源**，不重训
- **新模型在 bridge/ 里**，是 LLM-RAG 而非端到端 ML
- **实验反馈是数据**, 不是模型权重更新
- **实验方案迭代**是文档级的, 不是梯度下降

---

## 失败分类 (A-G)

| Code | 含义 | PXRD 必填 |
|------|------|-----------|
| A | 无产物 | 否 |
| B | 未充分反应 | 否 |
| C | 无定形产物 | 建议 |
| D | 弱结晶产物 | 建议 |
| E | 膜质量差 | 建议 |
| F | 膜质量中 | 必填 |
| G | 膜质量高 | 必填 |

详见 `experiment/failure_criteria.md`

---

## 反馈 CSV 字段 (中文表头)

| 字段 | 说明 |
|------|------|
| 方案编号 | `COF-{TAPT\|TAPB}-{YYYY-MM-DD}-{醛CAS}_{胺CAS}-v{N}` |
| 日期 | 反馈日期 |
| 版本 | v1, v2, v3... |
| 醛CAS / 醛SMILES / 醛名称 / 醛结构式路径 | 单体描述 |
| 胺CAS / 胺SMILES / 胺名称 / 胺结构式路径 | 同上 |
| tianxuan_预测概率 / tianxuan_MC标准差 | GNN 输出 |
| 试剂状态 | 已买 / 未买 |
| 阳性对照 | 已知成功体系, 如 AMCOF-1 |
| 单一变量 | 本组唯一改变的变量 |
| 科学问题 | 本组要回答的科学问题 |
| 失败Class | A-G |
| 失败现象描述 | 实际观察 |
| 根因Type | 单体 / 条件 / 操作 (辅助) |
| 根因Notes | 详细原因 |
| PXRD文件 / FTIR文件 / SEM文件 | 可选 |
| 关联历史失败 | 直接相似 + 引用 |
| 关联外推依据 | 外推置信度 + 引用 |
| 下一轮建议 | 自动生成 |
| 备注 | 自由文本 |

---

## LLM 路由

- **主 LLM**: MiniMax (方案拼装、结构化抽取)
- **备用 LLM**: Kimi 2.7 (失败诊断、复杂化学推理)
- **Embedding**: MiniMax (起步, 必要时换 BGE)
- **本地检索**: 优先结构式 CAS 精确匹配, 其次 embedding 相似度

**API key 安全**: 全部从环境变量读, 任何时候不写进文件

```powershell
$env:KIMI_API_KEY = "..."
$env:MINIMAX_API_KEY = "..."
```

---

## 当前状态

- ✅ predict/ 复制 (待执行)
- ✅ experiment/feedback_db.csv 表头建好
- ✅ experiment/failure_criteria.md 写好
- ✅ experiment/reagent_db.json 转好 (35 条)
- ✅ experiment/structure/ 32 张 PNG 就位
- ✅ bridge/cas_image_map.json 就位
- ✅ bridge/llm_config.yaml 就位
- ✅ experiment/history/index.json 就位
- ⏳ 等待用户上传最新实验组
- ⏳ bridge/search_local_pdfs.py
- ⏳ bridge/fetch_external.py
- ⏳ bridge/generate_proposal.py
- ⏳ 第一份样例 docx
