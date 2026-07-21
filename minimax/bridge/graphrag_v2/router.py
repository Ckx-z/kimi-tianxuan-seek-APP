"""
graphrag_v2/router.py
========================
Dynamic Query Router (v2 升级)

根据 query intent 路由到不同策略:
- local: embedding + 关键词 + 1 跳
- global: 社区摘要 + LLM 总结
- relational: 多跳 + LLM 推理
- temporal: 时间过滤 + 趋势
- entity: 节点直查
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))


def route(parsed_query):
    """路由到策略"""
    intent = parsed_query.get('intent', 'local')
    return {
        'local': LocalStrategy,
        'global': GlobalStrategy,
        'relational': RelationalStrategy,
        'temporal': TemporalStrategy,
        'entity': EntityStrategy,
    }.get(intent, LocalStrategy)(parsed_query)


class SearchStrategy:
    def __init__(self, parsed):
        self.parsed = parsed

    def execute(self, query_func, G=None):
        raise NotImplementedError


class LocalStrategy(SearchStrategy):
    """局部查询: 关键词 + embedding + 1 跳邻居"""
    def execute(self, query_func, G=None):
        # 直接调 query_graphrag
        return query_func(self.parsed['original'], verbose=False)


class GlobalStrategy(SearchStrategy):
    """全局查询: 社区摘要 + 遍历"""
    def execute(self, query_func, G=None):
        # 找 top communities (按 size)
        if G is None:
            return query_func(self.parsed['original'], verbose=False)

        from community import get_community_summary
        communities = get_community_summary(G, level=1, k=10)
        # 用 community 摘要拼成 result
        return {
            'intent': 'global',
            'query': self.parsed['original'],
            'communities': communities,
            'note': '基于分层社区摘要 (level 1, top 10 by size)',
        }


class RelationalStrategy(SearchStrategy):
    """关系查询: 多跳 + 路径"""
    def execute(self, query_func, G=None):
        # 先调 query_graphrag, 再做多跳推理
        result = query_func(self.parsed['original'], verbose=False)
        # 多跳: 找 result.reactions 中每个的邻居
        if G is not None and result.get('reactions'):
            from reasoning import multi_hop_paths
            hops = multi_hop_paths(G, result['reactions'][:5], max_hops=self.parsed['depth'])
            result['multi_hop_paths'] = hops
        return result


class TemporalStrategy(SearchStrategy):
    """时间查询: 当前数据无时间字段, 退化到 global"""
    def execute(self, query_func, G=None):
        result = query_func(self.parsed['original'], verbose=False)
        result['note'] = '时间过滤待 schema 扩展 (data/structured 无 year 字段)'
        return result


class EntityStrategy(SearchStrategy):
    """实体查询: 节点直查"""
    def execute(self, query_func, G=None):
        return query_func(self.parsed['original'], verbose=False)


if __name__ == '__main__':
    from nl2graph import nl_to_query
    test_intents = [
        ("TAPT 120°C 膜", "local"),
        ("总结所有含氟 COF", "global"),
        ("为什么 A1 失败", "relational"),
        ("近 3 年进展", "temporal"),
        ("TFPT CAS", "entity"),
    ]
    for q, expected in test_intents:
        parsed = nl_to_query(q)
        actual = parsed['intent']
        match = '✓' if actual == expected else '✗'
        print(f'  {match} "{q}" → {actual} (expected {expected})')