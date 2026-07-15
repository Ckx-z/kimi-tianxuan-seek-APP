# COF 成膜单体组合推荐系统 — AI 协作规范

> 项目路径：`C:/Users/ckx/Desktop/全新机器学习实验`
> 本文件由 kimi-code 自动加载，任何 AI 实例进入本项目时必读。

---

## 一、项目一句话

**输入候选单体组合 + 反应条件，输出"成膜可能性打分 + 排序 + SHAP 理由 + 相似成膜案例"，指导实验师优先试哪些。**

---

## 二、每次会话必读（按此顺序，不可跳过）

1. **`PROJECT_STATE.md`** — 当前阶段、下一步、阻塞点
2. **最新 `DAILY_LOG/YYYY-MM-DD.md`** — 上一次做了什么、遗留问题
3. **`DECISIONS.md`** — 关键决策和原因
4. **`DATA_DICT.md`** — 数据字段定义和已知问题
5. **`.agents/session_index.yaml`** — 历史会话索引（快速了解项目演进）
6. **`.agents/session_state.yaml`** — 当前会话实时状态（如果有）

> 读完以上文件后，运行 `python scripts/check_project_state.py` 快速检查状态一致性。

---

## 三、每次会话必做

### 会话开头
- [ ] 阅读上述 6 份文件
- [ ] 运行状态检查脚本
- [ ] 确认今天要解决的具体问题和优先级

### 会话结尾
- [ ] 更新 `PROJECT_STATE.md`（阶段、下一步、阻塞点）
- [ ] 写/更新 `DAILY_LOG/YYYY-MM-DD.md`（今天做了什么、发现、下一步）
- [ ] 更新 `.agents/session_state.yaml`（本次会话的修改、待办状态）
- [ ] 更新 `.agents/session_index.yaml`（如果有新会话记录）
- [ ] 如有新决策，记入 `DECISIONS.md`
- [ ] 如做了实验，写 `EXPERIMENTS/exp_XXX.md`
- [ ] **生成两份日报**：
  - `DAILY_LOG/YYYY-MM-DD.md` — AI 看的（技术细节、代码变更、指标）
  - `DAILY_LOG/YYYY-MM-DD_human.md` — 人看的（工作摘要、成果、下一步建议）

---

## 四、禁止事项

- **绝不修改旧项目** `C:/Users/ckx/Desktop/tianxuan seek` 任何文件
- **不在没更新 PROJECT_STATE 的情况下结束会话**
- **不假设自己记得之前的状态**——去读文档
- **不跳过数据审计直接跑模型**
- **不提交 session_state.yaml 到 git**（已加入 .gitignore）

---

## 五、快速状态检查

```bash
cd "C:/Users/ckx/Desktop/全新机器学习实验"
python scripts/check_project_state.py
```

输出示例：
```
✅ PROJECT_STATE.md 最新（2026-07-15）
✅ 最新日报：DAILY_LOG/2026-07-15.md
⚠️  models/ 有新文件未记录：tree_v2.pkl
❌  EXPERIMENTS/ 有未记录实验
```

---

## 六、关键文件路径速查

| 文件 | 路径 | 说明 |
|---|---|---|
| 项目状态 | `PROJECT_STATE.md` | 当前阶段、下一步、阻塞点 |
| 决策日志 | `DECISIONS.md` | 关键选择及原因 |
| 数据字典 | `DATA_DICT.md` | 字段定义、已知问题 |
| 日报（AI） | `DAILY_LOG/YYYY-MM-DD.md` | 技术细节、代码变更 |
| 日报（人） | `DAILY_LOG/YYYY-MM-DD_human.md` | 工作摘要、成果 |
| 会话索引 | `.agents/session_index.yaml` | 历史会话摘要 |
| 会话状态 | `.agents/session_state.yaml` | 当前会话实时状态 |
| 状态检查 | `scripts/check_project_state.py` | 一致性检查工具 |
| App 入口 | `app/gradio_app.py` | Gradio 前端 |
| 树模型 | `src/predictor/tree_model.py` | XGBoost 主模型 |
| GNN 封装 | `src/predictor/gnn_model.py` | subprocess 调用旧项目 |
| 描述符 | `src/features/descriptors.py` | 统一特征接口 |
| 条件推荐 | `src/condition_recommender/` | 规则+案例混合推荐 |
| 报告生成 | `src/report_generator/exporter.py` | Word 报告 |
| 训练数据 | `data/raw/v5_train_stage1.csv` | 6,201 行 |
| 模型权重 | `models/tree_baseline.pkl` | 当前树模型 |

---

## 七、运行环境

- **App 前端**：base Python 3.13（gradio 6.20, python-docx, xgboost）
- **GNN 预测**：dphuanjing 环境 Python 3.8（torch 2.3.1 + PyG 2.6.1）
- **启动 App**：`python app/gradio_app.py` 或双击 `start_app.bat`

---

## 八、当前核心指标

| 指标 | 值 | 目标 |
|---|---|---|
| 树模型 PR-AUC | 0.727 | > 0.784（超越 GNN） |
| GNN PR-AUC | 0.784 | 保持并行 |
| 测试通过数 | 9/9 | 保持全部通过 |
| 条件缺失率 | 86-90% | 用规则+案例弥补 |

---

## 九、日报双轨制

每天工作结束后，必须生成两份日报：

### AI 日报（`DAILY_LOG/YYYY-MM-DD.md`）
- 技术细节：改了哪些文件、函数、参数
- 代码变更：git diff 级别的描述
- 指标变化：PR-AUC、MAE、测试状态
- 遇到的问题和解决方案
- 下一步技术计划

### 人日报（`DAILY_LOG/YYYY-MM-DD_human.md`）
- 工作摘要：一句话总结今天做了什么
- 成果列表：完成的具体事项
- 关键发现：对项目有影响的结论
- 下一步建议：给用户的行动建议
- 阻塞点：需要用户决策或外部资源的事项

> 两份日报内容可以交叉引用，但面向不同读者。AI 日报给下一个 AI 实例看，人日报给用户看。

---

*最后更新：2026-07-15*
*下次更新：每次会话结束时*
