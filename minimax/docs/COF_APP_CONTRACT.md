# COF App 对接契约（RAG 侧视角）

> 2026-07-21 · 对应 App 侧 `全新机器学习实验/docs/APP_REDESIGN_PROPOSAL.md` 第 6 节
> 唯一权威 schema 定义在 App 侧 `data/rag_export/README.md`，本文件是 minimax 消费指南

## 定位

全新机器学习实验（Gradio App，tree_v4 路由 + GNN v5.3）是**标准化数据生产者**；
minimax 是**迭代智能主体**。阶段 1 采用文件对接，零耦合：

```
[COF App]                                    [minimax]
data/rag_export/predictions/  ──读取──→  adapters/cof_app_ingest.py
data/rag_export/records/      ──读取──→        ↓ 校验 + 转换
data/rag_export/suggestions/  ←─写入───  bridge/（检索 + LLM 生成）
```

## 读哪些目录

| 目录（App 侧） | 内容 | minimax 用途 |
|---|---|---|
| `data/rag_export/predictions/` | 每次打分快照（score ± std、路由臂、OOD、SHAP Top±） | 先验 vs 实际对比、候选池维护 |
| `data/rag_export/records/` | 用户真实实验记录（条件 + 结果 + 失败分类） | 攒批回流 feedback_db、RAG 证据 |
| `data/rag_export/suggestions/` | **minimax 写入**：迭代建议，App 页⑤回显 | 本系统产出 |

默认根路径 `C:\Users\ckx\Desktop\全新机器学习实验\data\rag_export`，
可用环境变量 `COF_APP_RAG_EXPORT` 或 CLI 参数覆盖。

## 关键字段含义（与 minimax 既有概念对照）

| rag_export 字段 | 含义 | minimax 对应 |
|---|---|---|
| `prediction.score` / `score_std` | **成膜打分（倾向性）**，树模型 bagging ± 认知不确定度 | ⚠ 不是 `tianxuan_预测概率`（GNN），语义不同，不混用 |
| `prediction.arm` | `tree_v4`（池内 TE 先验）/ `tree_v4_noTE`（双未见外推） | 无对应概念，新增信息 |
| `prediction.ood.level` | `none` / `warning`（外推警告）/ `out`（模型不适用，score 必为 null） | 无对应概念，**out 的记录打分不可信，禁止当先验用** |
| `record.outcome` | `film` / `partial` / `failed`（三档，App UI 口径） | 映射到失败 Class（见下） |
| `record.failure_class` | **A–G，与 `experiment/failure_criteria.md` 完全同一套** | `feedback_db.csv 失败Class` 列 |
| `record.conditions` | 溶剂/调制剂/催化剂/温度/时间/容器/加料顺序 | feedback CSV 无独立列 → 适配器并入备注 |
| `record.minimax_plan_no` | 对应的 minimax 方案编号（可空） | `方案编号` 列，互查用 |
| `suggestion.*` | 条件调整 / 新候选建议 + 证据引用 | 本系统产出格式 |

## outcome → 失败 Class 映射（record.failure_class 缺失时适配器兜底）

| outcome | 默认 Class | 说明 |
|---|---|---|
| film | F | 成膜（强度描述好时可人工升 G） |
| partial | E | 部分成膜 / 膜质量差 |
| failed | C | 失败（有产物但无定形；完全无产物应填 A，靠 failure_class 精确化） |

**优先使用 record.failure_class**（用户或 App 填的 A–G），上表仅是缺失时的保守兜底。

## 产出：写 suggestions/ 的格式

每个建议一个 JSON 文件 `sug_YYYYMMDD_NNN.json`，schema 见 App 侧 README（Schema 3）。要点：

- `type`：`condition_adjust`（改条件）/ `new_candidate`（推荐新单体对）/ `literature`
  （降级建议：LLM 失败、输出无法解析或建议全被去重时，只含检索证据原文，固定 1 条）
- `batch`（可选）：本次运行批次号 `batch_YYYYMMDD_HHMMSS`，同次运行所有建议共用
- `payload.confidence`（可选）：置信度自评 `{"level": high/medium/low, "reason"}`；
  **0 条白名单内有效证据的建议强制降为 low** 并在 reason 标注
- `payload.unverified_refs`（可选）：白名单校验剔除的引用原文 `[{"kind","ref","note"}]`，
  仅在有剔除时出现，供人工复核
- `evidence_refs` 必填且尽量挂本系统证据：`experiment_record`（rec_id）、`literature`（DOI 或知识库路径）、`prediction`（pred_id）。
  写入前经**白名单校验**（见下）
- `status` 初始写 `new`；之后 `adopted`/`rejected`/`done` 由 App 侧回写，**minimax 读取时应尊重该状态**（已 rejected 的方向不要重复建议）
- 未知 `schema_version` 的文件跳过，不报错

### 当前生成器行为（`adapters/iterate_suggest.py`，2026-07-22）

- **检索降级链**：search_local_pdfs 五路召回 → GraphRAG 图检索 → 多跳 BFS → failure 专家语料
  （failure_criteria / failure_playbook），任一路失败静默降级，不阻断流程
- **白名单校验**：LLM 产出的 `evidence_refs` 中，kind=experiment_record 的 ref 必须命中
  真实 record_id 白名单，kind=literature 的 ref 必须命中实际检索到的文献名/图节点 ID；
  先做大小写/空白/子串模糊纠正，匹配不上整条剔除并记入 `payload.unverified_refs`
  （绝不让编造引用静默通过）
- **LLM 失败降级**：写一条 `type=literature` 降级建议（只含检索证据），退出码仍为 0

## 适配器

`adapters/cof_app_ingest.py`（独立可运行脚本，stdlib 即可）：

```powershell
# 干跑：只校验 + 打印摘要，不写任何文件（默认）
python adapters/cof_app_ingest.py

# 实际转换：输出到 bridge/cof_app_import/（全新目录，不覆盖任何现有文件）
python adapters/cof_app_ingest.py --apply
```

产出（`--apply` 时，文件名带日期，重复运行不覆盖）：

- `bridge/cof_app_import/feedback_rows_<date>.csv` — records 转成的 feedback_db.csv 同表头行，
  **人工核对后再追加**进 `experiment/feedback_db.csv`（适配器绝不直写 feedback_db.csv）
- `bridge/cof_app_import/predictions_<date>.jsonl` — predictions 统一成一行一条的先验记录，
  供 search_local_pdfs.py 类检索层扩展消费

## 注意

- App 的 score 是"倾向性"不是成功率承诺（与 tianxuan 概率同一原则：实验成败不反噬模型）
- records 的 `record_id` 是独立编号体系，**不得**与训练样本 id 混用（防 CV 泄漏教训）
- 契约演进以 App 侧 `data/rag_export/README.md` 变更日志为准
