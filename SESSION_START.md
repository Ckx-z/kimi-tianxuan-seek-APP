# 会话启动清单 — 每次继续项目前必做

> 目的：防止上下文丢失，让任何大模型都能秒懂项目当前状态。
> 最后更新：2026-07-20

---

## 1. 必读文件（按顺序，不可跳过）

1. **`.agents/AGENTS.md`** — 项目级 AI 协作规范（自动加载）
2. **`PROJECT_STATE.md`** — 当前阶段、下一步、阻塞点
3. **`.agents/session_index.yaml`** — 历史会话索引（快速了解项目演进）
4. **最新 `DAILY_LOG/YYYY-MM-DD.md`** — 上一次做了什么、遗留问题
5. **`DECISIONS.md`** — 关键决策和原因
6. **`DATA_DICT.md`** — 数据字段定义和已知问题
7. **`.agents/session_state.yaml`** — 当前会话实时状态（如果有）

> 读完以上文件后，**必须运行** `python scripts/check_project_state.py` 检查状态一致性。

---

## 2. 自动化状态检查（推荐）

运行以下命令，自动检查项目状态：

```bash
cd "C:/Users/ckx/Desktop/全新机器学习实验"
.venv/Scripts/python.exe scripts/check_project_state.py
```

脚本会检查：
- PROJECT_STATE.md 是否滞后于最新日报
- models/ 是否有新模型未记录
- reports/ 是否有新报告未记录
- EXPERIMENTS/ 是否有未记录实验
- .agents/session_state.yaml 是否存在且有效
- 测试是否全部通过

---

## 3. 必问用户（只问真正影响下一步的问题）

- 今天要解决什么具体问题？
- 优先级最高的任务是什么？
- 是否有新数据/新想法要加入？

---

## 4. 会话结束必做

1. 更新 **`PROJECT_STATE.md`**（阶段、下一步、阻塞点）
2. 写/更新 **`DAILY_LOG/YYYY-MM-DD.md`**（AI 看的技术日报）
3. 写/更新 **`DAILY_LOG/YYYY-MM-DD_human.md`**（人看的工作摘要）
4. 更新 **`.agents/session_state.yaml`**（本次会话修改、待办状态）
5. 更新 **`.agents/session_index.yaml`**（如果有新会话记录）
6. 如有新决策，记入 **`DECISIONS.md`**
7. 如做了实验，写 **`EXPERIMENTS/exp_XXX.md`**

---

## 5. 禁止事项

- 不要直接修改旧项目 `C:\Users\ckx\Desktop\tianxuan seek` 任何文件
- 不要在没更新 PROJECT_STATE 的情况下结束会话
- 不要假设自己记得之前的状态——去读文档
- 不要跳过数据审计直接跑模型
- 不要将 `.agents/session_state.yaml` 提交到 git（已加入 .gitignore）

---

## 6. 快速状态检查（手动版）

如果无法运行脚本，手动检查：

- `data/raw/` — 是否新增数据？
- `models/` — 是否训练了新模型？
- `reports/` — 是否生成了新报告？
- `EXPERIMENTS/` — 是否有未记录的实验？
- `DAILY_LOG/` — 最新日报日期是否与今天一致？

---

## 7. 当前项目速览（截至 2026-07-20）

- **阶段**：阶段 12 已完成并收官 —— 双模型路由上线（D22）+ 切换 routed_strict（D23：仅双未见走 noTE，一新一熟走 v4）；指纹/校准路线证伪关闭；Hadamard 交互确认保留；双留出逐折报告标配化
- **可运行**：双击 `start_app.vbs`（推荐，静默启动）或 `.venv/Scripts/python.exe app/gradio_app.py`
- **模型（路由模式，routed_strict/D23）**：醛/胺均未见（双未见）→ `tree_v4_noTE`（142 特征，双留出 3 种子均值 0.6824 最强）；其余（双已见/一新一熟）→ `tree_v4`（144 特征含 TE，LOGO 0.8784）；路由键 `models/monomer_pool.json`（醛 896/胺 1366）；`tree_v3` 为单模型模式默认与回退；GNN v5.3 并行输出不变
- **评估协议**：LOGO 与醛胺双留出同时报告；双留出必须报多分组种子（单种子噪声 ±0.03）+ 逐折明细与 fold 级均值±std（fold 级 std≈0.21，最难折必报）；路由验证见 `reports/routing_eval.json`
- **前端**：「打分理由」SHAP 归因跟随路由模型（热态 ~0.04s）；预测标注实际模型 + 路由原因
- **最新报告**：`EXPERIMENTS/exp_006.md`（指纹证伪）、`exp_007.md`（交互消融 + fold3 诊断）、`exp_008.md`（路由评估 + D23 复盘：routed_strict 切换）
- **记忆系统**：.agents/ 目录已创建，AGENTS.md + session_index + session_state 已建立
- **日报双轨制**：AI 日报 + 人日报，每天工作结束后生成
- **已知阻塞**：无；真正瓶颈是双未见单体泛化（0.63-0.68，指纹已证伪 → GNN 表征迁移方向，明日第一优先：可行性评估）

---

## 8. 日报双轨制说明

每天工作结束后，必须生成两份日报：

| 文件 | 读者 | 内容 |
|---|---|---|
| `DAILY_LOG/YYYY-MM-DD.md` | AI | 技术细节、代码变更、指标变化、问题与方案 |
| `DAILY_LOG/YYYY-MM-DD_human.md` | 人 | 工作摘要、成果列表、关键发现、下一步建议 |

两份日报可以交叉引用，但面向不同读者。AI 日报给下一个 AI 实例看，人日报给用户看。

---

## 9. 关键文件路径速查

| 文件 | 路径 | 说明 |
|---|---|---|
| 项目状态 | `PROJECT_STATE.md` | 当前阶段、下一步、阻塞点 |
| 会话索引 | `.agents/session_index.yaml` | 历史会话摘要 |
| 会话状态 | `.agents/session_state.yaml` | 当前会话实时状态 |
| 状态检查 | `scripts/check_project_state.py` | 一致性检查工具 |
| AI 协作规范 | `.agents/AGENTS.md` | 自动加载的 AI 指南 |
| App 入口 | `app/gradio_app.py` | Gradio 前端 |
| 树模型训练 | `src/models/train.py` | v2 训练脚本（精简规则 + 交互 + 留一单体 CV） |
| 树模型入口 | `src/predictor/tree_model.py` | XGBoost 推理接口 |
| 训练数据 | `data/interim/v5_train_stage1_cond_filled.csv` | 6,201 行，条件已补全 |
| 消融脚本 | `scripts/ablation_v2.py` | A/B/C/D 四组消融 |
| 归因脚本 | `scripts/generate_attribution_report.py` | SHAP 归因报告生成 |

---

*本文件每次会话结束时检查是否需要更新。*
