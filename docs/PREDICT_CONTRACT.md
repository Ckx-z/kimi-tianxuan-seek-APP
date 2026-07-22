# predict() 返回契约（权威文档）

> **版本**：1.0（2026-07-22 首版）
> **适用对象**：React 前端迁移的开发者——**不看 Python 源码即可正确消费预测结果**。
> **代码出处**：`src/predictor/__init__.py`（`FilmPredictor.predict`）、`src/predictor/routing.py`、
> `src/predictor/ood.py`、`src/predictor/tree_model.py`、`src/predictor/gnn_model.py`；
> 消费方：`app/gradio_app.py`（App）、`api/deps.py`（API）。
> 本文所有键名均与上述源码逐一核对。若源码变更，本文必须同步更新。

---

## 0. 一句话总览

`FilmPredictor.predict(ald_smiles, amine_smiles) -> dict`
输入醛/胺两个 SMILES，返回一个 **dict**：始终含 `ald_smiles`/`amine_smiles`/`ood`，
成功时含树模型/GNN 分数与不确定度；失败时**不抛异常**，而是出现 `*_error` 降级键。
**分数语义是"成膜倾向性打分"（四级软标签上的回归分），非严格概率**，取值裁剪到 [0, 1]。

---

## 1. 全键表

### 1.1 始终存在的键

| 键 | 类型 | 含义 | 何时为 None / 缺失 | 消费方用法 |
|---|---|---|---|---|
| `ald_smiles` | str | 醛单体 SMILES（回显入参） | 永不为 None | App/API 回显；API 原样进 payload |
| `amine_smiles` | str | 胺单体 SMILES（回显入参） | 永不为 None | 同上 |
| `ood` | dict | OOD 三级检测结果（见 §2） | 永不为 None（检测本身异常时降级为 `{"level":"none","reasons":[],"checks":{},"ood_error":str}`） | App 渲染警示横幅；API 原样进 payload |

### 1.2 正常出分键（模型成功时出现）

| 键 | 类型 | 含义 | 何时缺失 | 消费方用法 |
|---|---|---|---|---|
| `tree_probability` | float ∈ [0,1] | 树模型（实际路由臂）成膜打分（集成成员均值，已裁剪） | 树模型不可用或预测异常（此时出现 `tree_error`） | 主分数候选（D29 口径见 §3）；App 分行展示 |
| `tree_std` | float ≥ 0 | 树模型 bagging 成员标准差（认知不确定度）。单模型 pkl 恒为 0.0 | 同 `tree_probability` | App 展示 ±std；API `tree_std` |
| `tree_model_name` | str | 实际出分的树模型文件名 stem | 同 `tree_probability` | App 标注模型名；API 原样透出；日志 `arm` 字段 |
| `tree_route` | str | 路由键，仅路由模式存在：`in_pool` / `ald_unseen` / `amine_unseen` / `both_unseen` | **单模型模式（含路由资产缺失回退 tree_v3）下不出现此键** | App 日志 `route`；API 原样透出 |
| `tree_route_reason` | str | 路由原因中文文案（供前端直接展示） | 同上 | App 展示"模型路由：…" |
| `gnn_probability` | float ∈ [0,1] | GNN v5.3 成膜打分 | GNN 不可用或预测异常（此时出现 `gnn_error`） | 主分数候选（D29 口径）；App 分行展示 |
| `gnn_std` | float ≥ 0 | GNN MC-dropout 不确定性（±std）；输出无该字段时为 0.0 | 同 `gnn_probability` | App 展示 ±std；API `gnn_std` |
| `score_std` | float ≥ 0 | **认知不确定度主口径 = `tree_std`**（树 bagging 成员 std；代码中直接 `score_std = tree_std`） | 树模型未出分时缺失 | App 主分数旁的 ±std；App 日志 `std` 字段 |
| `ensemble_probability` | float 或 None | 综合打分 = 已出分模型的**算术平均**（仅树→树分；仅 GNN→GNN 分；双出→均值） | 两模型全部失败时为 **None**（且出现 `error` 键） | App 仅作"对照参考"展示，**不作主分数** |

### 1.3 降级/错误键（异常时出现）

| 键 | 类型 | 何时出现 | 消费方用法 |
|---|---|---|---|
| `gnn_error` | str | GNN 预测抛异常（子进程失败/找不到 checkpoint/输出解析失败等）。出现后 `gnn_available` 被置 False，**本进程后续预测不再尝试 GNN** | App 展示"GNN 不可用（原因）" |
| `tree_error` | str | 树模型预测抛异常。出现后 `tree_available` 被置 False，后续不再尝试 | App 展示"树模型不可用（原因）" |
| `ood_error` | str | 仅当 OOD 检测本身异常时出现，嵌在 `ood` dict 内（此时 `ood.level` 强制为 `"none"`） | 前端一般无需处理 |
| `error` | str | 两模型全部失败时：`"没有可用的预测模型。"`（此时 `ensemble_probability` 为 None） | 前端应提示"未出分" |

**判定要点**：判断某模型是否出分，用 `"tree_probability" in result` / `"gnn_probability" in result`
（App 源码正是这么做的），不要依赖 `*_error` 键的缺失。

---

## 2. OOD 三级制（`result["ood"]`）

### 2.1 结构

```json
{
  "level": "none" | "warning" | "out",
  "reasons": ["中文原因1", "中文原因2"],
  "checks": {
    "functional_group": {"level": "...", "reasons": [...], "details": {...}},
    "novelty":        {"level": "...", "reasons": [...], "details": {...}},
    "feature_drift":  {"level": "...", "reasons": [...], "details": {...}}
  }
}
```

- `level` 取三个检测器的**最高等级**（none < warning < out），`reasons` 为三者合并（中文，可直接展示）。
- `checks.*.details` 为检测器明细，类型不定：
  - `functional_group.details`：`aldehyde` → `{"n_aldehyde_groups": int}` 或 `"unparsable"`；`amine` → `{"n_primary_amine","n_secondary_amine","hydrazide","hydrazine","hydroxylamine"}` 或 `"unparsable"`。
  - `novelty.details`：`{"ald_seen": bool, "amine_seen": bool}`；无单体池时为字符串 `"no_pool"`。
  - `feature_drift.details`：`{"n_checked","n_out","out_ratio","threshold","out_features":[{feature,value,p05,p95}...]}`；无包络文件时为 `"no_envelope"`。

### 2.2 三个检测器

| 检测器 | 触发等级 | 条件 |
|---|---|---|
| a. 官能团适配 `functional_group` | **out** | 胺侧为酰肼/肼类；或胺侧为羟胺类；或胺侧无伯胺/仲胺；或醛侧无醛基 C(=O)H；或 SMILES 无法解析 |
| b. 单体新颖性 `novelty` | **warning** | 醛/胺**双未见**于训练池（与路由 `both_unseen` 联动，走 noTE 外推臂） |
| c. 特征漂移 `feature_drift` | **warning** | 关键特征超出训练 5%–95% 包络的比例 > 10%（`models/feature_envelope.json`） |

### 2.3 「out 不出分」铁律 ⛔

**`ood.level == "out"` 时，前端一律不显示任何分数**（GNN 与树模型同挂），显示
「模型不适用」+ `reasons`。⛔ 优先于打分。App 与 API 均遵守：
App 不渲染分数与 SHAP 理由；API 将 `score`/`tree_score`/`gnn_score`/两个 std 全部置 null（见 §3）。
`warning` 则正常出分，但须展示黄色警示条与原因（"外推模式，打分可信度降低"）。

前端建议映射（与 App 一致）：`none` → "✓ 池内"；`warning` → "⚠️ 外推"；`out` → "⛔ 不适用"。

---

## 3. 消费映射表

### 3.1 主分数口径（D29：两模型较高值，乐观召回）

App 与 API **共用同一规则**（`app/gradio_app.py::_headline_score` ≡ `api/deps.py::headline_score`）：

```
score, source = headline_score(pred_result)
# tree 与 gnn 均出分 → score = max(tree, gnn), source = "both"
# 仅树出分          → score = tree,           source = "tree"
# 仅 GNN 出分       → score = gnn,            source = "gnn"
# 均未出分          → score = None,           source = None
```

- 展示须标注来源：`both`="两模型较高值"、`tree`="仅树模型出分"、`gnn`="仅 GNN 出分"。
- 属**乐观召回口径**：高分需结合 OOD 与 `score_std` 判断。
- `ensemble_probability`（平均）**仅对照参考，永不做主分数**。

### 3.2 predict() 键 → API payload 键（`api/deps.py::build_prediction_payload`）

| API payload 键 | 来源 predict() 键 | OOD=out 时 |
|---|---|---|
| `ald_smiles` / `amine_smiles` | 同名 | 正常透出 |
| `score` | `headline_score()`（max 口径） | **null** |
| `score_source` | `"both"/"tree"/"gnn"/None` | 正常透出（不置 null） |
| `score_policy` | 常量 `"max_tree_gnn"` | 正常透出 |
| `tree_score` | `tree_probability` | **null** |
| `tree_std` | `tree_std` | **null** |
| `tree_model_name` | `tree_model_name` | 正常透出 |
| `tree_route` | `tree_route`（单模型模式下 predict 无此键 → payload 为 null） | 正常透出 |
| `gnn_score` | `gnn_probability` | **null** |
| `gnn_std` | `gnn_std` | **null** |
| `ood` | `ood`（整个 dict 原样） | 正常透出 |
| `source` | 调用方标注（默认 `"api"`） | — |
| `timestamp` | API 侧生成（ISO 秒） | — |

> 规则：**OOD=out 时所有分数类字段（score/tree_score/gnn_score/tree_std/gnn_std）全置 null**，
> 防止消费方绕过 `score` 直读分量被误导；路由/模型名等元信息保留。

### 3.3 predict() 键 → App（Gradio）消费

| App 展示 | 来源键 |
|---|---|
| 大分数 + ±std | `headline_score()` + `score_std` |
| 来源标签 | `score_source`（both/tree/gnn 中文映射） |
| GNN 行 | `gnn_probability` (±`gnn_std`)，或 `gnn_error` |
| 树模型行 | `tree_probability` + `tree_model_name` (±`score_std`)，或 `tree_error`；附 `tree_route_reason` |
| 综合打分（仅对照） | `ensemble_probability` (±`score_std`) |
| OOD 横幅 | `ood.level` / `ood.reasons` |
| 预测日志 | `score`/`tree_score`=`tree_probability`/`gnn_score`=`gnn_probability`/`std`=`score_std`/`arm`=`tree_model_name`/`route`=`tree_route`/`ood_level`=`ood.level` |

---

## 4. 路由逻辑（树模型双臂）

**一句话：醛/胺双未见于训练池 → `tree_v4_noTE` 外推臂（noTE）；其余（双已见/一新一熟）→ `tree_v4` 池内臂（含 TE 先验）。**（routed_strict，D23）

路由键（`tree_route` 取值）与中文原因（`tree_route_reason`）：

| `tree_route` | 条件 | 实际模型 | `tree_model_name`（当前资产） |
|---|---|---|---|
| `in_pool` | 醛胺均已见 | tree_v4 集成 | `tree_v4_ens` |
| `ald_unseen` | 仅醛未见 | tree_v4 集成（沿用池内模型） | `tree_v4_ens` |
| `amine_unseen` | 仅胺未见 | tree_v4 集成（沿用池内模型） | `tree_v4_ens` |
| `both_unseen` | 双未见 | tree_v4_noTE 集成（外推） | `tree_v4_noTE_ens` |

**`tree_model_name` 全部可能取值**（= 加载的 pkl 文件名 stem，前端不应硬编码判断，仅作展示）：

- `tree_v4_ens`（路由·池内臂，5 种子 bagging 集成）
- `tree_v4_noTE_ens`（路由·外推臂，5 种子 bagging 集成）
- `tree_v3`（单模型回退，见 §5）
- 其他：显式传入 `tree_model_path` 时为该文件 stem

---

## 5. 错误语义与坑

1. **`gnn_available` 是惰性标志（坑）**：初始化时只要构造了 GNN 封装就置 True，
   **不代表 GNN 真能跑**——首次预测失败才翻转为 False 并写入 `gnn_error`。
   因此前端**不能**用"第一次有没有 `gnn_error`"推断 GNN 环境健康；只要本次返回有
   `gnn_probability` 才算出分。
2. **树模型加载回退**：路由模式要求 `tree_v4_ens.pkl` + `tree_v4_noTE_ens.pkl` +
   `monomer_pool.json` 三件资产齐全；任一缺失时**静默回退单模型 `tree_v3.pkl`**
   （`DEFAULT_TREE_MODEL`）。此时：仍正常出分，但 **`tree_route`/`tree_route_reason` 键不出现**，
   `tree_model_name == "tree_v3"`，`tree_std`/`score_std` 恒为 0.0（单模型无集成 std）。
   前端处理路由展示时务必容忍这两个键缺失。
3. **失败后熔断**：`tree_error`/`gnn_error` 一旦出现，对应 `*_available` 置 False，
   进程内后续请求不再尝试该模型（直接没有对应键，也不再刷错误信息）。
4. **GNN 依赖外部环境**：GNN 通过 subprocess 调旧项目（`C:\Users\ckx\Desktop\tianxuan seek`
   + `E:\ANACONDA\envs\dphuanjing\python.exe`），单次最长 120s。React 前端应对 predict
   接口设置充足超时，并把 `gnn_error` 视为常态降级而非异常。
5. **OOD 检测不依赖模型**：官能团检查始终执行；即使两模型全挂，`ood` 仍有效，
   前端仍应展示 OOD 状态。

---

## 6. 版本与变更日志

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-07-22 | 1.0 | 首版：覆盖 `FilmPredictor.predict` 全键表、OOD 三级制与「out 不出分」铁律、App/API 消费映射（D29 max 口径）、routed_strict 路由与 tree_model_name 取值、gnn_available 惰性标志与 tree_v3 回退等错误语义。React 前端迁移 Phase 0 交付物。 |
