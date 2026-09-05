# 成膜打分 GNN 修正机制 + 科研助手功能完善 + 文献录入图谱 —— 方案设计文档

> 状态：**方案稿（未开发）**，待用户确认后实施。
> 依据：标杆文献 *Discovery of highly fluorescent covalent organic frameworks through AI-assisted iterative experiment–learning cycles*（Nature Chemistry 2025, 17, 1645–1654, DOI: 10.1038/s41557-025-01974-x）及其 SI（41557_2025_1974_MOESM1_ESM.pdf）。
> 原则：**只修正 GNN**；tree 模型（XGBoost bagging）保持不动，作为固定基准对照。

---

## 需求一：GNN 成膜打分修正机制

### 1.1 问题分析

**症状**：TFPT（三嗪三醛，已修复 tree 训练数据后 tree=0.866）与 B5（TFMB，2,2'-双(三氟甲基)-4,4'-联苯二胺，CAS 341-58-2，用户实验记录中与 TFPT/TFPB 均观察到管壁成膜）等已验证成膜的体系，GNN 分量仍给出低分（TFPT+对苯二胺 GNN≈0.29–0.50，MC 方差大；TFPT+BD-CF3 系 GNN 偏低）。由于融合口径 headline_score = max(tree, gnn)（redline 内），GNN 低估导致：① tree 也低分的组合（如文献新体系）被整体压低；② GNN 分量的解释性差（校准后仍与文献事实矛盾）；③ 用户对 GNN 分量失去信任。

**根因**（与阶段二/三诊断一致）：
1. GNN 训练语料（旧项目 v3_db_full 衍生）对三嗪三醛/TFMB 类单体覆盖不足，且历史脏标签把部分 TFPT 行打成负样本；
2. 训练集是「一次性」快照——新文献、用户自己的实验结论（experimental_refs 里已有多条 TFPT+B5 成膜记录）没有回流通道；
3. 缺少版本管理与回退：改模型只能靠发版，不能小步迭代。

**结论**：需要一个与标杆文献同构的「**模型推荐 → 实验/文献验证 → 主动学习微调 → 部署评估**」闭环，让 GNN 随新证据演进，而 tree 始终作为不动基准。

### 1.2 技术方案（数据反馈 → 模型微调 → 部署验证）

#### 1.2.1 反馈数据模型（单一事实来源：feedback store）

落盘 `user_data_root()/feedback/gnn_feedback.jsonl`（追加式，与 sessions.jsonl 同风格；frozen 下落 `%APPDATA%/COF-Film-Recommend/data/feedback/`）。

```jsonc
// 每条反馈一行
{
  "feedback_id": "fb_<uuid12>",
  "source": "score_correction" | "literature_pdf" | "experiment_csv",  // 反馈来源
  "ald_smiles": "O=Cc1ccc(-c2nc(...)n2)cc1",       // 醛单体（canonical SMILES）
  "amine_smiles": "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F",
  "label": 1.0 | 0.5 | 0.0,                         // 成膜三档（与金标准同口径）
  "note": "用户备注（文献标题/实验编号/理由）",
  "refs": ["10.1038/s41557-025-01974-x", "record_xxx", "pdf_<hash8>"],  // 溯源
  "can_network": true,                              // 校验字段（label=1 而 can_network=false → 待确认）
  "dedupe": { "in_tree_v6": false, "in_gnn_base": false, "existing_label": null },
  "status": "pending" | "confirmed" | "rejected" | "merged" | "conflict",
  "created_at": "...", "updated_at": "..."
}
```

**三条反馈通道**：

| 来源 | 处理方式 | 产物 |
|---|---|---|
| 打分纠错（界面按钮） | 用户填正确档位 + 理由，直接 pending | 1 条 feedback |
| 新文献 PDF | `pdf_extract`（PyMuPDF，已随包）取全文 → **LLM 结构化提取**（复用 src/llm/client + 文献挖掘 prompt，对应标杆 SI 的 GPT-4+PyPDFLoader 做法）+ **RDKit 正则兜底**（SMILES-like token → `Chem.MolFromSmiles` 校验、按「醛侧/胺侧官能度」归类）→ 半自动预览表，**用户逐行确认**后入 pending | 多条候选 → 确认后的 feedback |
| 实验反馈 CSV/Excel | 固定列 `aldehyde_smiles, amine_smiles, label[, note]`，服务端逐行校验 | 多条 feedback |

**校验与去重（确认时执行，结果写回 dedupe 字段）**：
- `can_network`：复用 `src/predictor/ood.py::check_networkability`——label>0 但不可成网 → 标记待确认，界面黄条提示（防止把噪声正样本带进训练）；
- 去重：对 v6 树训练集（`data/interim/v6_train_stage1.csv`）与 GNN 基础训练集（`gnn_training/data/`）做 canonical SMILES 组合去重；
- 标签冲突：同一组合历史标签与反馈不一致 → status=conflict，要求用户在界面二次确认「覆盖」。

#### 1.2.2 微调（fine-tuning）策略

训练代码**从旧项目迁入本仓库** `gnn_training/`（`finetune.py` 入口 + `dataset.py`），但**模型定义唯一来源是 `gnn_runtime/src/screening/gnn_v4/`**（推理与训练共用同一份模型代码，杜绝版本漂移；训练脚本 `cwd=gnn_runtime`，`sys.path` 插入脚本目录即可 import，与 `predict_pair.py` 同法）。

- **执行环境**：`runtime_config.gnn_python()`（dphuanjing，torch 2.3.1 + PyG 2.6.1 + CUDA），以后端 subprocess 方式跑训练（与推理同构）。终端用户机器没有 dphuanjing 时重训入口置灰并给引导文案（与 GNN 降级口径一致）。
- **初始权重**：`models/gnn_v5.4/v5_model.pt`（baseline）。
- **迁移学习**：冻结 global virtual node 输入投影 + 前 k 层 message passing（k 默认 = 层数-1，可配），仅微调顶层 + 打分头（classification head）。理由：底层学的是通用分子图/官能度表征，新证据只应修正「成膜判定」这层。
- **损失函数**（在 v5.4 的 group-weighted Focal 基础上叠加）：
  ```python
  weight = base_weight                 # v5.4 既有：不可成网组 ×2.0
  if row.is_feedback:
      weight *= (FEEDBACK_POS_W if y == 1.0 else FEEDBACK_NEG_W)
      # 默认 FEEDBACK_POS_W=5.0（纠偏文献正样本），FEEDBACK_NEG_W=3.0
  ```
- **早停**：验证集 = 反馈行分层抽 15% + 基础集随机抽 5%；`patience=5`，监控 val PR-AUC；epoch 上限 30，lr=1e-4 + cosine decay。
- **校准**：验证集重拟合 Isotonic（复用 v5.4 calibrator 代码），与 checkpoint 同目录落 `calibrator.pkl`。
- **产出物**：`models/gnn_feedback/gnn_v5.5_<YYYYmmdd_HHMM>/v5_model.pt` + `calibrator.pkl` + `retrain_meta.json`（base_version、feedback_ids、超参、val 指标、gold 快照、tree 基准分）。

#### 1.2.3 验证闸门（guardrail，不达标自动回滚）

训练完成后自动执行（subprocess 调 dphuanjing 推理脚本）：
1. **反馈对改善**：本次 merged 反馈中 label=1 的组合，校准后 GNN 分 ≥ 0.5；label=0 的组合 ≤ 0.25；
2. **金标准无回退**：`scripts/eval_film_scoring.py --offline` 在 39 对金标准上重跑，a_min/c_max/MAE/Spearman 不得劣于激活版本快照（容忍 ±0.01）；
3. 任一不满足 → 版本标记 `rejected` 且**不激活**，界面给出逐对对比表。

#### 1.2.4 版本管理与部署

- **Registry**：`models/gnn_feedback/registry.json`
  ```jsonc
  {
    "active": "gnn_v5.4",                       // 当前激活版本（回退 = 改此指针）
    "versions": [
      {"version": "gnn_v5.5_20260910_1030", "base": "gnn_v5.4", "status": "active|rejected|retired",
       "feedback_ids": [...], "val_pr_auc": 0.74, "gold_snapshot": {...}, "created_at": "..."}
    ]
  }
  ```
- **推理接入**：`src/predictor/gnn_model.py::_resolve_runtime()` 增加 registry 读取——存在激活的 retrained 版本则 checkpoint 指向该版本（缺失/损坏自动回退 bundled v5.4，绝不阻塞打分）。predict 响应的 `gnn_model_version` 字段透出版本号（现有 `"model": "gnn_v5.4"` 升级为实际版本）。
- **A/B 与回退**：`POST /versions/{v}/activate` 即切换（推理是逐次 subprocess，下一请求即生效）；历史版本保留，可随时切回。
- **溯源**：retrain_meta.json 记录每版本用到的 feedback_ids + DOI/记录号 → 「这个版本吃了哪些数据」可查。

### 1.3 前端交互设计

1. **打分页「反馈打分不合理」**（Home.tsx 结果卡）：弹窗三档修正（成膜/边界/不成膜）+ 理由框 → `POST /api/gnn/feedback` → toast「已入反馈队列」。
2. **设置页新增「GNN 模型演进」面板**（Settings.tsx 新 tab 或独立卡片，参考 DFT 精度档布局）：
   - **反馈队列**：表格列出 pending 反馈（结构预览用现有 MoleculeViz / Ketcher 组件），逐行「确认 / 拒绝 / 改标签」；黄条提示 can_network 冲突与去重冲突；
   - **导入**：PDF 上传（显示 LLM 提取的候选对预览表，逐行勾选确认，对应标杆「半自动」）与 CSV/Excel 上传；
   - **重训**：按钮 → 进度条五阶段（`data_parse → feature_build → fine_tune → calibrate → guard_eval → deploy`，SSE/轮询 job 状态）+ 取消按钮；
   - **版本列表**：激活版本高亮，每版本展示 val 指标 + gold 快照 + 溯源（feedback/DOI）；「激活」「对比」按钮——对比页显示新旧模型在反馈对与金标准的逐对打分表；
   - dphuanjing 缺失时整面板置灰 + 安装引导（与现有 env-status 口径一致）。
3. 重训期间**不阻塞正常打分**（新请求继续用激活版本）。

### 1.4 后端接口设计（新路由 `api/routers/gnn_feedback.py`，prefix `/api/gnn`）

| 方法/路径 | 说明 |
|---|---|
| `POST /feedback` | 提交打分纠错 {ald_smiles, amine_smiles, label, note, source} → feedback_id |
| `GET /feedback?status=` | 反馈列表（分页） |
| `PATCH /feedback/{id}` | 改标签/理由（仅 pending/conflict 态） |
| `DELETE /feedback/{id}` | 删除 |
| `POST /feedback/{id}/confirm` | 校验（can_network/去重/冲突）→ confirmed/conflict |
| `POST /feedback/import-pdf` | multipart PDF → LLM+RDKit 提取候选 → 返回预览行（不落库） |
| `POST /feedback/import-table` | multipart CSV/XLSX → 校验后批量入 pending |
| `POST /feedback/confirm-batch` | 预览确认后批量 confirm |
| `POST /retrain` | 启动微调 job {feedback_ids, freeze_layers?, lr?, epochs?} → {job_id} |
| `GET /retrain/{job_id}` | 进度（阶段/epoch/val 指标/日志尾） |
| `POST /retrain/{job_id}/cancel` | 终止训练进程树 |
| `GET /versions` | registry 列表 |
| `POST /versions/{version}/activate` | 切换/回退激活版本 |
| `GET /versions/{version}/compare` | 新旧模型在反馈对+金标准的逐对对比 |
| `GET /env` | dphuanjing/torch/PyG 可用性（前端置灰依据） |

**Job 实现**：不引 Celery——训练作为 subprocess（dphuanjing python）启动，状态写 `user_data_root/feedback/jobs/<job_id>.json`（phase/epoch/metrics/log_tail），`GET /retrain/{job_id}` 读该文件即可；取消 = kill 进程树（taskkill /T）。

### 1.5 与标杆文献的对应关系

| 标杆文献方法（Nat. Chem. 2025, DOI 10.1038/s41557-025-01974-x） | 本系统实现 |
|---|---|
| LLM 读文献提取单体（GPT-4 + PyPDFLoader → aldehyde/amine 列表，SI Note 1） | `import-pdf`：PyMuPDF 全文 + LLM 结构化提取 + RDKit 校验，半自动确认 |
| 模型推荐（MLP 预训练 + Siamese 网络排序） | 现有 GNN v5.4（+ tree 基线）打分与推荐 |
| 实验验证（Gen I 随机 5 个 → 逐代实验 → 结果回流） | 用户实验反馈（CSV/实验记录）+ 文献正样本入 feedback store |
| 主动学习（每个 cycle 用实验结果 fine-tune，再推荐下一批） | 冻结底层微调顶层 + 加权损失 + 早停；可反复执行 |
| 嵌入化学知识（14 个 TD-DFT 电子结构描述符） | 对应我们的领域知识：成网必要条件 can_network 校验 + hard rules 向量 + 全局虚拟节点（v5.4 已有） |
| 迭代进化（4 代 11 个 COF 找到 PLQY 41.3%） | 版本化 checkpoint + registry + A/B 回退，可持续迭代 |

**首批 pilot 数据**：`data/experimental_refs/` 中已有的 TFPT+TFMB(B5)/TFPB+B5 成膜实验记录（SMILES、条件、现象齐全）→ 直接构造第一批 `experiment_csv` 反馈，验证整个闭环。

### 1.6 涉及文件/模块

| 类别 | 文件 |
|---|---|
| 新增-训练 | `gnn_training/finetune.py`、`gnn_training/dataset.py`、`gnn_training/guard_eval.py`（自旧项目 train_v5_4.py 移植改造） |
| 新增-存储/服务 | `src/predictor/gnn_feedback.py`（store/校验/合并）、`src/predictor/gnn_jobs.py`（训练 job 管理） |
| 修改 | `src/predictor/gnn_model.py`（registry 激活版本解析 + gnn_model_version 透出）、`api/routers/gnn_feedback.py`、`api/schemas.py`、`api/main.py`（挂路由）、`scripts/cof-backend.spec`（datas：gnn_training/*.py + feedback 目录排除） |
| 前端 | `webapp/src/pages/Home.tsx`（反馈按钮）、`webapp/src/pages/Settings.tsx`（演进面板）、`webapp/src/api/`（gnn client） |
| 评估复用 | `scripts/eval_film_scoring.py`（增加 `--gnn-only` / snapshot 输出，供 guardrail） |

**第三方库**：PyMuPDF（已随包）、openpyxl（如支持 XLSX 上传，需确认主环境已有）、其余全部复用现有依赖；训练依赖（torch/PyG）只存在于 dphuanjing 环境，不随包。

---

## 需求二：科研助手功能完善

### 2.1 问题分析（含现状核查结论）

- **后端已具备会话持久化**：`src/assistant/sessions.py`（jsonl，`sess_<uuid12>.jsonl`）已有 create/list/load/append/update_meta，路由已有 `POST/GET /sessions`、`GET /sessions/{id}`、`PATCH /sessions/{id}/title`。
- **「新话题不保存」根因在前端**：`Assistant.tsx::handleNewSession` 只清本地 state（`setActiveId(null)`），**不调用 createSession**；会话在首条消息发送时才惰性创建（sendMessage 内 `if (!sid)` 分支）。→ 建话题后未发消息就刷新 = 丢失。
- **无删除**：后端无 `delete_session`，前端无删除按钮。
- **报告粒度**：`research.py::save_report` 的 report 是「每次提问一份」，`report_<id>.json` 不带 session 关联。

**存储选型决策（与任务书差异，需确认）**：任务书建议 IndexedDB（Dexie）。但本应用已存在**服务端会话 API + jsonl 持久化**，且会话要承载深度研究（需要服务端生成报告、导出 docx）。前端再引 Dexie 会造成双份状态与同步逻辑。**本方案坚持「服务端为单一事实来源」，前端只修接线**，不动存储层。

### 2.2 话题持久化方案（数据结构 + 存储）

沿用现有 jsonl 格式（无需改动），仅补两个语义字段：

```jsonc
// meta 行（现有 + report 指针）
{"kind": "meta", "session_id": "sess_...", "title": "新会话",
 "context": {}, "created_at": "...",
 "report": {                        // 新增：一对话一报告指针
   "report_id": "rep_sess_xxx_v2",
   "version": 2,
   "updated_at": "..." }}
// 消息行（现有格式不变）
{"kind": "message", "role": "user|assistant", "content": "...",
 "tool_events": [...], "attachments": [...], "created_at": "..."}
```

- 标题生成：现有「首条消息前 16 字」逻辑保留；会话在 `title == "新会话"` 且首条消息到达时自动 `update_meta(title=...)`（后端 chat 接口内已有类似逻辑则复用）。
- `updated_at` 排序已实现（list_sessions 倒序）。

### 2.3 话题管理功能（保存/删除/重命名）

| 功能 | 现状 | 方案 |
|---|---|---|
| 自动保存 | 仅发送消息时才建会话 | `handleNewSession` 改为**立即 `createSession({title:"新会话"})`**，setActiveId + 刷新列表；`sendMessage` 去掉 `if (!sid)` 分支（sid 恒存在），首条消息触发标题更新 |
| 重命名 | 已有（双击标题行内编辑 + PATCH） | 保留不动 |
| 删除 | 无 | 后端 `sessions.delete_session(session_id)`（删 jsonl；uploads/ 附件文件保留不删，防误删共享引用）+ `DELETE /api/assistant/sessions/{session_id}`；前端列表项悬停「✕」→ 确认弹窗 → 删除；删除的是当前会话则自动切到新会话 |
| 排序 | 已有（updated_at 降序） | 保留不动 |

### 2.4 深度研究报告生成策略（一对话一报告）

**规则：一个 session 只有一份综合报告；研究类输出全部归入该报告。**

1. **报告结构**（writer LLM 单轮合成，复用 research.py 的 writer/verify 链路）：
   `研究背景 → 核心发现 → 详细分析（按主题分节）→ 参考文献（DOI 引用核验复用 loop._emit_verified）→ 附录（原始问答时间线）`。
2. **生成触发**：会话头部新增「生成深度研究报告」按钮 → `POST /api/assistant/sessions/{session_id}/report`（后台任务 + 进度 SSE）：收集该会话全部 user/assistant 消息 + 已有 tool_events 引用 → 合成综合报告。
3. **更新语义（追加不覆盖）**：若 `meta.report` 已存在 → 「更新报告」：以上一版报告为底稿，只消化**新增消息**（按 created_at 差集），writer 增量修订（保留旧章节、更新发现、引用取并集），`version += 1`，落 `rep_sess_xxx_v{n}.json` 并更新 meta 指针；历史版本文件保留。
4. **与研究模式的关系**：对话内「深度研究」单次提问仍走 plan→search→critic→writer 流程，但其产出不再单独成一份顶层报告，而是**作为该会话综合报告的一节/来源**写入（同时保留单次研究记录消息供回看）。报告列表页（现有「研究报告」弹窗）改为展示「会话报告」（按会话归组），老的独立 report 兼容展示。
5. **导出**：复用现有 `export.docx` 端点（加 session 版路径或 query 参数）。

### 2.5 涉及文件/模块

| 类别 | 文件 |
|---|---|
| 修改-后端 | `src/assistant/sessions.py`（delete_session + meta.report 指针）、`src/assistant/research.py`（save_report 关联 session_id、update_report 增量合成、session_report 合成器）、`api/routers/assistant.py`（DELETE session、POST sessions/{id}/report、reports 列表归组） |
| 修改-前端 | `webapp/src/pages/Assistant.tsx`（handleNewSession 立即建档、删除按钮/确认弹窗、报告按钮与更新态）、`webapp/src/api/`（deleteSession/report 接口） |
| 测试 | `tests/test_assistant*.py` 新增：新建即持久、删除、一对话一报告 upsert/增量更新 |

---

## 需求三：文献录入图谱功能

### 3.1 图谱数据类型定义

| 类型 `figure_type` | 来源 | 存储 |
|---|---|---|
| `structure`（分子结构图） | SMILES 输入（RDKit 生成 2D SVG/PNG）或上传图片 + 关联 SMILES | PNG/SVG + `{smiles}` 元数据 |
| `spectra`（表征图谱） | 上传/剪贴板粘贴：PXRD、FTIR、荧光光谱、NMR 等 | PNG/JPEG + 元数据 |
| `mechanism`（机理/反应路径图） | 上传 SVG/PNG | SVG/PNG |

**元数据 schema**（存 `user_data_root()/literature/figures/index.json`，文件本体存 `figures/<fig_id>.<ext>`）：

```jsonc
{
  "fig_id": "fig_<uuid12>",
  "paper_id": "L042",                    // 关联文献条目（paper_titles.json 的 id）
  "figure_type": "structure|spectra|mechanism",
  "caption": "图 2a：TFPT 的荧光发射光谱",
  "tags": ["荧光", "TFPT"],
  "meta": {                              // 按类型补充
    "smiles": "O=Cc1ccc(-c2nc(...)n2)cc1",        // structure 必填
    "technique": "PXRD|FTIR|PL|NMR",              // spectra
    "conditions": "120 °C, 48 h",                 // spectra/mechanism
    "peaks": [{"x": 6.3, "label": "(100)"}]       // spectra 可选峰位标注
  },
  "file": "fig_xxx.png", "mime": "image/png", "size": 123456,
  "score_note": null,                    // 联动回写：本系统打分与文献一致性备注
  "created_at": "..."
}
```

### 3.2 上传与标注流程

1. **上传入口**：文献详情页（现有文献列表/详情在哪就挂哪）「图谱」区：拖拽/选择 PNG/JPG/SVG，或**剪贴板直接粘贴**（前端 Clipboard API → blob 上传）；`structure` 类型可只输 SMILES，由后端 RDKit `MolToImage`/`rdMolDraw2D` 生成 2D 图（复用现有 MoleculeViz 同款渲染口径）。
2. **标注**：上传后弹轻量表单——figure_type 三选一、caption、tags；spectra 选 technique + conditions + 可选峰位备注；structure 自动回填/校验 SMILES。
3. **后端**：`POST /api/literature/{paper_id}/figures`（multipart，type/caption/tags/meta）→ 存文件 + 写 index；SMILES 生成走 `POST /api/literature/figures/from-smiles`（body: {paper_id, smiles, caption}）→ 返回生成图 + fig_id。

### 3.3 展示与检索方案

- **画廊**：文献详情页按 figure_type 分栏（结构图/光谱/机理图），缩略图网格；点击 lightbox 放大（前端现有 zoom 组件或轻量实现），支持下载原图。
- **筛选**：`GET /api/literature/figures?paper_id=&figure_type=&tag=`；图谱列表也可从「图谱库」聚合页进入（按时间/类型/标签）。
- **编辑/删除**：`PATCH /figures/{fig_id}`（caption/tags/meta）、`DELETE /figures/{fig_id}`（删文件 + index 条目）。

### 3.4 与成膜打分模块的联动

1. **一键验证**：structure 图（含 SMILES）卡片上「导入成膜打分」按钮 → 跳转 Home 打分页并预填该醛/胺 SMILES（沿用 TransferState 机制）。
2. **结果回写**：打分完成后可选「回写文献备注」→ `PATCH /api/literature/{paper_id}/figures/{fig_id}` 或文献条目备注字段，写 `score_note = "本系统打分 0.85（tree 0.87 / gnn 0.79），与文献一致"`，在画廊卡片角标展示。
3. **与需求一联动（顺带收益）**：从文献图谱确认的 SMILES 可一键加入 GNN 反馈队列（「加入重训反馈」按钮 → 复用 `POST /api/gnn/feedback`）。

### 3.5 涉及文件/模块

| 类别 | 文件 |
|---|---|
| 新增-后端 | `src/literature/figures.py`（index 读写、文件管理、SMILES 渲染）、`api/routers/literature.py`（增 figures 端点组） |
| 修改-后端 | `api/schemas.py`（FigureIn/FigureOut）、`scripts/cof-backend.spec`（无需新 datas：frozen 下用户目录可写；RDKit 已随包） |
| 修改-前端 | 文献相关页面（列表/详情/录入流程所在组件）+ `Home.tsx`（TransferState 预填）+ 图谱库聚合入口 |
| 测试 | `tests/test_literature_figures.py`（上传/生成/筛选/删除/联动回写） |

**第三方库**：无需新增——RDKit（结构渲染）、PyMuPDF（已有）均已在主环境与包内；SVG 仅作文件存储不解析。

---

## 综合实施计划

### 优先级排序（建议分两个版本）

| 优先级 | 需求 | 理由 |
|---|---|---|
| P0（v1.7.0） | 需求二：助手功能完善 | 根因已定位（前端接线 bug），改动小、用户高频感知，2 天内可发版 |
| P1（v1.7.0） | 需求三：文献图谱 | 独立模块，无外部依赖，与需求二并行开发 |
| P2（v1.8.0） | 需求一：GNN 修正闭环 | 需要训练管线迁移 + pilot 验证周期（TFPT/B5 首批反馈），单独一个版本更稳 |

> 也可三合一发 v1.7.0，但需求一含训练/验证长尾，建议独立发版。

### 预估工时（人天）

| 需求 | 后端 | 前端 | 测试 | 小计 |
|---|---|---|---|---|
| 需求二 | 1.0 | 0.5 | 0.5 | **2.0** |
| 需求三 | 1.0 | 0.7 | 0.3 | **2.0** |
| 需求一 | 3.5（迁移/微调/job/registry/guardrail） | 1.5（反馈按钮/面板/对比） | 1.0 | **6.0** |
| 合计 | | | | **10.0** |

### 依赖关系

- 需求二 → 需求一：研究模式报告归会话后，需求一的「文献反馈 → 报告引用溯源」可复用会话报告引用（非阻塞，仅增强）。
- 需求三 → 需求一：图谱 SMILES 一键入反馈队列（顺带联动，非阻塞）。
- 需求一内部顺序：feedback store/导入 → finetune 管线 → job/进度 → guardrail → registry/切换 → 前端面板 → pilot（TFPT+B5 批次）→ 发版。

### 验证标准

**需求二**：
- [ ] 点击「新建会话」后刷新页面，空会话仍在列表；
- [ ] 删除会话（含二次确认）后列表/后端文件同步消失，删除当前会话自动开新会话；
- [ ] 一个会话内多次研究/提问后点「生成深度研究报告」→ 一份报告涵盖全部问答；再次提问后「更新报告」→ 版本 +1 且旧章节保留、引用并集；
- [ ] 全套 pytest 保持全绿（新增 ≥10 用例）。

**需求三**：
- [ ] 上传/粘贴/粘贴板图片 → 画廊按类型分栏展示、筛选/放大/下载可用；
- [ ] SMILES 生成 2D 结构图成功且与 MoleculeViz 渲染一致；
- [ ] 「导入成膜打分」跳转预填成功；回写 score_note 在文献详情可见；
- [ ] 图谱删除后文件与 index 同步清理（无孤儿文件）。

**需求一**：
- [ ] 上传 TFPT/B5 系文献 PDF 与实验 CSV → 候选提取预览可人工确认，冲突/不可成网黄条提示生效；
- [ ] 用 pilot 反馈启动重训 → 五阶段进度可见、可取消；
- [ ] guardrail：反馈正样本校准后 ≥0.5，且 39 对金标准四项指标不回退；不达标自动 rejected 不激活；
- [ ] 版本切换/A/B 对比/回退可用；predict 响应透出 gnn_model_version；
- [ ] tree 模型文件与分数**零改动**（基准对照断言：重训前后 tree_score 完全一致）；
- [ ] 端机无 dphuanjing 时面板置灰、打分不受影响（降级口径回归）。

---

## 待确认事项（开工前）

1. **需求二存储**：本方案不引入 IndexedDB/Dexie，坚持服务端 jsonl 单一事实来源（理由见 2.1），是否同意？
2. **需求一训练环境**：重训仅在装有 dphuanjing 的机器可用（终端用户机置灰），模型更新是否以「应用内重训」为主，还是仍保留「发版携带新 checkpoint」双通道（本方案两者兼容）？
3. **发版拆分**：建议 需求二+三 → v1.7.0，需求一 → v1.8.0；如需三合一请说明。
4. **B5 等 pilot 反馈**：首批反馈直接用 `data/experimental_refs/` 里 TFPT/TFPB+B5 成膜记录构造，标签按「观察到连续薄膜」记 1.0，是否需要你逐条复核？
