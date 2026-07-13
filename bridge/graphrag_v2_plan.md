# GraphRAG v2 升级设计（保留 v1 + 补齐 6 大能力）

> 设计日期: 2026-07-13
> 基准: v1 (`bridge/graphrag/`, 9066 节点 / 23920 边 / 954 文献 embedding)
> 标杆: Nature Comm 2026 (s41467-026-69549-z.pdf)
> 原则: **升级而非删减** — 所有 v1 能力保留

---

## 1. v1 已有的能力（基础）

| 能力 | v1 实现 |
|------|---------|
| 节点/边存储 | NetworkX MultiDiGraph (9066/23920) |
| 实体抽取 | 复用 tianxuan-seek yaml 22 字段 |
| Embedding | MiniMax embo-01 (954 文献 × 1536 维) |
| 关键词查询 | 文本打分 + 排序 |
| Embedding 重排 | cosine 加权 |
| 数据来源 | 单一 (yaml + v5_train CSV) |

## 2. v2 要新增的 6 大能力（用户标杆）

### 2.1 LLM 多跳推理 (Multi-hop Reasoning)

**v1 限制**: BFS depth=1-2，无 LLM 总结  
**v2 升级**:
```python
def multi_hop_reasoning(start_nodes, target, max_hops=3):
    """3 跳推理: start → 邻居 → 邻居的邻居 → target
    返回: 路径列表 + LLM 总结"""
    paths = graph.bfs_paths(start_nodes, target, max_hops)
    summary = llm.summarize_paths(paths, question)
    return paths, summary
```

**应用**:
- Q: "为什么 A1-A8 自反应法都失败？"
- 推理: A1 → Reaction(synthesis_mode=自反应) → Outcome(粉末/破碎膜)
       → Literature(innovation=界面聚合) → Reaction(synthesis_mode=界面)
       → Outcome(film) ✓
- LLM 输出: "自反应法失败是因为缺界面聚合模板..."

**实现**: `graphrag_v2/reasoning.py`

### 2.2 Hierarchical Community Detection (分层社区发现)

**v1 限制**: 无社区结构  
**v2 升级**: 
```python
def hierarchical_communities(G, levels=3):
    """Louvain 分层社区发现
    Level 1: 大社区 (~10)
    Level 2: 子社区 (~50)
    Level 3: 叶子社区 (~200)
    每个社区生成 summary node
    """
    communities = louvain.hierarchical_communities(G, levels=3)
    for comm in communities:
        # 聚合 community 内节点生成摘要
        summary_text = aggregate_text(comm.members)
        # LLM 生成社区摘要
        summary_node = llm.summarize_community(summary_text)
        G.add_node(summary_id, node_type='community', level=comm.level,
                   summary=summary_node)
        # 边: members -> community
        for member in comm.members:
            G.add_edge(member, summary_id, edge_type='belongs_to')
    return communities
```

**应用**:
- 全局查询 "总结所有含氟 COF 体系" → L1 社区摘要
- 中等查询 "TAPT 体系的共同点" → L2 子社区
- 局部查询 "R-161-1 的具体条件" → L3 叶子

**实现**: `graphrag_v2/community.py` (用 networkx + python-louvain)

### 2.3 Dynamic Query Router (动态查询路由)

**v1 限制**: 关键词打分 (1 种路径)  
**v2 升级**:
```python
def route_query(nl_query, G, embeddings):
    """LLM 判断 query 类型 → 路由到最优策略"""
    intent = llm.classify_intent(nl_query, [
        'local_search',     # "R-161 的具体条件是什么?"
        'global_search',    # "总结所有含氟 COF 体系"
        'relational_search', # "TAPT 和 TFPT 哪个更适合"
        'temporal_search',   # "近 3 年的进展"
        'entity_search',     # "TFPT 的 CAS"
    ])
    
    if intent == 'local_search':
        return LocalSearchStrategy(embedding_rerank + 1-hop)
    elif intent == 'global_search':
        return GlobalSearchStrategy(community_summary + LLM_synthesize)
    elif intent == 'relational_search':
        return RelationalStrategy(multi_hop + LLM_reason)
    elif intent == 'temporal_search':
        return TemporalStrategy(filter_year + trend_analysis)
    else:
        return EntityStrategy(direct_lookup)
```

**应用**:
- "TAPT+含氟二胺" → local + relational (找相似 + 找关联)
- "近 3 年 COF 膜进展" → temporal + global
- "TFPT CAS" → entity

**实现**: `graphrag_v2/router.py`

### 2.4 自然语言 → 图查询翻译 (NL2GraphQL)

**v1 限制**: 无 NL 处理  
**v2 升级**:
```python
def nl_to_graph_query(nl_query):
    """LLM 把 NL 转成结构化图查询"""
    prompt = f"""
    把下面的查询转成结构化图查询:
    
    查询: {nl_query}
    
    输出 JSON:
    {{
      "start_entities": [{{type: "monomer|reaction|literature", filter: {{...}}}}],
      "filters": [{{field: "temperature", op: ">", value: 100}}],
      "edge_types": ["uses_aldehyde", "uses_amine"],
      "depth": 2,
      "intent": "local|global|relational"
    }}
    """
    return llm.complete_json(prompt)
```

**应用**:
- 输入: "找 120°C 以上的 TFPT 反应，含氟醛类，结果是膜"
- 输出: `{"start_entities": [{"type": "monomer", "name": "TFPT"}], 
          "filters": [{"field": "temperature", "op": "contains", "value": "120"}], 
          "edge_types": ["uses_aldehyde"], "depth": 2, "intent": "local"}`

**实现**: `graphrag_v2/nl2graph.py`

### 2.5 节点重要性 (Node Importance)

**v1 限制**: 无重要性评分  
**v2 升级**:
```python
def compute_node_importance(G, alpha=0.85):
    """PageRank + betweenness centrality
    缓存到节点属性
    """
    pr = nx.pagerank(G, alpha=alpha)
    bc = nx.betweenness_centrality(G)
    
    for nid in G.nodes():
        G.nodes[nid]['pagerank'] = pr.get(nid, 0)
        G.nodes[nid]['betweenness'] = bc.get(nid, 0)
        # 综合重要性
        G.nodes[nid]['importance'] = (
            0.5 * pr.get(nid, 0) / max(pr.values()) +
            0.5 * bc.get(nid, 0) / max(bc.values())
        )
    return G
```

**应用**:
- "最常被引用的反应/文献" → PageRank top-k
- "关键的桥接节点" → betweenness top-k
- 在 rerank 时给 importance 加权

**实现**: `graphrag_v2/importance.py`

### 2.6 多模态融合 (Multi-modal Fusion)

**v1 限制**: 2 路 (keyword + embedding)  
**v2 升级**:
```python
def multi_modal_rerank(candidates, query, G, embeddings):
    """4 路打分 + LLM 相关性"""
    keyword_scores = score_keyword(candidates, query)
    embedding_scores = score_embedding(candidates, query, embeddings)
    importance_scores = score_importance(candidates, G)
    llm_scores = llm_relevance_batch(candidates, query)
    
    # 加权融合
    final = (
        0.25 * normalize(keyword_scores) +
        0.25 * normalize(embedding_scores) +
        0.20 * normalize(importance_scores) +
        0.30 * normalize(llm_scores)
    )
    return sorted(zip(final, candidates), reverse=True)
```

**实现**: `graphrag_v2/multimodal.py`

---

## 3. 文件结构 (v2 = v1 + 新增)

```
minimax/bridge/
├── graphrag/                       # v1 索引 (保留不变)
│   ├── graph.pkl
│   ├── lit_embeddings.jsonl
│   └── *.jsonl
│
├── graphrag_v2/                    # v2 新增 (升级层)
│   ├── __init__.py
│   ├── reasoning.py                # 多跳推理
│   ├── community.py                # 社区发现
│   ├── router.py                   # 查询路由
│   ├── nl2graph.py                 # NL → 图查询
│   ├── importance.py               # 节点重要性
│   ├── multimodal.py               # 多模态重排
│   ├── graphrag_v2.py              # 主类 (集成)
│   └── cached/
│       ├── communities.json        # 缓存社区结构
│       ├── pagerank.json           # 缓存 PageRank
│       └── embeddings_cache.json
│
├── query_graphrag.py               # v1 接口 (保留)
├── query_graphrag_v2.py            # v2 接口 (新)
├── build_graphrag.py               # v1 构建 (保留)
├── build_graphrag_v2.py            # v2 构建 (新)
└── llm_config.yaml                 # LLM 配置 (扩展 v2)
```

---

## 4. 实施步骤

| Phase | 内容 | 时间 | 状态 |
|-------|------|------|------|
| 2.1 | `importance.py` (PageRank + betweenness) | 30 min | 立即 |
| 2.2 | `community.py` (Louvain + summary) | 1 hour | 立即 |
| 2.3 | `nl2graph.py` (LLM prompt + JSON) | 30 min | 立即 |
| 2.4 | `router.py` (intent classification) | 30 min | 立即 |
| 2.5 | `multimodal.py` (4 路打分) | 1 hour | 立即 |
| 2.6 | `reasoning.py` (多跳 + LLM 总结) | 1.5 hour | 立即 |
| 2.7 | `graphrag_v2.py` (主类) | 1 hour | 立即 |
| 2.8 | 测试 v2 vs v1 效果 | 1 hour | 立即 |
| 2.9 | v7 docx (用 v2 找 D/A1 文献) | 30 min | 立即 |

**总工作量**: 7-8 小时 (一次完成)

---

## 5. 与 v1 的兼容性

- v1 接口 `query_graphrag.py` 保持不变
- v2 接口 `query_graphrag_v2.py` 是新接口
- v1 索引 (graph.pkl, lit_embeddings.jsonl) **完全复用**
- v2 在 v1 索引上添加 importance/community 缓存
- 任何 v1 调用 v2 自动 fallback

---

## 6. v2 vs v1 效果预期

| 查询类型 | v1 准确度 | v2 准确度 |
|---------|----------|----------|
| "TFPT CAS" | 100% | 100% (相同) |
| "TAPT+含氟" | 70% | 90% (LLM 抽取关键词) |
| "为什么自反应失败" | 30% | 80% (多跳 + LLM 总结) |
| "近 3 年进展" | 0% | 70% (temporal) |
| "关键桥接节点" | 0% | 85% (PageRank) |

---

*Generated by minimax agent on 2026-07-13*