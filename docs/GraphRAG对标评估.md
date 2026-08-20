# GraphRAG 迭代系统对标评估

> 评估日期：2026-07-25
> 评估范围：`minimax/`（bridge/ 检索与图谱、adapters/ 数据契约摄入、graphrag_v2/ 升级层）
> 评估依据：仓库内实际代码与文档；所有结论均标注文件路径。

---

## 1. "那篇 Nature" 指的是什么

仓库中标杆文献的明确出处只有两处：

- `minimax/bridge/graphrag_v2_plan.md:5`：**"标杆: Nature Comm 2026 (s41467-026-69549-z.pdf)"**
- `minimax/progress.md:49-50` 与 `minimax/experiment/daily/2026-07-15_ai.md:106-107`："✅ Nature Comm 2026 文献评估：GraphRAG vs 我们 RAG (gap 分析)"

**重要声明：该 PDF（s41467-026-69549-z.pdf）不在仓库内**（全盘检索仅找到另一篇无关的 `minimax/知识库/文献阅读/在基底上生长薄膜/.../s41467-022-29050-9.pdf`），仓库中也没有该论文的章节摘录或工作流描述。`progress.md:118` 还留着待办"知识库 RAG 全文索引：等用户对 Nature 2026 详细讨论后实施"。

因此本评估的对标分两层：

1. **仓库内可考的标杆**：`graphrag_v2_plan.md` 第 2 节列出的 6 大能力（多跳推理、分层社区、查询路由、NL2Graph、节点重要性、多模态融合）——这是团队自己从那篇 Nature Comm 2026 提炼的 gap 清单，可视为"文献要求"的二手记录。
2. **通用范式**：LLM + GraphRAG 材料发现闭环的常见范式（Nature Synthesis / Nature Communications 2024 以来 LLM 辅助材料设计类工作的通行流程）：**知识图谱构建 → 图谱证据检索增强 → LLM 假设/方案生成 → 实验执行 → 结果分析回流（图谱与模型双更新）→ 主动学习选下一轮（DMTA 式闭环）**。以下对标标注为"通用范式"的部分均非特定论文内容。

---

## 2. 我方实现现状（基于代码）

### 2.1 数据从哪进

- **图谱数据源（一次性离线构建）**：`minimax/bridge/build_graphrag.py` 从 tianxuan-seek 数据（954 篇文献 YAML、1059 单体、6197 反应）构建 NetworkX MultiDiGraph，7 类节点 / 8 类边（`graphrag/meta.json`：monomer 1058、reaction 6197、literature 954、solvent 709、catalyst 158、interface 5、outcome 4）。
- **App 实验数据**：走契约目录 `data/rag_export/`（predictions/ records/ suggestions/），权威定义见 `minimax/docs/COF_APP_CONTRACT.md`。
- **回流通道**：`minimax/adapters/cof_app_ingest.py` 把 records 校验后转成 feedback_db 同表头行，**默认干跑，`--apply` 也只写 `bridge/cof_app_import/` 新文件，人工核对后才追加 `experiment/feedback_db.csv`**（文件 docstring 明确"绝不直写"）。

### 2.2 图谱结构

- v1：`graphrag/graph.pkl`（9066 节点 / 23920 边），节点类型含 monomer/reaction/literature/solvent/catalyst/interface/outcome。
- **关键事实：自己的实验记录（records / feedback_db）不是图节点**。图谱至今是纯文献+预测池数据，实验反馈只走 failure 专家语料（`experiment/failure_criteria.md` / `failure_playbook.md` 按 Class/实验号抽段，见 `iterate_suggest.py:231-300`）和 prompt 内联记录两条路进 LLM，不进图。

### 2.3 检索怎么做

- `bridge/query_graphrag.py`：硬编码关键词 + nl2graph 规则解析（`graphrag_v2/nl2graph.py`，规则+关键词，LLM 可选增强）→ 文本打分 + 结构化过滤软加权（`filter_bonus`）+ importance(PageRank/betweenness) 惰性加权 + embedding 余弦重排（`embedding_rerank.py`）+ 零结果逐词兜底。
- v2 升级层 `graphrag_v2/`：router（意图路由）、community（greedy modularity 两层社区）、importance、multimodal（4 路融合）、reasoning（3 跳 BFS 多跳）。**但 `reasoning.py:71` 的路径总结是模板拼接，无 LLM；社区摘要在 `community.py` 里是 `top_text` 截断而非 LLM summary**。
- **集成缺口**：`iterate_suggest.py` 实际只调用了 v1 `query_graphrag.query()` + `graphrag_v2.reasoning.multi_hop_paths`（`iterate_suggest.py:354-402`），router/community/multimodal 主类 `GraphRAGv2`（`graphrag_v2/__init__.py`）未接入迭代编排器。代码内注释也自认"用 graph.pkl（v1 图）跑多跳，与 graph_v2.pkl 节点 ID 可能不一致"（`iterate_suggest.py:387`）。

### 2.4 LLM 在哪一环

LLM 只出现在**建议生成**一环（`adapters/iterate_suggest.py`）：模板拼查询 → 五路召回（`search_local_pdfs.search`）+ GraphRAG 图检索 + 多跳 BFS + failure 专家语料 → 拼 prompt → `llm_client.chat_completion` → 容错 JSON 解析 → **evidence_refs 白名单校验（模糊纠正/剔除编造引用，`normalize_evidence`）+ confidence 规则校验（0 条有效证据强制 low，`normalize_confidence`）+ 已否决方向去重（`load_rejected_directions`/`is_rejected_direction`）** → 落盘 suggestions JSON。LLM 失败写降级建议（`write_fallback_suggestion`）。检索侧基本不依赖 LLM（降级链设计，任何一路失败静默跳过）。

### 2.5 建议如何生成与采纳

- 建议写入 `data/rag_export/suggestions/sug_*.json`，`status: new`；**采纳/否决完全由人在 App 页⑤操作**（`docs/APP_ITERATION_PLAN_20260722.md:52` 明确"你确认后写入……可标 adopted/rejected"）。
- 人在环路的防重复机制：status=rejected 的方向不再重复推荐。

### 2.6 实验结果如何回流

实验记录 → App 导出 records → `cof_app_ingest.py` 校验/干跑 → 人工核对 → `experiment/feedback_db.csv` → 作为检索语料和 prompt 证据参与下一轮建议。**不回图谱、不自动回灌 ML 模型**；`docs/WORKFLOW_ALIGNMENT.md:101-103` 明确这是刻意分工："闭环错位：主动学习不该在我们项目里重造……RAG 的实验记录再回流为高质量训练数据"，即回流训练是约定方向但尚未实现自动化。

---

## 3. 逐环节对标表

| 环节 | 文献/通用范式要求 | 我方实现 | 状态 |
|---|---|---|---|
| ① 知识图谱构建 | 文献结构化抽取成图，实体/关系丰富 | 7 节点 8 边、9066/23920，`build_graphrag.py` | ✅ 已对齐 |
| ② 图谱增强检索 | 多跳、社区摘要、NL 查询、重要性加权、多路融合（Nature Comm 2026 标杆 6 能力，`graphrag_v2_plan.md` §2） | v2 六模块代码齐备，但**只有多跳 BFS 接入了迭代管线**；社区/路由/多模态在主类里空转；社区摘要与路径总结是模板非 LLM | ⚠️ 半对齐（建成未接线） |
| ③ LLM 假设/建议生成 | 基于图谱证据生成可操作假设，引用可溯源 | `iterate_suggest.py`：证据三链路 + 白名单校验 + 置信度规则 + 降级兜底，引用防编造做得很扎实 | ✅ 已对齐（甚至是亮点） |
| ④ 实验规划（条件级） | 给出具体可执行条件（溶剂/温度/调制剂） | condition_adjust 建议带 字段/原值/改为/理由；与侯老师法方案卡结合（`docs/WORKFLOW_ALIGNMENT.md` §2） | ✅ 已对齐 |
| ⑤ 实验执行 | 人执行（本场景合理） | 化学家执行，记录进 App | ✅ 合理裁剪 |
| ⑥ 结果分析与失败分类 | 失败结构化归因并参与迭代 | failure_class A–G 判据（`experiment/failure_criteria.md`）+ playbook 注入 prompt；失败记录明确拼入检索查询（`iterate_suggest.py:205-208`） | ✅ 已对齐 |
| ⑦ 结果回流图谱 | 新实验作为节点/边更新图谱，图谱随迭代生长 | ❌ 实验记录不进图，只在 CSV/语料层；graph.pkl 是 2026-07-13 静态构建 | ❌ 缺失 |
| ⑧ 主动学习 / 不确定性引导选下一轮 | 用模型不确定性 + OOD 指导下一批实验选择（通用 DMTA 范式） | App 侧已输出 score ± std + OOD 标记（`docs/PREDICT_CONTRACT.md`），但迭代建议侧**不消费不确定性**，选下一个实验靠人+LLM 定性判断 | ⚠️ 弱化（且属刻意裁剪，见 `WORKFLOW_ALIGNMENT.md:101`） |
| ⑨ 闭环自动化程度 | 全自动 self-driving（通用范式）/ 人机协同 | 明确人在环路：建议须人工 adopt/reject，摄入须人工核对 | ✅ 合理裁剪（湿实验场景） |
| ⑩ 模型随迭代更新 | 实验数据回灌重训 ML 模型 | 契约预留（predictions 快照含 model_version），未实现自动回灌 | ❌ 未实现 |

---

## 4. 差距与改进建议（按优先级）

1. **P0 — 把 v2 检索能力接进迭代管线**（最大性价比）。社区/路由/多模态已建成却在 `GraphRAGv2` 主类里空转，`iterate_suggest.retrieve_evidence` 仍走 v1。建议把 `graphrag_v2/__init__.py` 的 `GraphRAGv2.query()` 替换 `iterate_suggest.py:360` 的 v1 调用，并统一 graph.pkl / graph_v2.pkl 节点 ID（消除 `iterate_suggest.py:387` 自认的不一致隐患）。
2. **P1 — 实验记录入图，闭环到图谱层**。把 `data/rag_export/records/`（含失败 outcome + failure_class）作为 reaction 节点增量写入图谱（边：uses_monomer / produces / cited_in=内部记录），使多跳推理能直接从"我失败的 A1"走到"文献里相似但成功的反应"。这是相对通用范式⑦的最大缺口，也是 GraphRAG 区别于普通 RAG 的核心价值。
3. **P2 — 让建议消费不确定性**。检索查询和 prompt 中已有单体 OOD/MC std（predictions 快照就在契约目录），把"该单体对 OOD=high、std 大"作为建议生成的显式上下文，让 LLM 在低置信区域倾向建议"先做小规模验证/阳性对照"而非直接调条件——这比全自动主动学习更符合本场景（17 个组合已圈定，`WORKFLOW_ALIGNMENT.md:99`）。
4. **P3 — 社区摘要/路径总结接入 LLM**。`reasoning.py:71` 与 `community.py` 目前是模板/截断，标杆论文的 global search 价值（"总结所有含氟体系"类问题）依赖 LLM 摘要；可在离线 `build_v2_indexes.py` 阶段批量生成，不增加在线时延。
5. **P4 — 回流训练半自动化**。`cof_app_ingest.py` 的人工核对是合理的质量闸，但可在核对通过后自动触发模型重训或至少生成"新增样本 diff 报告"，兑现 `WORKFLOW_ALIGNMENT.md:103` 的约定。

---

## 5. 结论

**是否符合"那篇 Nature"的流程？** 无法逐字核验——仓库只记录了标杆是 Nature Comm 2026（s41467-026-69549-z，`graphrag_v2_plan.md:5`）及团队自拟的 6 大能力 gap 清单，论文原文不在库内。就该清单而言：**6 大能力代码层面全部建成，但只有 1.5 个（重要性加权 + 多跳 BFS）真正跑在迭代闭环里，检索层是"建成未接线"状态**；图谱不随实验生长，与"闭环迭代更新知识图谱"的文献范式存在实质差距。

**我们的 GraphRAG 设计是否合理？** 合理，且有几处超出通用模板的优点：evidence_refs 白名单防编造、置信度规则校验、已否决方向去重、全链路降级兜底（永不整体失败）、失败实验结构化分类并参与迭代——这些是湿实验场景下很成熟的设计。裁剪也大多有据：人在环路、不自建主动学习、不追求全自动，都是 `docs/WORKFLOW_ALIGNMENT.md` 里明确的战略决策而非疏漏。**真正需要补的不是方向而是两件工程事：把 v2 检索接进管线（P0）、让实验记录进图（P1）**。补上这两点，本系统与 LLM+GraphRAG 材料迭代闭环的通用范式即基本对齐（差异仅剩"全自动 vs 人机协同"，而那是合理裁剪）。
