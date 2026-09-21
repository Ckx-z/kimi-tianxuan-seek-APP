# F3.2 / F3.3 实施所需的 API 面（已探明，供直接动手）

> 探明方式：AST 静态分析（只读，未改任何代码）。配合 `docs/待办-G1G2-F3-实施方案.md` 使用。

## `src/recommend/plan_card.py`（203 行）

**顶层函数**
- `generate_plan_card(...)` ← **方案卡生成入口，F3.2 要扩展的就是它**
- 内部辅助：`_mol` / `_aromatic_ring_count` / `_monomer_hints` / `_monomer_obj`

**顶层常量**（模板素材所在地）
- `TEMPLATE_NAME` · `DEFAULTS_NOTE`（"默认建议"文案）
- `_CONDITIONS` · `_STEPS` · `_CHECKLIST` ← **模板化的条件/步骤/检查表；当前"没有用量"就是这里没有量**
- `BUILTIN_PATH` / `PROJECT_ROOT`

## `src/recommend/generated_plans.py`（311 行）

**顶层函数**
- `adopt_suggestion(...)` ← 从"建议"采纳并**生成方案卡**（`api/routers/iterate.py` 的 `POST /adopt` 调它）
- `_extract_adjustments(...)` ← **真实用量所在**（`adjustments_applied`），F3.2 要把它的结果**回填进 `conditions`**
- `_resolve_monomers` / `_monomers_from_favorite` / `_monomers_from_records` / `_resolve_template`
- `_load_existing_plans` / `_next_seq` / `_next_plan_id`（编号与序号）
- `_read_json` / `_monomer_ok`
- 类 `AdoptError`（路由层用它转 HTTP 错误）

**顶层常量**
- `PLANS_DIR`（= `user_data_root()/generated_plans`，`plan_*.json`）
- `SUGGESTIONS_DIR` / `RECORDS_DIR` / `FAVORITES_DIR` / `_UD` / `_PLAN_ID_RE`

## 由此得到的实施要点

**F3.2（接 LLM + 真实用量）**
1. 新建 `src/recommend/plan_card_llm.py`：
   - 先调 `plan_card.generate_plan_card(...)` 拿**模板基线**（`_CONDITIONS`/`_STEPS` 的骨架）；
   - 再用 LLM（复用 `src/literature/reading_llm.py::_chat`）在**给定"与用户讨论确定的用量"**下产出
     每步用量 + 依据，**JSON Schema 校验 + 只重试违规字段**；
   - 返回的卡片里 **`conditions` 必须含具体用量**（醛/胺比、溶剂、催化剂当量、温度/时间）。
2. 在 `generated_plans.adopt_suggestion(...)` 的落库路径里：
   - 把 `_extract_adjustments(...)` 的结果**写入 `conditions`**（当前只写进 `adjustments_applied` 就丢了）；
   - 卡片多一个字段标注用量来源（`dosage_source: discussed|default|literature`）。
3. 路由：`POST /api/plan-card`（`api/routers/plan.py`）与 `POST /api/iterate/adopt` 都走上面路径，保持兼容。

**F3.3（助手方案卡工具）**
- 新建 `src/assistant/tools/plan_manage.py`，内部直接复用 `generated_plans` 的
  `adopt_suggestion` / `_load_existing_plans` / `PLANS_DIR`（删除走
  `DELETE /api/iterate/plans/{plan_id}` 同一套文件匹配逻辑，或直接在此实现文件删除）；
- 在 `src/assistant/registry.py` 注册：顶部 `from .tools.plan_manage import ...`，
  `TOOLS` 字典加一项（`"handler": lambda args: ...` + `"schema": {...}`，照 `lit_graph` 的写法）；
- `src/assistant/persona.py` 写明：**生成方案卡前必须先与用户确认用量**。

**测试建议**
- `tests/test_plan_card_llm_v198.py`：注入假 `chat`，断言生成的 `conditions` **含用量**、
  用量来源标注正确、LLM 失败时**降级为模板**而不是报错、Schema 违例只重试违规字段；
- `tests/test_assistant_plan_tools_v198.py`：创建/修改/删除各一条 + 删除不存在的方案报错 +
  工具已注册（`registry.list_tool_schemas()` 含名字）。
