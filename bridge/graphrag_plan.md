# GraphRAG 设计文档

> 设计日期: 2026-07-13
> 数据来源: `C:\Users\ckx\Desktop\tianxuan seek\data\` (只读)
> 实施脚本: `bridge/build_graphrag.py` + `bridge/query_graphrag.py`

---

## 1. 数据现状

| 资源 | 数量 | 路径 |
|------|------|------|
| 结构化 yaml | **954 篇** | `data/structured/*.yaml` |
| 单体池 | **1059 单体** | `data/processed/merged_monomer_pool.csv` |
| 醛-胺配对 | **216209 配对** | `data/processed/v4_cartesian_pairs.csv` |
| 反应记录 | **6201 反应** | `data/processed/v5_train_stage1.csv` |
| 增强反应 | **6392 反应** | `data/processed/v5_train_stage1_aug_v2.csv` |

每个 yaml 含 **22 个结构化字段**（journal, system, reagent, catalyst, solvent, synthesis_route, synthesis_mode, interface_type, schiff_base_kinetics, reaction_temperature, film_crystallinity_fluorine, innovation...），**100% 填充率**。

---

## 2. Schema 设计

### 2.1 节点类型

```yaml
Monomer:        # 单体
  id: M-{smiles 哈希}
  smiles: ...
  best_name: ...
  type: aldehyde | amine
  has_fluorine: bool
  n_f_atoms: int
  has_cf3: bool
  n_aldehyde: int        # 醛基数 (aldehydes only)
  n_amine: int           # 胺基数 (amines only)
  n_papers: int          # 被多少文献用过 (关键)
  source: llm | train

Reaction:       # 反应
  id: R-{paper_id}-{group_id}
  paper_id: str
  group_id: str
  aldehyde_smiles: str
  amine_smiles: str
  aldehyde_name: str
  amine_name: str
  stoichiometry: str          # "1:1 (Tp:Pa)"
  solvent: str                # "water/CH₂Cl₂"
  temperature: str            # "120°C"
  source_db: str              # "v3_db_full"
  outcome: str                # 从 yaml 关联 (powder/film/crystal/no_product)
  synthesis_mode: str         # "均相/异相/液-液界面"
  interface_type: str

Solvent:        # 溶剂 (从 yaml 聚合)
  id: S-{normalized_name}
  name: str
  synonyms: [str]             # ["mesitylene", "均三甲苯", "TMB"]

Catalyst:       # 催化剂 (从 yaml 聚合)
  id: C-{normalized_name}-{concentration}
  name: str
  concentration: str          # "6 M", "3 M", "18 M"

Interface:      # 界面类型 (从 yaml 聚合)
  id: I-{type}
  type: str                   # "固-液界面", "液-液界面", "气-液界面"

Literature:     # 文献
  id: L-{yaml_filename}
  literature_id: str
  journal: str
  system: str
  innovation: str
  doi: str                    # 推断

Outcome:        # 产物 (从 yaml film_crystallinity_fluorine + synthesis_mode 抽取)
  id: O-{type}
  type: powder | film | crystal | no_product
  description: str
```

### 2.2 边类型

| 边 | 起点 | 终点 | 来源 |
|----|------|------|------|
| `uses_aldehyde` | Reaction | Monomer (醛) | v5_train_stage1.csv |
| `uses_amine` | Reaction | Monomer (胺) | v5_train_stage1.csv |
| `uses_solvent` | Reaction | Solvent | yaml.solvent 聚合 |
| `uses_catalyst` | Reaction | Catalyst | yaml.catalyst |
| `at_interface` | Reaction | Interface | yaml.interface_type |
| `cited_in` | Reaction | Literature | yaml.literature_id |
| `produces` | Reaction | Outcome | yaml.synthesis_mode + film_crystallinity_fluorine |
| `co_occurs` | Monomer | Monomer | 跨 Reaction 共同出现, 权重=共同次数 |

---

## 3. 索引输出

```
minimax/bridge/graphrag/
├── nodes_monomer.jsonl       (~1MB)
├── nodes_reaction.jsonl      (~1MB)
├── nodes_solvent.jsonl       (~10KB)
├── nodes_catalyst.jsonl      (~10KB)
├── nodes_interface.jsonl     (~5KB)
├── nodes_literature.jsonl    (~1MB)
├── nodes_outcome.jsonl       (~5KB)
├── edges_*.jsonl             (8 种边)
├── graph.pkl                 (NetworkX 内存图, ~10MB)
├── embeddings.jsonl          (节点 embedding, 用于 rerank)
└── meta.json                 (统计信息)
```

---

## 4. 查询接口

```bash
python bridge/query_graphrag.py "TAPT + 含氟二胺 形成膜 120°C"
```

### 4.1 查询流程

```
1. 关键词提取 (rule-based)
   - 单体名 → Monomer 节点
   - 反应条件 (溶剂/温度/界面) → Solvent/Catalyst/Interface 节点
   - 产物 (膜/粉末) → Outcome 节点

2. 节点定位 (精确匹配 + fuzzy)
   - 名字完全匹配: "TAPT" → M-TAPT
   - 简称/别名: "含氟二胺" → has_fluorine=true AND type=amine

3. 图遍历 (BFS, 深度 2)
   - 起点: Monomer/Outcome/Condition 节点
   - 一跳: Reaction 节点
   - 二跳: 关联 Literature + 邻居 Monomer

4. Embedding rerank
   - MiniMax embo-01 计算 query embedding
   - 候选 Literature 节点 text embedding
   - cosine 相似度重排

5. 返回结构化结果
   - top-k 文献
   - 每个文献: 引用条件 + outcome + 关键创新
```

### 4.2 查询示例

```python
# Q1: "TAPT + 含氟二胺 形成膜"
query_graphrag("TAPT + 含氟二胺 形成膜")
# → 返回: 含 TAPT 的 Reaction → 关联含氟胺类 → 产生膜的 Outcome
# → 关联文献: TFA-COF (yaml) 等

# Q2: "Boc 保护 缓慢释放 膜"
query_graphrag("Boc 保护 缓慢释放 膜")
# → 关键词: Boc 保护 (schiff_base_kinetics 字段)
# → 返回: a-synthetic-route-for-crystals-of-woven-structures yaml

# Q3: "异相合成 粉末"
query_graphrag("异相合成 粉末")
# → 关键词: synthesis_mode=异相 + Outcome=powder
# → 返回: 多个异相粉末反应
```

---

## 5. 实施步骤

| Phase | 内容 | 时间 | 验证 |
|-------|------|------|------|
| 1 | 数据加载 + yaml 解析 | 30 min | 输出 node/edge JSONL |
| 2 | NetworkX 建图 + meta 统计 | 30 min | graph.pkl 生成 |
| 3 | 简单查询 (3 种 query) | 30 min | demo 输出 |
| 4 | Embedding rerank 集成 | 30 min | query "TAPT 膜" 命中 |
| 5 | 接入 generate_proposal.py | 1 hour | docx 自动用 graphrag |

---

## 6. 与现有 RAG 的关系

| 维度 | 现有 embedding RAG | GraphRAG |
|------|-------------------|----------|
| 召回 | "找相似文本" | "找关联知识" |
| 适合 | 单文档问答 | 跨文档推理 |
| 速度 | 快 (<100ms) | 中 (<1s) |
| 成本 | 低 (一次 embedding) | 中 (建图 + 每次 query 重排) |

**混合策略**:
- 普通查询: 用 embedding + cosine (快)
- 跨文档推理: 用 graphrag (准)
- 决策: query 包含 ≥ 2 个实体 → 用 graphrag, 否则 embedding

---

## 7. 风险与限制

1. **yaml 抽取错误**: LLM 抽取可能含错（CAS 对应错等）→ 用 v5_train_stage1.csv 校验
2. **Solvent 聚合歧义**: "mesitylene/1,4-dioxane" 是混合还是单独? → 拆分为多个节点
3. **Outcome 抽取错误**: "粉末" vs "多晶粉末" vs "纳米颗粒" 是否区分? → 简化为 4 类
4. **数据时效**: yaml 是 2025 年前抽取的 → 不含最新文献
5. **规模**: 954 篇 vs 用户 1700 篇 → 缺 ~750 篇, 需要补 index

---

## 8. 下一步

1. **你审核此设计** (5-10 分钟阅读)
2. **我写 build_graphrag.py** (Phase 1-2)
3. **我跑 demo** (3 个 query 验证)
4. **如通过**: 写 query_graphrag.py (Phase 3-4)
5. **集成到 generate_proposal.py**

---

*Generated by minimax agent on 2026-07-13*