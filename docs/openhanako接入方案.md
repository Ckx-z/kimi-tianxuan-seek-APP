# openhanako（HanaAgent）"ming 助手"接入方案（讨论稿）

> 产出日期：2026-08-20（调研代理）
> 调研对象：https://github.com/liliMozi/openhanako （GitCode 镜像：https://gitcode.com/liliMozi/openhanako ，用于读取目录树与文件）
> 本文是方案讨论稿，不是实施记录；未改任何业务代码，未提交 git。

---

## 一、openhanako 项目概况与 ming 助手架构

### 1.1 项目是什么

openhanako 即 **HanaAgent**（v0.447.4，约 6.2k star）：一个本地优先的桌面 AI Agent 应用，主打"有记忆、有人格、会主动行动、多 Agent 协作"。技术栈：

| 层 | 技术 |
| --- | --- |
| 桌面端 | Electron 42 |
| 前端 | React 19 + Zustand 5 + Vite 7 |
| 服务端 | Hono + @hono/node-server（独立 Node 进程，WebSocket 通信）|
| Agent 运行时 | Pi SDK（@earendil-works/pi-coding-agent 0.80.3）|
| 数据库 | better-sqlite3（WAL）|
| 模型接入 | OpenAI 兼容 / Anthropic 风格 / OAuth / Ollama 本地模型 |

顶层目录：`core/`（引擎编排+Manager）、`lib/`（记忆、工具、沙盒等核心库）、`server/`、`hub/`（定时任务/频道/事件总线）、`desktop/`、`plugins/`（内置插件：beautify、jimeng-cli、media、office）、`skills2set/`（内置技能）、`packages/`（插件 SDK 四件套）。

### 1.2 "ming 助手"的真实形态（重要校正）

**调研结论：ming 不是一个"科研板块"功能模块，而是 HanaAgent 三个内置人格模板之一**（hanako / butter / ming）。它的全部定义只有两个 Markdown 文件：

- `lib/identity-templates/ming.md`（身份卡，全文 2 行）：
  > {{agentName}} —— {{userName}}的个人助手。理性优先，用逻辑和分析拆解世界。
- `lib/ishiki-templates/ming.md`（"意识"/人格定义，全文约 12 条），要点：
  - 冷静深刻，把复杂的事情拆到最简；语气克制、精准、不废话
  - 核心能力是分析和判断：抓问题结构、找关键杠杆点
  - 不回避不确定性：不知道就说不知道，不在中间地带含糊
  - 对用户观点先拆前提、再评价结论
  - **涉及概念解释时，必须全网搜索**
  - 从底层客观原理出发，不人云亦云
  - 文风约束：少用破折号、不用套话收尾、避免「不是…是…」句式

ming 的"科研感" = 这套理性分析人格提示词 + HanaAgent 通用能力（联网搜索、记忆、工具、技能）的组合效果，**仓库里没有任何名为 ming 的科研流水线、RAG 或领域代码**。中英两版模板各一份（`lib/identity-templates/{,en/}ming.md`、`lib/ishiki-templates/{,en/}ming.md`）。

### 1.3 License 与合规复制结论

- **Apache License 2.0**（仓库根 `LICENSE`，package.json 亦标注 `"license": "Apache-2.0"`）。
- **可以合规复制入库**，义务：①保留 Apache-2.0 许可证文本与版权/归属声明（建议在复制文件头部注明来源 liliMozi/openhanako）；②对修改过的文件声明变更；③不得使用其商标背书；④注意其依赖的 Pi SDK 等第三方组件各有自己的许可证，只复制 openhanako 自有代码部分不涉及此问题。
- 两个 ming 人格模板是 Markdown 提示词，复制成本与风险都最低，建议复制时在文件头加一行来源注释。

---

## 二、可接入功能清单（对照我们现有体系）

我们已有：FastAPI 后端（`api/`）+ React/Electron 前端 + `src/llm/client.py`（OpenAI 兼容统一客户端，配置链+磁盘缓存+密钥红线）+ minimax GraphRAG v2 迭代系统（5 路召回、NL2Graph、方案 docx 生成、实验反馈 CSV、日报）+ `src/rag/suggestions.py`（迭代建议契约）+ 方案卡/实验记录（含自我总结/失误字段，已纳入 RAG 摄入）。

| # | openhanako 能力 | 是什么 | 对我们的用处 | 接入难度 | 依赖/前提 |
|---|---|---|---|---|---|
| 1 | **ming 人格模板**（identity + ishiki 两份 md） | 理性分析型助手提示词 | 直接作为"科研助手"系统提示词层，叠加在方案迭代问答/日报/建议生成上；与 GraphRAG 正交 | **小** | 无，纯文本，当天可用 |
| 2 | **人格=文件夹**的 Agent 封装思路（identity.md / ishiki / pinned.md，见 `lib/identity.example.md` 等） | 人格、钉选记忆、技能打包成一个目录，可备份可导入导出（角色卡 zip） | 我们可以把"COF 科研助手"定义成一个可版本管理的目录（人格+领域规则+失败判据引用），放进仓库随项目分发 | **小** | 只需约定目录规范 |
| 3 | **记忆编译架构**（`lib/memory/`：rolling summary、fact-store、pinned-memory、deep-memory、memory-search、compile、dream/ 定期反思、llm-budget） | 会话→滚动摘要→事实抽取→钉选→定期"做梦"反思压缩的多层记忆 | 与我们的 GraphRAG 互补：GraphRAG 管领域知识/文献，记忆系统管"这个课题组做过什么、踩过什么坑"。实验记录的"自我总结/失误"字段正是事实抽取的天然素材 | **中** | 需用 Python 重写（原实现是 TS+better-sqlite3）；可复用我们现有 SQLite/文件存储 |
| 4 | **技能（SKILLS）生态与技能包**（skills2set/、lib/skills、lib/skill-bundles） | Agent 可安装/自写 Markdown 技能，按包成组启用 | 我们的 GraphRAG 迭代流程（反馈→检索→建议→方案）可以沉淀成显式技能文档，让 LLM 行为可审计可迭代 | **中** | 概念移植，无代码依赖 |
| 5 | **定时任务/心跳 + 书桌巡检**（hub/ 调度器，"什么时候触发"与"做什么"分离） | cron + 文件变动巡检 + 后台执行 | 对应我们已有的每日 22:00 日报（`update_daily.py`）；可借鉴其"轻量提醒直接通知、重活后台跑"的拆分 | **中** | 我们已有等价物，只借鉴设计 |
| 6 | **PathGuard 四级访问控制 + 沙盒** | 应用层读写权限分级 | 我们单机分发、用户即-owner，威胁模型不同，暂不需要 | **大/不需要** | 跳过 |
| 7 | **多平台 Bridge**（Telegram/飞书/QQ/微信）、移动端 PWA | 远程操控同一 Agent | 与"单机离线可分发"约束冲突，且无需求 | 不建议 | 跳过 |
| 8 | **Pi SDK Agent 运行时 / Hono server / 插件系统** | 整套 TS Agent 底座 | 语言栈不兼容（我们是 Python/FastAPI），整体搬运会引入 Node 运行时，违背"无重型服务、科研人员维护" | 不建议 | 跳过 |
| 9 | **文档解析工具链参考**（mammoth/unpdf/exceljs/anydoc 依赖清单） | Office/PDF 抽取 | 我们已有 `index_knowledge.py` 摄入 PDF/docx；仅作依赖选型参考 | 小 | 可选 |

**一句话：值得接的是 1（人格提示词）、2（Agent=目录封装）、3（记忆编译架构）、4（技能化）；5 借鉴；6–8 明确不接。**

---

## 三、推荐接入顺序（分批）

**第一批（本周可做，纯文本零依赖）**
1. 复制 ming 两份人格模板入库（如 `minimax/persona/ming/`，头部加 Apache-2.0 来源注释），接进 `src/llm/client.py` 调用链：方案迭代问答、日报生成、建议生成时作为系统提示词前缀。
2. 定一个"科研助手目录规范"（人格 + pinned 领域规则 + 引用 failure_criteria/playbook），先手写一版，不建基础设施。

**第二批（设计先行，Python 重写）**
3. 记忆编译最小闭环：实验记录"自我总结/失误"字段 → 事实库（SQLite 表或 jsonl）→ 每周一次 LLM 编译成滚动摘要 → 检索时作为一路召回并入 `search_local_pdfs.py` 的 5 路召回变 6 路。
4. 把 GraphRAG 迭代流程写成显式技能文档（`skills/` 目录），让助手行为可版本化。

**第三批（可选）**
5. 借鉴 hub 的触发/动作分离，把日报、cas 巡查等定时任务统一管理（我们已有 cron/脚本，收益有限，优先级低）。

**不建议接**：整套 Pi SDK/Node 底座、多平台 Bridge、移动端、沙盒系统、插件市场——与单机离线、无重型服务、Python 技术栈、维护者画像全部冲突。

---

## 四、与现有 GraphRAG/LLM 体系的集成方式（草案）

```
用户提问/迭代请求
   │
   ▼
系统提示词 = ming 人格模板（identity + ishiki）   ← 第一批
   + pinned 领域规则（COF 实验规范/失败判据引用）  ← 第一批
   │
   ▼
检索增强 = 现有 GraphRAG v2 5 路召回
   + 第 6 路：编译记忆召回（实验事实/失误教训）    ← 第二批
   │
   ▼
src/llm/client.py（不变：配置链 + 缓存 + 密钥红线）
   │
   ▼
输出：方案卡 / 迭代建议（sug_*.json 契约不变）/ 日报
```

- **不动** `src/llm/client.py` 的配置链、缓存、密钥红线；人格只是 messages 里多一条 system。
- **不动** suggestions 契约（`src/rag/suggestions.py` 的 schema 1.0）；记忆编译产出走独立存储，避免契约膨胀。
- 记忆编译频率先用"每周一次手动脚本"，跑稳再考虑定时；LLM 调用走现有缓存，控制成本（可借鉴其 `llm-budget.ts` 思路，给编译任务设 token 上限）。

---

## 五、风险与开放问题

1. **认知落差（最大风险）**：用户预期"ming 是专门科研板块"，实际它只是一份人格提示词。科研能力需要我们自己把 GraphRAG/实验反馈与之组合——本方案的第一、二批就是在做这件事，请先与用户对齐预期。
2. **语言栈不可直接复制**：openhanako 全部 TypeScript/Node，"代码全部可以复制参考入库"在工程上不成立；能直接入库的是提示词、目录规范、架构思路，代码需 Python 重写。
3. **License 合规细节**：复制文件需保留 Apache-2.0 声明与来源；若日后我们项目要商用/闭源分发，Apache-2.0 兼容但须履行声明义务。Pi SDK（其 Agent 运行时）是独立依赖，我们不引入。
4. **镜像滞后**：本次目录树读自 GitCode 镜像，可能与 GitHub main 有同步延迟；正式实施前建议以 GitHub 原仓为准复核 ming 模板原文（当前版本 sha：`lib/identity-templates/ming.md` = 931992a…）。
5. **开放问题**：
   - 人格叠加会不会削弱方案生成的格式稳定性（docx 模板填字段）？需要一个小评估：同一反馈分别用/不用 ming 人格生成建议，比对采纳率。
   - 记忆编译用哪个模型槽位（longcat/minimax）？编译是长文本任务，建议沿用 llm_config.yaml 的主路由。
   - "失误教训"事实库是否对终端用户可见可编辑？（建议可见可钉选，借鉴其 pinned-memory。）

---

## 附：调研依据（本回合实际抓取的来源）

- GitHub README / README_EN（功能、架构、技术栈、License）
- GitCode 镜像 API 目录树：顶层、`plugins/`、`skills2set/`、`packages/`、`lib/`、`lib/memory/`、`lib/identity-templates/`、`lib/ishiki-templates/`
- 原文文件：`package.json`、`lib/identity-templates/ming.md`、`lib/identity-templates/en/ming.md`、`lib/ishiki-templates/ming.md`、`lib/identity.example.md`
