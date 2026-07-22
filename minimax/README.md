# minimax — COF 实验方案迭代系统

把 **tianxuan-seek (GNN 成膜预测)** 和 **实验反馈** 整合到一个人机协同工作流。

> 模型的归模型，实验的归实验。
> 模型的输出"成膜概率"是**先验估计**，不是对实验结果的承诺。
> 实验成败不反噬模型 PR-AUC，可解释性是核心交付。

---

## 目录结构

```
minimax/
├── predict/                    # 从 tianxuan-seek 复制的最小运行集
│   ├── predict_pair.py         # 单对预测 CLI
│   ├── src/screening/{gnn_v3,gnn_v4}
│   ├── src/chemistry/          # 规则 + 链接子 + 负采样
│   ├── models/v5.3/            # 权重
│   ├── config/model_v4.yaml
│   └── _check_env.py           # 环境检查脚本
│
├── experiment/                 # 实验反馈层 (本系统的核心)
│   ├── feedback_db.csv         # 实验反馈 (中文表头)
│   ├── failure_criteria.md     # 失败判据手册 (Class A-G)
│   ├── failure_playbook.md     # 进行中实验的失败应对
│   ├── in_progress.md          # 进行中实验状态板
│   ├── reagent_db.json         # 试剂库 (CAS → 结构式/已买状态)
│   ├── history/index.json      # 现有方案索引
│   ├── structure/              # 32 张 CAS 结构式图片
│   ├── proposals/              # 生成的实验方案 docx
│   ├── daily/                  # 每日工作报告 (人类版 + AI 版)
│   └── HOW_TO_FILL.md          # 反馈 CSV 填表指南
│
├── bridge/                     # 两系统的连接层
│   ├── search_local_pdfs.py    # RAG 检索 (5 路召回: CAS + 历史 + 核心/tianxuan embedding + 关键词)
│   ├── generate_proposal.py    # docx 生成器 (中文模板 + GraphRAG v2 证据)
│   ├── fast_search.py          # 高性能 tianxuan 二进制向量检索
│   ├── build_tianxuan_matrix.py # JSONL → 二进制向量索引构建
│   ├── graphrag_v2/            # GraphRAG v2 (NL2Graph + 路由 + 社区 + 多跳)
│   ├── inspect_abcdef.py       # ABCDEF 巡查工具 (识别新增完成)
│   ├── index_knowledge.py      # 知识库 PDF/docx → embedding 索引
│   ├── update_daily.py         # 每日 22:00 日报生成 + git commit
│   ├── pre_commit_check.py     # 敏感信息扫描 (pre-commit hook 调用)
│   ├── install_pre_commit_hook.py # 一键安装 pre-commit hook
│   ├── llm_config.yaml         # LLM 路由 (主 MiniMax + 备 longcat)
│   ├── cas_image_map.json      # CAS → 结构式图片映射
│   ├── knowledge_index.jsonl   # 核心知识库 embedding 索引 (1791 chunks, gitignored)
│   ├── knowledge_index_tianxuan.jsonl # tianxuan 全库索引 (282057 chunks, 5.8GB, gitignored)
│   ├── tianxuan_vectors.bin    # 二进制向量索引 (1.7GB, gitignored)
│   ├── tianxuan_meta.json      # tianxuan 元数据 (200MB, gitignored)
│   ├── test_integration.py     # 集成测试 (12 cases)
│   ├── _build_reagent_db.py    # 从 xlsx 重建 reagent_db.json
│   └── _copy_predict.py        # 从 tianxuan-seek 复制最小集
│
├── adapters/                   # 与 COF App（全新机器学习实验）的文件对接层
│   ├── cof_app_ingest.py       # 读 App 侧 rag_export → 校验 + 转换 feedback 行
│   ├── iterate_suggest.py      # 页⑤ 自然语言方案迭代编排器（见下「页⑤ 对接」）
│   └── test_iterate_suggest.py # 编排器测试
│
├── 知识库/                    # 本地文献 PDF (只读, 来源包含于知识库/下)
│
├── progress.md                 # 项目进度跟踪 (时间戳追加)
├── README.md                   # 本文件
└── _tmp/set_api_env.ps1        # 环境变量设置脚本 (一次性)
```

---

## 运行环境与随仓库分发说明

- **GraphRAG 运行时**：固定使用 `E:\python3.12\python.exe`（Python 3.12），依赖见 `requirements-minimax.txt`（networkx / requests / python-docx 等）。`predict/` 的 GNN 推理走 dphuanjing 环境（torch/PyG），不在该文件内。
- **GraphRAG 检索底座随仓库分发**：`bridge/graphrag/`（图谱 `graph.pkl` / `graph_v2.pkl`、节点/边 jsonl、文献 embedding）连同本模块其余已追踪文件共约 75MB，**刻意保留在 git 追踪中**（决策：克隆即用，避免每位用户重建图谱；它们虽命中根 `.gitignore` 的 `*.pkl` 规则但已被强制追踪）。`bridge/graphrag/.gitignore` 中原有的 `graph_v2.pkl` 忽略规则与该决策矛盾，已删除。详见根 README「数据与随仓库分发说明」。

---

## 核心工作流

```
[tianxuan-seek GNN]                    [实验文件夹]
  predict_pair.py  ── 成膜概率 ──→       feedback_db.csv
  v5.3  PR-AUC 0.7635                    ↑ ↓ (每天上传, 攒批回流)
        ↓                                       ↓
  [RAG + LLM 生成器]                  bridge/inspect_abcdef.py
        ↓                                       ↓
  实验方案 docx ←─── 知识库 embedding ──── 每周巡查 ABCDEF 新完成
```

**关键原则**：
- **tianxuan-seek 是只读工具源**，不重训
- **新模型在 bridge/ 里**，是 LLM-RAG (embedding + Kimi/MiniMax LLM)
- **实验反馈是数据**, 不是模型权重更新
- **实验方案迭代**是文档级的, 不是梯度下降

---

## 失败分类 (A-G, 单编组递进)

| Code | 含义 | PXRD 必填 |
|------|------|-----------|
| A | 无产物 | 否 |
| B | 未充分反应 | 否 |
| C | 无定形产物 | 建议 |
| D | 弱结晶产物 | 建议 |
| E | 膜质量差 | 建议 |
| F | 膜质量中 | 必填 |
| G | 膜质量高 | 必填 |

详见 `experiment/failure_criteria.md` 和 `experiment/failure_playbook.md`

---

## LLM 路由

- **主 LLM**: MiniMax (方案拼装、结构化抽取)
- **备用 LLM**: longcat (`LongCat-2.0`，`LONGCAT_API_KEY`；主端点失败自动回落)
- **Embedding**: MiniMax `embo-01` (type=db / type=query, 1536 维 asymmetric)
- **本地检索**: 优先结构式 CAS 精确匹配, 其次 embedding 相似度

**API key 安全**: 全部从环境变量读, 任何时候不写进文件

```powershell
$env:MINIMAX_API_KEY = "你的 MiniMax API key"
$env:LONGCAT_API_KEY = "你的 longcat API key"
```

设置脚本: `_tmp/set_api_env.ps1`

---

## 页⑤ 对接（COF App ↔ minimax）

与主项目「全新机器学习实验」页⑤（自然语言方案迭代）对接：**读 App 侧
`data/rag_export/records/` 实验记录 + 收藏单体 → 降级链检索取证 → LLM 生成 →
按 Schema 3 落盘 `data/rag_export/suggestions/` 供 App 回显**（文件对接，零耦合；
契约见 `docs/COF_APP_CONTRACT.md` 与 App 侧 `data/rag_export/README.md`）。

```powershell
# 指定收藏条目迭代
python minimax/adapters/iterate_suggest.py --favorite-id fav_20260722_001 --question "上次失败了，下次怎么调条件"
# 省略 --favorite-id 表示用全部实验记录
python minimax/adapters/iterate_suggest.py --question "..."
```

---

## 每日工作流 (automation cron 22:00)

`bridge/update_daily.py` 自动运行:
1. 生成 `experiment/daily/{today}.md` (人类版日报)
2. 生成 `experiment/daily/{today}_ai.md` (AI 版详细日报)
3. 追加 `progress.md` 时间戳
4. `git add` + `commit` (不 push)

用户手动 `git push` 到 GitHub。

---

## Git 仓库

- **本地**: 本仓库 `minimax/` 目录（位于 `C:\Users\ckx\Desktop\全新机器学习实验\minimax`，master 分支；旧独立路径 `C:\Users\ckx\Desktop\minimax` 已失效）
- **GitHub**: `https://github.com/Ckx-z/shiyandiedai`
- **认证**: SSH (你已配)

⚠ Git Bash 自带 `sh.exe` 有 bug (`Win32 error 5`)，`git push` 改用 HTTPS + git credential。

## ⚠️ 敏感信息保护 (pre-commit hook)

所有 commit 前自动扫描 (`.git/hooks/pre-commit.bat`):
- **阻断路径**: `知识库/`, `.env*`, `secrets/`, `.ssh/`, `.aws/`
- **阻断大文件**: > 10MB
- **阻断 API key**: `sk-*`, `ghp_*`, `sk-kimi-*`, `sk-cp-*`
- **阻断二进制**: `.pdf`, `.pptx`, `.xlsx` (除 `.docx`)

测试通过: fake API key 被检出, commit 被阻断 (returncode=1).

⚠ **本地前 6 个 commit 仍含 知识库/ PDF git objects**:
- Force push (`git push --force origin master`) 不会删除远程的 unreachable objects
- 推荐: GitHub 仓库 Settings → Danger Zone → Delete this repository + 重建 (方案 B)

---

## 当前状态 (2026-07-22)

✅ 已完成:
- 实验 ABCDEF 14 条入库 feedback_db.csv
- 失败分类体系 (A-G) + Playbook + 进行中状态板
- tianxuan-seek 全库 2468 PDF embedding 索引 (282057 chunks, 5.8 GB)
- **二进制向量索引** (1.7 GB bin + 200 MB meta) 实现亚秒级检索
- **GraphRAG v2** 集成到方案生成 (NL2Graph + 动态路由 + 多模态重排 + v1 fallback)
- RAG 检索 (5 路召回: CAS + 历史 + 核心 embedding + **tianxuan 全库 embedding** + 关键词)
- docx 生成器 (中文模板 + 自动插图 + 加料顺序修正 + 1.5 倍行距 + GraphRAG v2 证据)
- 12 个集成测试全部通过
- 知识库核心 10 篇 PDF/docx embedding 索引 (1791 chunks)
- 每日日报 + git commit (cron job)
- ABCDEF 巡查工具 (inspect_abcdef.py)
- pre-commit hook (敏感信息扫描)
- **页⑤ 对接**（adapters/iterate_suggest.py + cof_app_ingest.py，与主项目文件对接零耦合）
- **波次 2**：failure 专家语料注入、evidence_refs 白名单校验（编造引用剔除进 unverified_refs）、
  confidence 置信度自评（0 证据强制 low）、GraphRAG 多跳 BFS、nl2graph 中文支持、数据清洗重建

⏸ 等待用户:
- 上传最新实验反馈 (in_progress.md 停在 7/11)
- 手动 git push 到 GitHub
- 清理 git history (知识库 PDF 在前 6 个 commit)