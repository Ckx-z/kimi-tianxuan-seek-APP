# GraphRAG 技术选型评估与 P0 闭环接入报告

> 日期：2026-08-20
> 前置文档：`docs/GraphRAG对标评估.md`（结论：6 大标杆能力"建成未接线"，P0=把 v2 检索接进迭代管线 + 实验记录入图）
> 本报告对应任务两步：① 技术选型评估（本章结论）② P0 闭环接入实现与验证。

---

## 1. 技术选型评估结论

**结论：现有自研 GraphRAG（NetworkX + pickle + 规则 nl2graph + 可选 embedding 重排）已适配本项目全部硬约束，继续自研接线；切换到 LangChain / MS GraphRAG / Neo4j 等方案的代价显著大于收益。**

### 1.1 本项目硬约束（核实自代码与分发配置）

- 单机离线 Windows 桌面分发（PyInstaller frozen + Electron；`scripts/cof-backend.spec` 把 `graph.pkl/graph_v2.pkl/lit_embeddings.jsonl` 打进包，包内只读）
- 图谱规模 9066 节点 / 23920 边（v2 图 11820 节点含社区节点），纯内存可毫秒级遍历
- LLM 走 OpenAI 兼容端点（longcat 等，`minimax/bridge/llm_client.py`），检索层刻意不依赖 LLM
- 实验数据量极小（records 几十条）
- 维护者为科研人员非工程师；禁止重型服务（Neo4j / 向量数据库）

### 1.2 对比表

| 维度 | 自研 GraphRAG（现状） | LangChain / LangGraph | MS GraphRAG 库 | Neo4j + 向量库 |
|---|---|---|---|---|
| 功能匹配度 | 6 大标杆能力代码已建成（router/community/importance/multimodal/nl2graph/reasoning），缺口是"接线"而非能力 | **编排框架，不是 RAG 方案本身**：图检索、nl2graph、降级链仍需自建 | 面向大规模非结构化语料的 LLM 抽取+社区摘要管线 | 功能完备但远超需求 |
| 部署分发成本 | 纯 Python + pickle，PyInstaller 直接打包，离线零服务 | 重依赖链（langchain-core/pydantic 生态），frozen 打包体积膨胀 + 版本冲突风险 | 索引期需大量 LLM 调用，违反离线与成本约束 | 需常驻数据库服务，**直接违反约束** |
| 维护成本 | 模块均为百行级单文件，科研人员可读可改 | 抽象层级深、版本 churn 快，调试需理解框架内部 | 黑盒管线，参数体系面向英文通用语料 | 需要 DBA 式运维 |
| 社区生态 | 不依赖（自包含） | 生态最大，但生态解决的是"编排"，不解决本项目缺口 | 面向论文复现场景 | 生态成熟但重 |
| 小数据+单机+科研迭代适配 | 9k 节点内存图全扫描毫秒级；几十条实验记录增量入图刚好 | 为小数据引入大框架，本末倒置 | 为百万 token 级语料设计，小数据上社区摘要无意义 | 杀鸡用牛刀 |

### 1.3 关于 LangChain 的特别说明

LangChain 与本项目现有体系是**不同层**的东西：它是 LLM 调用/工具编排层，而本项目的核心价值在检索底座（图谱 + 规则解析 + 五路召回 + 白名单防编造 + 全链路降级）。引入它的真实代价 = 新重依赖 + frozen 打包风险 + 需重写已验证的降级链与校验逻辑，换来的主要是 prompt 模板与会话管理——对"小数据+单机+离线分发"场景收益近零。若未来确需编排能力，也应以单点 utility 形式引入而非替换检索底座。

### 1.4 评估结论落地

评估确认"换框架代价大于收益"→ 继续第二步 P0 接入。真正的缺口是工程接线：① 把 v2 检索接进迭代管线；② 实验记录增量入图。

---

## 2. P0 闭环接入实现说明

改动文件（全部在 `minimax/`，未触碰 webapp/ api/ electron/ src/；未提交 git）：

| 文件 | 改动 |
|---|---|
| `minimax/bridge/user_graph.py` | **新增**。用户实验记录增量侧车图：记录→`EXP-<record_id>` 节点（`node_type='reaction'` + `source='user_experiment'`，携带 outcome/failure_class/mistakes/self_summary/timeline/process_notes），边沿用包内 edge_type 方案（uses_aldehyde/uses_amine/uses_solvent/uses_catalyst/produces）；ID 方案与 `build_graphrag.py` 完全一致（md5），同 SMILES 单体自动对齐包内节点。`append_records()` 幂等增量（同 record_id 覆盖更新）；`merge_into()` 运行时合并（包内已有节点属性不被覆盖，只补边） |
| `minimax/bridge/query_graphrag.py` | `load_graph(app_root=None)`：**优先 graph_v2.pkl**（已实测 v2 是 v1 节点 ID 的全类型超集：reaction/monomer/literature/solvent/catalyst/interface/outcome 交集 100%），随后合并用户侧车图；`query(..., G=None)` 支持调用方传入预加载图；`REACTION_SCAN_FIELDS` 扩展用户实验字段（对包内节点零影响）；用户实验节点命中加 `USER_EXP_BONUS=2.0` 排序加权，防止被 6000+ 文献反应挤出 top-k |
| `minimax/adapters/iterate_suggest.py` | **retrieve_evidence 第 2 路重写**：统一加载一次合并图 → v2 链（nl2graph 解析 → router 意图路由 → 策略执行 → multimodal 4 路融合重排文献）→ global/temporal 等不返 reactions 的策略自动补 local 基线防证据断档 → 社区摘要块 → 同图多跳（优先复用 relational 路由已算路径，跳过 belongs_to 社区死胡同边）。**v2 任何失败降级 v1 直查**（同一合并图），图缺失/缺 networkx 整体静默跳过——降级链不变。用户实验命中以【我的实验记录 rec_xxx】单独格式化并纳入引用白名单 |
| `minimax/bridge/graphrag_v2/__init__.py` | `GraphRAGv2.load_graph()` 改走 `query_graphrag.load_graph()`（v2 优先+侧车图合并）；`query()` 内 v1 调用统一在 self.G 上执行，消除 ID 不一致隐患 |
| `minimax/bridge/graphrag_v2/reasoning.py` | `multi_hop_paths()` 新增 `skip_edge_types` 参数；`format_paths()` 渲染 EXP 节点为 `EXP[rec_xxx outcome=failed]` |
| `minimax/bridge/graphrag_v2/nl2graph.py` | FAILURE_DICT 的 outcome 过滤值补 `'failed'/'partial'`（用户实验记录取值），失败诊断提问时用户失败记录获得 +3 过滤加分排前 |
| `minimax/adapters/cof_app_ingest.py` | `--apply` 时增量入图：写 CSV/JSONL 后调 `user_graph.append_records()`；新增 `--app-root`（侧车图位置，缺省按 COF_DATA_DIR > frozen %APPDATA%/COF-Film-Recommend > 项目根解析）与 `--no-graph`；入图失败不阻断摄入；dry-run 提示将入图条数 |
| `minimax/adapters/test_user_graph.py` | **新增 6 个测试**：记录→图元素、幂等增量、合并保留包内节点、检索命中用户失败记录（含 top-5 排序）、retrieve_evidence v2 链引用用户记录、v2 不可 import 时降级 v1 且证据不断档 |

### 分发模式安全性

- 侧车图写 `<app_root>/data/graphrag_user/graph_user.pkl`：frozen 时 app_root = %APPDATA%/COF-Film-Recommend（可写用户数据目录，API 层已按此传 `--app-root`）；包内 `graph.pkl/graph_v2.pkl` 只读，**运行时只在内存合并，绝不回写**。
- 增量更新非全量重建：几十条记录毫秒级完成；幂等覆盖，无重复节点/边。

## 3. 端到端验证输出（真实运行，E:/python3.12）

流程：造"成膜失败+失误总结"记录 `rec_20990315_001`（outcome=failed, mistakes="升温过快未分段保温…", self_summary="成膜失败，怀疑成核速率过快…"）→ `cof_app_ingest --apply` → `iterate_suggest --record-id rec_20990315_001 --question "这个组合失败了怎么改"`。

```
✓ 侧车图谱增量入图: 1 条实验记录 -> <tmp>/data/graphrag_user/graph_user.pkl
  meta: n_nodes=7, n_edges=6, n_experiment_nodes=1
[query_graphrag] 合并用户实验侧车图: 1 条实验记录节点
[iterate_suggest] GraphRAG v2 检索: intent=failure_diagnosis reactions=30 literatures=10 communities=0
[iterate_suggest] 多跳推理: 路径 10 条
{"written": ["sug_20260820_001"], "count": 1, "batch": "batch_20260820_091307"}
```

写出建议 `sug_20260820_001.json` 中：

- **该失败记录在 GraphRAG 证据中排名第 1**（24.0★，高于全部文献反应）：
  `- [24.0★] 【我的实验记录 rec_20990315_001】(图节点 EXP-rec_20990315_001, 实验编号 E2E-1) outcome=failed 失败分类=Class C | … 失误 升温过快未分段保温… | 自我总结 成膜失败，怀疑成核速率过快…`
- `evidence_refs` 含 `{"kind": "experiment_record", "ref": "rec_20990315_001"}`（白名单内真实 ID）
- 多跳路径从用户失败实验出发：`EXP[rec_20990315_001 outcome=failed] → M[Benzene-1,4-dicarbox…] → …`（侧车图单体与包内图对齐成功）
- LLM 端点当时返回 402 → 走降级建议（设计内行为），降级建议同样携带上述证据
- 包内 `graph.pkl / graph_v2.pkl` sha256 前后一致（未被修改）；e2e 假数据 CSV 与临时目录已清理

### 测试回归

| 套件 | 基线 | 现在 |
|---|---|---|
| `E:/python3.12/python.exe -m pytest`（minimax 目录） | 27 passed + 1 预存收集错误（bridge/test_integration.py fixture 问题，与本次无关） | **33 passed**（+6 新增）+ 同一预存错误 |
| `E:/ANACONDA/python.exe -m pytest tests/ -q` | 394 passed | **402 passed**（+8 来自另一代理新增的 tests/test_records_full_edit.py；无失败） |

## 4. 遗留风险与后续建议

1. **多跳方向性**：包内图边方向为 reaction→单体/溶剂/文献，EXP 节点出发的 BFS 走到单体后只能沿 monomer_cooccurs 到同类单体，无法反向走到"文献里相似但成功的反应"。后续可让 `multi_hop_paths` 支持无向遍历（`G.to_undirected()` 视图）或补反向边。
2. **importance/embedding 对用户节点缺省**：侧车图节点无 PageRank importance（按 0 计）、无文献 embedding；目前靠 USER_EXP_BONUS 与 outcome 过滤加分补偿。数据量小，影响可控。
3. **社区摘要不覆盖用户增量**：社区节点是离线构建的包内资产，用户新实验不属于任何社区；global 意图的社区摘要不含用户记录（但 local 基线兜底保证其仍被命中）。重建社区需全量重跑，不符合增量约束，暂不做。
4. **社区摘要/路径总结仍是模板非 LLM**（对标评估 P3），可在离线构建阶段批量生成，未在本次范围。
5. **embedding rerank 依赖 MiniMax 在线 API**：端点不可达时已有的静默降级继续生效（本次 e2e 中亦如此），但对"纯离线"场景 embedding 路天然不可用——这是既有设计，未改变。
6. **dev 模式侧车图落项目根 `data/graphrag_user/`**：`*.pkl` 已被根 .gitignore 覆盖，不会误入库；若项目根被打成只读镜像运行，请用 `COF_DATA_DIR` 指向可写目录。
7. `minimax/bridge/test_integration.py` 的 pytest 收集错误为**预存问题**（函数签名 `test(name)` 缺 fixture），本次未修复，建议后续单独处理。
