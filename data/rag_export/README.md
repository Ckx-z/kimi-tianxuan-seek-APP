# data/rag_export — App ↔ RAG（minimax）数据契约

> 版本：schema_version **1.0** · 2026-07-21
> 依据：`docs/APP_REDESIGN_PROPOSAL.md` 第 6 节细化
> 消费方：`C:\Users\ckx\Desktop\minimax`（shiyandiedai RAG 项目），对接文档见其 `docs/COF_APP_CONTRACT.md`

## 定位

本目录是 App 向 RAG 迭代系统的**标准化数据出口**（阶段 1：文件对接，零耦合）。

- App 侧写入：`predictions/`（每次打分的快照）、`records/`（用户上传的真实实验记录）
- RAG 侧写入：`suggestions/`（RAG 产出的迭代建议，App 页⑤回显）
- 双方**只新增文件，不改他人文件**；每个 JSON 一个文件，文件名即主键
- 所有文件必须带 `schema_version` 与 `record_type` 字段；消费方遇到未知 `schema_version` 应跳过而非报错

```
data/rag_export/
├── README.md                ← 本文件（唯一权威契约）
├── predictions/             ← App 写 / RAG 读   pred_<YYYYMMDD>_<NNNN>.json
├── records/                 ← App 写 / RAG 读   rec_<YYYYMMDD>_<NNN>.json
└── suggestions/             ← RAG 写 / App 读   sug_<YYYYMMDD>_<NNN>.json
```

## 通用约定

- 编码 UTF-8；时间戳 ISO 8601 带时区（`2026-07-21T14:30:00+08:00`）；日期 `YYYY-MM-DD`
- 单体统一用对象表示：`{"smiles": "...", "cas": "...", "name": "..."}`，smiles 必填，cas/name 可空字符串
- 打分口径为「成膜打分（倾向性）」，字段名用 `score`，**不叫 probability**，不解释为实验成功率承诺
- `null` 表示"不适用/未出分"，空字符串表示"未知"，缺省字段视为未提供

---

## Schema 1：prediction（App → RAG）

每次查询/批量打分落一条。RAG 用它做"先验 vs 实际"对比与候选池维护。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| schema_version | string | ✓ | 当前 `"1.0"` |
| record_type | string | ✓ | 固定 `"prediction"` |
| prediction_id | string | ✓ | `pred_YYYYMMDD_NNNN`，主键 |
| aldehyde / amine | object | ✓ | 单体对象（smiles 必填） |
| score | number\|null | ✓ | 成膜打分（倾向性），0–1；**ood.level=="out" 时必须为 null** |
| score_std | number\|null | ✓ | bagging 集成成员 std（认知不确定度） |
| arm | string | ✓ | 路由臂：`tree_v4`（池内 TE 先验）/ `tree_v4_noTE`（双未见外推）/ `none`（未出分） |
| arm_reason | string | ✓ | 路由原因一句话（中文） |
| ood | object | ✓ | `{"level": "none"\|"warning"\|"out", "reasons": [str]}`。out=模型不适用；warning=外推警告 |
| shap_top | array | ✓ | Top± SHAP 贡献：`[{"feature","group","contribution","direction"}]`，可空数组 |
| gnn_reference | object\|null | – | GNN 对照分 `{"score","std"}`，不可用时为 null |
| model_version | string | ✓ | 如 `tree_v4_ens+tree_v4_noTE_ens@2026-07`，供复盘点 |
| source | string | ✓ | `query`（单组查询）/ `batch`（批量排序） |
| timestamp | string | ✓ | 打分时间 |

示例：`predictions/example.json`

## Schema 2：experiment_record（App → RAG）

用户真实实验记录。**独立 record_id**（防 CV 泄漏教训），是与用户分布完全一致的训练样本，也是 RAG 迭代的核心输入。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| schema_version / record_type | string | ✓ | `"1.0"` / `"experiment_record"` |
| record_id | string | ✓ | `rec_YYYYMMDD_NNN`，主键，独立编号体系 |
| favorite_id | string\|null | ✓ | 关联收藏条目；游离记录为 null |
| aldehyde / amine | object | ✓ | 单体对象 |
| prediction_id | string\|null | – | 当初依据的预测快照 id |
| prediction_snapshot | object\|null | – | `{"score","std","ood"}` 冗余快照，防预测文件丢失 |
| conditions | object | ✓ | 见下 |
| outcome | string | ✓ | `film`（成膜）/ `partial`（部分）/ `failed`（失败） |
| failure_class | string\|null | – | **minimax 失败分类 A–G**（A 无产物 … G 膜质量高），与 minimax `failure_criteria.md` 对齐；未知为 null |
| strength | string | – | 机械强度/膜质量描述 |
| notes | string | – | 备注 |
| attachments | array | – | 附件相对路径（照片/docx/PXRD 等） |
| operator | string | – | 实验人 |
| date | string | ✓ | 实验日期 YYYY-MM-DD |
| minimax_plan_no | string\|null | – | 若对应 minimax 方案编号（`COF-...-vN`），填上以便双侧互查 |

`conditions` 字段（全部为字符串/数字，未知留空字符串）：

| 字段 | 说明 | 示例 |
|---|---|---|
| solvent | 溶剂 | `甲苯` / `BTF/二氧六环` |
| modulator | 调制剂及用量 | `苯胺 13.7 μL` |
| catalyst | 催化剂及用量 | `6M 乙酸 0.2 mL` |
| temperature_c | 温度 °C | `120` |
| time_days | 反应天数 | `3` |
| vessel | 容器 | `35 mL Pyrex 管` |
| addition_order | 加料顺序 | `先醛+苯胺，后胺，最后乙酸` |

示例：`records/example.json`

## Schema 3：suggestion（RAG → App）

RAG 产出的迭代建议，App 页⑤展示并与收藏条目关联。**由 minimax 侧写入**。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| schema_version / record_type | string | ✓ | `"1.0"` / `"suggestion"` |
| suggestion_id | string | ✓ | `sug_YYYYMMDD_NNN`，主键 |
| favorite_id | string\|null | ✓ | 针对的收藏条目；通用建议为 null |
| type | string | ✓ | `condition_adjust`（条件调整）/ `new_candidate`（新候选单体对） |
| payload | object | ✓ | type=condition_adjust：`{"adjustments":[{"field","from","to","rationale"}]}`；type=new_candidate：`{"aldehyde":{...},"amine":{...},"rationale"}` |
| evidence_refs | array | ✓ | 依据：`[{"kind":"experiment_record"\|"literature"\|"prediction","ref":"rec_...或DOI或pred_id","note":""}]` |
| created_at | string | ✓ | 生成时间 |
| status | string | ✓ | `new` / `adopted`（已采纳）/ `rejected`（已否决）/ `done`（已实验验证），由 App 侧回写状态 |

示例：`suggestions/example.json`

---

## 与 minimax 既有格式的映射要点

| 本契约 | minimax 对应物 | 说明 |
|---|---|---|
| experiment_record.outcome + failure_class | feedback_db.csv `失败Class`（A–G） | 契约同时保留三档 outcome（App UI 用）与 A–G（minimax 判据手册） |
| experiment_record.conditions | feedback_db.csv 无直接列 | 由 minimax 适配器并入 `下一轮建议`/`备注` 或内部结构化存储 |
| prediction.score/score_std | feedback_db.csv `tianxuan_预测概率`/`tianxuan_MC标准差` | **语义不同**（树模型倾向性 ≠ tianxuan GNN 概率），适配器不得写入该两列，放备注 |
| record_id / prediction_id | minimax `方案编号`（CAS 中心） | 契约用独立 id + 可选 `minimax_plan_no` 回链，不强行统一 |

## 演进规则

- 新增可选字段：不改 schema_version
- 改字段含义/删字段/新增 record_type：schema_version 升 `1.1`/`2.0`，并在本文件头部记变更日志
