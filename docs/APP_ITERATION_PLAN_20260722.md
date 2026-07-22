# App 迭代方案 v2（2026-07-22，待用户审查）

> 依据用户 2026-07-22 实测反馈（实验记录 4 项 / 查询打分 2 项 / 架构 2 项 / 方案迭代 1 项）。
> 本方案审查通过后开工，分 P4a / P4b / P4c 三期。

---

## A. 实验记录模块修复（P4a，最高优先）

| # | 反馈 | 修法 |
|---|---|---|
| a | 选中收藏后只显示它关联的记录 | 页④时间线与收藏选择联动：选中 → 只显示该 fav 的记录；提供"显示全部"开关 |
| b | 实验编号写在备注里 → 备注必填 | 表单加"实验编号"**独立必填字段**（如 A5、G2-3），比混在备注里更结构化；提交时并入记录的 `notes` 前缀 + 单独存 `experiment_no` 字段（契约加可选键） |
| c | 切换单体组后表单不刷新（bug） | 收藏选择变化时重置全部表单字段；提交成功后也重置；加回归测试 |
| d | 溶剂分溶剂一/二 + 加洗脱剂 | conditions 改为：`solvent_1` / `solvent_2` / `eluent`（洗脱剂）+ 调制剂/催化剂/温度/时间/加料顺序；契约 README 同步（新增可选键，schema_version 不变，向后兼容） |

## B. LLM 基础设施（P4b 前置，支撑 C/D/F）

**统一 LLM 客户端 `src/llm/`**（新模块，与 minimax 的 llm_client 同思路但独立）：

- **配置链**：App 设置页输入（base_url / api_key / model）→ 写 `config/llm_settings.local.json`（**gitignored**）→ 环境变量兜底 → minimax 的 `secrets.local.json` 作为默认种子（longcat 已配好，开箱即用）
- **OpenAI 兼容**：任何兼容端点都能接（longcat / MiniMax / OpenAI / 本地 vLLM），界面设置页可测连通性
- **优雅降级**：未配置时 LLM 功能按钮显示"未配置 LLM，点击设置"而不报错；所有 LLM 内容本地缓存（`data/llm_cache/`，gitignored），同输入不重调
- **红线**：密钥永不入库；仓库里只留 `config/llm_settings.example.json` 模板

**新用户开箱体验**：clone 后无任何 key → LLM 功能灰显，其余功能全可用；填自己的 key 即激活。（这就是反馈 3 的"预留 API 接口"。）

## C. 单体性质卡片（反馈 2a，P4b）

页①/批量选中单体后展示性质卡：

- **确定性事实**（RDKit 计算，零成本零幻觉）：分子量、XlogP、TPSA、HBD/HBA、芳环数、F 原子数、可旋转键
- **LLM 解读**（调 B 的客户端）：基于上述事实 + SMILES 生成 3-5 句中文解读（溶解性预期、在 COF 中的角色、含氟/官能团意义、实验注意事项），明确标注"LLM 生成，供参考"
- 按 SMILES 缓存，不重复调用

## D. 方案卡模板系统（反馈 2b，P4b）

- **模板 schema**：`{name, source, conditions{}, steps[], checklist[], hints_rules[]}`；内置默认模板"侯老师界面法 v3.9"（随仓库分发）
- **自定义模板**：上传文献实验方案（docx/pdf/txt）→ LLM 自动提取为模板 schema → **预览确认** → 存 `data/plan_templates/`（用户模板 gitignored，仅默认模板入库）
- 页①/③生成方案卡时可下拉选择模板；提取失败给原文摘选 + 手动编辑入口

## E. 数据独立与 git 卫生（反馈 3，P4a）

- 新增 gitignore：`data/favorites/*.json`（保留 .gitkeep）、`data/rag_export/records/*` 与 `suggestions/*`（保留 example.json）、`data/plan_templates/*`、`data/llm_cache/`、`config/llm_settings.local.json`
- 已入库的用户数据 `data/favorites/fav_20260721_001.json` 执行 `git rm --cached` 移出跟踪（文件保留）
- README 加"新用户独立实例"说明：clone 后收藏/记录/日志均为本地数据，互不影响

## F. 页⑤ 自然语言方案迭代（反馈 4，P4c）

**我的看法：同意，这才是这页该有的样子。** 现在的页⑤只是被动展示，不是"迭代"。但建议分两层，别等 minimax：

- **L1（本期做，App 内轻量闭环）**：自然语言对话框——你说"A 组失败了，乙酸加多了，下一步怎么调？" → 系统自动组装上下文（该收藏条目 + 其实验记录 + 相似案例 + 相关文献标题 + 方案卡）→ LLM 生成迭代建议 → **你确认后**按契约写入 `data/rag_export/suggestions/`（status=proposed）→ 建议卡片可关联收藏、可标 adopted/rejected
- **L2（将来）**：minimax 的 GraphRAG v2（7GB 向量索引已就位）成熟后走深检索路线，产出同样落 suggestions/ 契约——两层共用一套展示和确认机制，不冲突
- 检索策略 L1 先用轻量方案（关键词 + 结构相似度 + 收藏关联），不急着上向量库；上下文拼进 prompt 前先过 PII/体积控制

## 分期与工作量

| 期 | 内容 | 预估 |
|---|---|---|
| **P4a** | A（记录模块 4 项修复）+ E（git 卫生） | 半天 |
| **P4b** | B（LLM 基座+设置页）+ C（性质卡）+ D（模板系统） | 1 天 |
| **P4c** | F（页⑤ 自然语言迭代 L1） | 1 天 |

## 待你确认的开放问题

1. "实验编号"独立必填字段（我的建议）还是就放备注里必填（你的原话）？我倾向独立字段——结构化、将来可按编号检索
2. 洗脱剂（eluent）是"反应后处理洗涤用"的理解对吗？还有别的工艺参数要加吗（如陈化时间）
3. LLM 性质卡的默认语言/详略：3-5 句中文要点，够吗
4. 模板上传接受 docx/pdf/txt，先用 docx 为主（你的文献都是 docx）？
5. P4a 是否立即先做（不影响你继续实测其他页面）
