# 项目进度跟踪 (progress.md)

每次完成重要任务追加时间戳条目。

---

## 2026-07-11 (Sat, GPT session)

### 上午 (用户在场时)

- ✅ 读 `实验/` 和 `tianxuan seek/` 两个项目全貌
- ✅ 桥接架构设计确认：tianxuan-seek (只读工具) + 实验文件夹 (反馈层) + bridge/ (生成器)
- ✅ 整合架构目录骨架拍板：
  ```
  minimax/
  ├── predict/                    # tianxuan-seek 最小集 (已复制)
  ├── experiment/                 # 反馈层 + 历史 + 试剂库
  ├── bridge/                     # RAG + 生成器 (本次会话推进)
  └── _tmp/                       # 临时
  ```
- ✅ 失败分类 A-G 7 档判据 + Type (单体/条件/操作) 辅助标签拍板
- ✅ 设计决策全锁定（编号体系、SOP 模板、字段、文件引用规则等）

### 下午 (用户在外时)

- ✅ 实验 ABCDEF 全 6 条入库 feedback_db.csv（A/B/C/D/E/F 已完成 + 用户主动归因）
- ✅ 完整提取 8 个进行中实验（A1/A2/A5/A8/D3/D4/D7/D9），建立 in_progress.md
- ✅ 写 failure_playbook.md — 每个进行中实验按"预测失败 → 应对"组织
- ✅ 复制 tianxuan-seek 最小集到 predict/（10 个文件 + v5.3 weights 2.8MB）
- ✅ 写 `bridge/search_local_pdfs.py` — RAG 检索（CAS 精确 + 历史反馈 + 文献文件名扫描）
- ✅ 写 `bridge/generate_proposal.py` — docx 生成器（中文模板 + 自动插图 + 失败回显）
- ✅ 发现 minimax 下已存在 `[知识库/]` 目录（用户预先同步），更新 RAG 检索路径优先该目录
- ✅ ✅ **生成第一份样例 docx**：COF-TFPT-2026-07-11-443922-06-3_2569674-64-0-v2.docx（候选 #2: TFPT+H3）
  - 72 段落 + 5 表格 + 2 个内嵌结构式
  - 基于 ABCDEF 实验 C (TFPT+H3, 0.040 但实际有粉末) 迭代
  - 路径: `experiment/proposals/COF-TFPT-2026-07-11-443922-06-3_2569674-64-0-v2.docx`

### 待用户回来后验证

- ⚠️ `predict/` 代码已复制但依赖未装 (torch / torch_geometric / numpy / pandas / sklearn)
  - **已装有**: rdkit 2026.03.1, yaml, docx
  - 需要在 conda env `dphuanjing` 中安装其余: torch, torch_geometric, numpy, pandas, sklearn
  - 检查: `python predict/_check_env.py`
- ⚠️ 样例 docx 中 H3 胺单体显示 "2569674-64-0" 不是简称 (试剂库没有 name_short)
  - 可选改进: 在 reagent_db.json 中补全 H3 简称

### 晚上 (微信端)

#### RAG 升级与 Nature 2026 学习
- ✅ Nature Comm 2026 文献评估：GraphRAG vs 我们 RAG (gap 分析)
- ✅ MiniMax embedding API 验证：endpoint + embo-01 + type=db/query (1536 维 asymmetric)
- ✅ 写 `experiment/HOW_TO_FILL.md` 填表说明
- ✅ Word 排版优化：1.5 倍行距/缩进 0.74cm(2字符)/中宋英新罗马
- ✅ 加料顺序修正：步骤 2(醛+苯胺) → 步骤 3(立即胺) → 步骤 4(最后乙酸)
- ⚠️ 删除 9 个 doc.add_page_break()：v4 docx 更紧凑 (63段 vs v2 72段)

#### Git 仓库 (已讨论，待拍)
- 📌 用户已 `git init`
- 待回答：GitHub repo URL + 认证方式 (SSH/PAT) + 分支策略 (master vs feature/*)

#### 夜晚 (微信端, 后续推进)

- ✅ Git 配置完成: SSH cmd + remote `https://github.com/Ckx-z/shiyandiedai.git` + longpaths
- ✅ Initial commit (0b105e5): 369 files, 389646 lines
- ✅ chore commit (ae155a5): remove tracked pyc
- ✅ `bridge/inspect_abcdef.py` 巡查工具: 识别 18 个实验 (6 完成 + 12 进行中)
- ✅ `bridge/index_knowledge.py` 索引脚本: MiniMax embo-01 + chunked + pure-python cosine
- ✅ `bridge/update_daily.py` 每日日报 + git commit (cron job 22:00 提示创建中)
- ✅ Knowledge index: 核心 10 篇 PDF/docx → 1791 chunks (knowledge_index.jsonl)
- ✅ RAG 检索集成 embedding: search_local_pdfs.py 加 query_text 参数
- ✅ 测试查询 "TFPT 三嗪醛 与 H3 长氟链酰肼" 命中 sim 0.939 (Chemist-Guided 文献)
- ✅ v5 docx 生成 (COF-TFPT-...-v5.docx, 60KB, 含 RAG 检索结果 + 紧凑排版)
- ✅ README.md 全部更新

### 🚨 重要补救 (微信端紧急)
- ⚠️ **未拦截推送**: 知识库/ PDF 被推到 GitHub (369 文件, ~89MB)
- ❌ pip install git-filter-repo 失败 (ephemeral temp 权限)
- ❌ git filter-branch 撞 sh.exe bug
- ✅ git rm -r --cached 知识库/ 从最新 commit 移除
- ✅ .gitignore 加 知识库/ 永久屏蔽
- ✅ **pre-commit hook 安装 + 测试**: 检出 fake API key 阻断 commit (returncode=1)
- ✅ `bridge/pre_commit_check.py`: 扫描敏感路径 + API key 模式 + 大文件
- ✅ `bridge/install_pre_commit_hook.py`: 一键安装脚本
- ✅ commit d42859f: 安全机制

⚠️ **本地历史前 6 个 commit 仍含 PDF objects**, 用户需 force push + GitHub Support 清理 reflog, 或选方案 B (删仓库重建)
- 待实现：automation cron job (22:00 每日日报)

---

## 资产清单 (脚手架完成度)

| 资产 | 路径 | 状态 |
|------|------|------|
| 失败判据手册 | `experiment/failure_criteria.md` | ✅ |
| 反馈 CSV (6 条 + 中文表头) | `experiment/feedback_db.csv` | ✅ |
| 进行中实验状态板 | `experiment/in_progress.md` | ✅ |
| 失败应对 Playbook | `experiment/failure_playbook.md` | ✅ |
| 试剂库 JSON (35 条) | `experiment/reagent_db.json` | ✅ |
| 结构式图片 (32 CAS) | `experiment/structure/*.png` | ✅ |
| 历史方案索引 | `experiment/history/index.json` | ✅ |
| LLM 路由配置 | `bridge/llm_config.yaml` | ✅ |
| CAS → 图片映射 | `bridge/cas_image_map.json` | ✅ |
| tianxuan-seek 模型 | `predict/` | ✅ |
| Set env 脚本 | `_tmp/set_api_env.ps1` | ✅ |
| RAG 检索 | `bridge/search_local_pdfs.py` | ✅ 已生成并跑通 |
| docx 生成器 | `bridge/generate_proposal.py` | ✅ 已生成并跑通 |
| 第一份样例 docx | COF-TFPT-...v4.docx | ✅ v4 紧凑版（含排版+加料修正） |
| 填表说明 | `experiment/HOW_TO_FILL.md` | ✅ |

---

## 待办 (用户回来后 / 等用户拍)

1. **GitHub 仓库**：创建空仓库，给 URL（SSH 或 HTTPS）
2. **Git 认证**：SSH key 或 PAT，二选一
3. **每日 22:00 自动化**：写 update_daily.py + automation cron job
4. **知识库 RAG 全文索引**：等用户对 Nature 2026 详细讨论后实施
5. **predict/ 装依赖**：conda env dphuanjing 中装 torch + torch_geometric + numpy + sklearn + pandas
6. **审样例 docx v4**：桌面看 COF-TFPT-...-v4.docx 验证排版+加料
7. **Bridge 微信运行**：✅ 已确认（用户已在微信上收发）
8. **API key 轮换**：⚠️ 用户在截图里明文暴露两个 key，强烈建议去 Kimi + MiniMax 控制台轮换
9. **Force push 覆盖远程历史 (方案 A)**：
   ```
   git push --force origin master
   ```
   然后联系 GitHub Support (https://support.github.com/contact) 清理 unreachable objects (PDF 副本)
   或 选方案 B: GitHub 仓库 Settings → Danger Zone → Delete this repository


### 2026-07-13 (auto: cron 22:00)
- 反馈库: 6 条 (6 已完成)
- 进行中: 27 个
- git 状态: 有未提交
- 日报: `experiment/daily/2026-07-13.md` (人类版) + `2026-07-13_ai.md` (AI 版)
