# 「科研助手」Agent 模块接入方案 v2（讨论稿）

> 产出日期：2026-08-20（调研代理，v2 方向修正版，覆盖 v1）
> v1 思路（围绕现有体系做增量优化）已被用户否决。v2 目标：**在软件中新增一个名为「科研助手」的 Agent 智能体模块**，ming 人格包含其中，为后续工作铺垫。
> 调研对象：https://github.com/liliMozi/openhanako （GitCode 镜像读取目录与源码）
> 本文是方案讨论稿，不实施、不改业务代码、不提交 git。

---

## 〇、openhanako Agent 运行时调研结论（本次新增）

上一轮已确认 ming 是人格模板。本轮调研其 **Agent 运行时组织方式**，可借鉴的架构思路（TS 源码，只看设计不搬代码）：

| openhanako 机制 | 出处 | 设计要点 | 我们借鉴什么 |
|---|---|---|---|
| **SDK 适配门面**：所有 Pi SDK 导入收敛在 `lib/pi-sdk/index.ts` 唯一入口，"不接受 engine 参数、不拼 session options、不持状态" | `lib/pi-sdk/index.ts` 头注释 | 第三方能力一律过门面，升级只改一处 | 我们的 `src/llm/client.py` 已是这个门面，agent loop 只准调它，不直接碰 requests |
| **低层 agent loop**：`runAgentLoop`（pi-agent-core）+ `createAgentSession`（pi-coding-agent），工具先注册为 customTools 再按名字 allowlist 启用 | 同上 | loop 与工具注册解耦，工具是"定义+执行函数"的对子 | 我们的 Python loop 同样做注册表：name → {schema, handler} |
| **工具结果契约**：`{content: [{type:"text", text}], details, isError?}`，统一 `toolOk/toolError` 构造器 | `lib/tools/tool-result.ts` | 工具返回结构统一，错误也是一等返回值 | Python 版照搬此契约（dict 返回），LLM 可读文本 + 结构化 details 双通道 |
| **SKILLS 机制**：SKILL.md + YAML frontmatter（name/description/disable-model-invocation/default-enabled），只有 frontmatter 进元数据，正文是提示内容 | `lib/skills/skill-metadata.ts` | 技能=Markdown 文档，零代码、可版本管理 | V2/V3 把"方案迭代方法论"写成技能文档挂给科研助手 |
| **记忆编译**：rolling-summary / fact-extraction / dream 提示词独立成文件；摘要两节制（用户画像事实 + 带时间戳的时间线），核心原则"宁可漏，不可错" | `lib/memory/prompts/rolling-summary.ts` 等 | 记忆编译是独立 LLM 任务，提示词与代码分离，格式契约独立文件 | 我们的记忆编译照此分层（见 §4） |
| **上下文压缩**：`prepareCompaction` + 统一 session-compactor | `lib/pi-sdk/compaction-request-shape.ts` | 长会话自动压缩 | V2 再做，MVP 用简单截断 |

---

## 一、模块定位与核心场景

**定位**：软件侧栏新增一级页面「科研助手」——一个有 ming 人格、能调用本系统真实能力（预测/检索/记录/建卡）的对话式 Agent。它不是替代页⑤方案迭代，而是其**下游深化入口**。

**主场景（必须做好）**：页⑤方案迭代跑出建议后，用户对方案有疑问（"为什么推荐这个温度？""这个单体组历史上失败过吗？"），点击「转科研助手深入讨论」→ 跳转科研助手页，自动携带：
- 当前单体组（醛/胺 CAS + 名称）
- 最新迭代建议（`data/rag_export/suggestions/sug_*.json` 最新若干条）
- 该组实验记录摘要（含时间线、自我总结、失误字段）
助手以此开场，给出第一段主动分析，用户继续追问。

**次场景**：侧栏直接进入的自由问答，助手可主动调工具查系统内数据（预测打分、图谱检索、实验记录、单体性质）。

---

## 二、Agent 架构（Python，跑在 FastAPI 后端）

### 2.1 总体结构

```
新增 src/agent/ 包（纯 Python，零新重型依赖）
├── loop.py          # agent 主循环：LLM ↔ 工具调用，max_rounds 限制
├── registry.py      # 工具注册表：name → {schema(JSON Schema), handler, 超时}
├── persona/
│   ├── ming_identity.md   # 复制自 openhanako lib/identity-templates/ming.md（含 Apache-2.0 来源声明头）
│   ├── ming_ishiki.md     # 复制自 lib/ishiki-templates/ming.md（同上）
│   └── domain_rules.md    # 我们的领域规则（§3）
├── tools/           # 每个工具一个文件，包一层现有模块
│   ├── predict.py / graphrag.py / records.py / monomers.py / cas.py / web.py / plan_card.py
├── memory/
│   ├── store.py     # 会话与跨会话记忆读写（用户数据目录）
│   └── compile.py   # 记忆编译（V2）
└── sessions.py      # 会话建档/载入（jsonl，参照 lib/session-jsonl.ts 的思路）

新增 api/routers/agent.py：POST /api/agent/chat（SSE 流式）、会话 CRUD、页⑤转入上下文注入
前端新增 webapp/src/pages/Assistant.tsx + 侧栏入口 + Iterate.tsx 转跳按钮
```

### 2.2 Agent Loop（核心循环）

```
用户消息 (+ 可选页⑤上下文注入)
   │
   ▼
组装 messages = [system(ming 人格 + 领域规则 + 记忆摘要), ...历史, user]
   │
   ▼
┌───────────── loop（max 8 轮）─────────────┐
│  src/llm/client.py chat_completion        │
│    ├─ 路径 A：模型支持 function calling    │
│    │   → 传 tools schema，读 tool_calls   │
│    └─ 路径 B（降级）：两段式 prompt        │
│        ①计划：输出 JSON {tool, args} 或    │
│          {final: ...}（提示词内嵌工具清单）│
│        ②解析后执行，结果回填再续写         │
│      ↓ 执行工具（注册表 dispatch，         │
│        统一 toolOk/toolError 契约）        │
│      ↓ 结果以 tool/user 角色回填           │
└────────────────────────────────────────────┘
   │
   ▼
最终回答（SSE 流式吐给前端）→ 会话落盘 jsonl
```

**路径 B（降级方案）是必备而非可选**：longcat/minimax 的 function calling 支持情况未经实测验证（见 §8 风险），MVP 即实现两段式降级，实测支持后切路径 A。

### 2.3 首批工具集（全部映射到现有真实能力）

| 工具名 | 参数 | 返回（text + details） | 实现来源（现有模块包一层） | 期次 |
|---|---|---|---|---|
| `predict_film` | aldehyde_cas, amine_cas, conditions(可选) | 成膜概率、走 tree/GNN 哪条路、OOD 标记 | `src/predictor/routing.py` + `tree_model.py`/`gnn_model.py` + `ood.py` | MVP |
| `query_graphrag` | query 或 cas 对 | GraphRAG v2 检索结果（社区/多跳证据摘要） | `minimax/bridge/search_local_pdfs.py` 的 `search()` / `format_results_for_prompt()` | MVP |
| `read_experiment_records` | aldehyde_cas+amine_cas 或 record_id | 该组实验记录时间线、结果、自我总结、失误 | `src/records/store.py` + `minimax/experiment/feedback_db.csv` | MVP |
| `get_monomer_props` | cas 或名称 | 单体物化性质 | `src/recommend/monomer_props.py` | V2 |
| `cas_resolve` | cas / 名称 / SMILES 互查 | 候选 SMILES、试剂库存与已购状态 | `search_local_pdfs.cas_to_smiles_candidates()` + `minimax/experiment/reagent_db.json` | V2 |
| `generate_plan_card` | 讨论结论结构化参数 | 方案卡落库 id 与链接 | `src/recommend/plan_card.py` + `generated_plans.py` | V2 |
| `web_search` | query | 联网搜索结果（标题+摘要+URL） | 新写薄封装（仅在联网时启用，默认关） | V3（可选） |

工具设计纪律（继承 openhanako）：每个工具返回 `{text（LLM 可读中文摘要）, details（结构化数据）, is_error}`；工具内不打印密钥、不吞异常（异常转 toolError 让 LLM 知道失败了）；执行加超时与参数白名单校验。

---

## 三、人格与规则

System prompt 三层拼装：

1. **ming 身份卡 + 人格定义**：复制 `lib/identity-templates/ming.md`、`lib/ishiki-templates/ming.md` 入库。**License 合规**：openhanako 为 Apache-2.0，允许复制修改，义务是保留声明——在两份 md 头部加注释：来源仓库、Apache-2.0、已修改说明。
2. **领域规则**（我们自写 `domain_rules.md`）：
   - 语境：COF 醛胺缩合成膜推荐；模型输出是先验概率，不是实验承诺（沿用 minimax README 口径）
   - OOD 单体组必须显式警告并降低措辞置信度
   - **引用纪律：凡涉及数据、文献、历史实验的论断，必须来自本轮工具返回；工具没查到的，明确说"系统内未查到"，禁止编造 CAS/文献/数字**
   - 建议落地方案时提示用户走 `generate_plan_card` 建档而非口头说完就散
3. **动态记忆摘要**（§4，V2 起注入）。

---

## 四、记忆设计

**两层**（借鉴 openhanako `lib/memory/` 分层，存储全部落用户数据目录，冻结分发时 `%APPDATA%/COF-Film-Recommend`）：

- **会话内**：jsonl 追加记录每轮消息与工具调用（参照 `lib/session-jsonl.ts`），重开可回放；超长先简单截断头部，V2 再上编译压缩。
- **跨会话**：每个单体组一个记忆文件（`agent_memory/<醛CAS>_<胺CAS>.md`），两节制——「事实」（该组已确认的偏好/结论/禁忌）+「时间线」（YYYY-MM-DD 讨论过大主题）。会话结束时由 LLM 编译更新，提示词独立成文件，原则照抄其 rolling-summary：**宁可漏，不可错；只记更新/反驳/强化，不重复抄写**。
- **与实验记录"自我总结/失误"的关系**：实验记录字段是**实验级事实**（人填的 ground truth，已入 GraphRAG 摄入）；Agent 记忆是**讨论级上下文**（这场讨论得出了什么倾向）。前者是工具的查询对象，后者是 system prompt 的注入对象，两者不互写，避免讨论噪声污染实验数据。

---

## 五、前端形态

- 新页 `Assistant.tsx`：消息列表 + 输入框 + 工具调用过程的可折叠展示（哪个工具、查到什么），参照 openhanako 的消息块思路但简化。
- **流式选 SSE 不选 WebSocket**：FastAPI `StreamingResponse` + 前端 `fetch` 读流即可，零新依赖、单向推送够用、PyInstaller/现有 uvicorn 部署无改动；WS 要管连接生命周期，复杂度不划算。
- **页⑤转入**：`Iterate.tsx` 建议卡上加「转科研助手深入讨论」按钮 → `POST /api/agent/sessions {source: "iterate", monomer_pair, suggestion_ids}` → 后端建档并生成开场上下文 → 前端跳转 `/assistant?session=xxx`，助手自动发出第一段分析。
- LLM 未配置时（`api/routers/llm.py` 已有 `env-status` 探针），页面显示引导去设置页配置的占位态，不崩不白屏。

---

## 六、约束过筛

| 约束 | 结论 |
|---|---|
| 单机离线可分发 | 通过。核心工具全部本地；web_search 默认关且可整体缺席；LLM 未配置时模块优雅禁用（设置页引导） |
| 无重型服务 | 通过。全部跑在现有 FastAPI 进程内，不引 Node/数据库服务，记忆用 md+jsonl 文件 |
| Windows 桌面 / PyInstaller | 通过。纯 Python + Markdown 资源文件；persona/记忆提示词按数据文件打包（参照现有 minimax 资源配置走法），写入一律走 `runtime_config.user_data_root()` |
| 科研人员维护 | 通过。提示词全是 md 文件可手改；工具一文件一工具，加工具=注册表加一行 |
| License | 通过。仅复制两份 ming md（Apache-2.0，头部保留来源声明）；不复制任何 TS 代码 |

---

## 七、分期实施路线

**MVP（约 5 人天）**
- src/agent/ 骨架 + 两段式降级 loop（路径 B）+ 工具注册表与契约
- 3 个工具：predict_film / query_graphrag / read_experiment_records
- ming 人格 + 领域规则 system prompt
- `/api/agent/chat`（SSE）+ Assistant.tsx 最小聊天页 + 页⑤转入按钮与上下文注入
- 验收：页⑤转进来，助手能基于注入上下文回答"这组做过哪些实验、结果如何"（调 read_experiment_records）、"这个条件组合打分多少"（调 predict_film），引用均能指回工具返回；LLM 未配置时优雅禁用。

**V2（约 6 人天）**
- 补齐 get_monomer_props / cas_resolve / generate_plan_card
- 实测 longcat/minimax function calling，支持则切路径 A（不支持则保留两段式并调优提示词）
- 跨会话记忆编译（会话结束触发，md 记忆文件注入 system）
- 会话内压缩、工具调用过程 UI 完善
- 验收：跨天再问同一单体组，助手记得上轮结论；讨论结论可一键 generate_plan_card 落库并在方案卡页可见。

**V3（约 4 人天，可选）**
- web_search（联网时）、SKILLS 机制（把方案迭代方法论写成 md 技能挂接）
- 主动建议（如检测到新失误记录时提示"要不要回顾一下这组的历史讨论"）
- 讨论自动建档回链到实验记录（只写侧车引用，不动实验数据本体）
- 验收：技能 md 改动无需改代码即改变助手行为；主动建议不误报。

---

## 八、风险与开放问题

1. **LLM 工具调用能力参差（最大技术风险）**：longcat/minimax 是否稳定支持 OpenAI function calling 未实测。对策：MVP 即带两段式降级（计划 JSON → 执行 → 续写），并在 V2 首日做实测切流；两段式的 JSON 解析要做容错（正则提取 + 失败重问一次）。
2. **token 成本**：工具结果可能很长（GraphRAG 证据）。对策：工具返回 text 限长截断、走 `src/llm/client.py` 现有缓存、记忆编译借鉴其 `llm-budget.ts` 思路设单次上限。
3. **幻觉/编造引用**：靠 §3 引用纪律 + 回答后校验（正则扫 CAS 号，凡出现的 CAS 必须能在本轮工具 details 里找到，找不到则打回让模型改写）。
4. **人格与格式稳定性**：ming 文风克制，但生成方案卡参数时需结构化输出——结构化走工具参数（JSON Schema 约束），人格只影响自然语言部分，二者隔离。
5. **开放问题**：①记忆文件是否允许用户在 UI 查看/编辑/钉选（建议 V2 做只读查看，编辑后置）；②页⑤注入上下文的长度上限与脱敏（不含密钥，现有红线不变）；③多用户/多课题组场景暂无，记忆按单体组隔离是否够用。

---

## 附：调研依据（本回合实际抓取）

- openhanako 源码（GitCode 镜像）：`lib/pi-sdk/index.ts`（SDK 门面与 loop 组织）、`lib/tools/tool-result.ts`（工具契约）、`lib/skills/skill-metadata.ts`（SKILL.md frontmatter）、`lib/memory/prompts/`（rolling-summary/fact-extraction/dream/compile 四件套，rolling-summary 全文）、`lib/tools/`、`lib/pi-sdk/` 目录清单
- ming 人格原文（上一轮）：`lib/identity-templates/ming.md`、`lib/ishiki-templates/ming.md`
- 本仓库盘点：`src/llm/client.py`、`src/rag/suggestions.py`、`src/predictor/`、`src/recommend/`、`src/records/`、`minimax/bridge/search_local_pdfs.py`、`api/routers/`（llm.py 已有 env-status 探针）、`webapp/src/pages/`
