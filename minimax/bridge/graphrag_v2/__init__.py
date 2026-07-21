"""
graphrag_v2/__init__.py
========================
GraphRAG v2 主类 - 集成 6 大能力
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nl2graph import nl_to_query, query_to_str
from router import route
from multimodal import multimodal_rerank
from community import detect_communities, add_community_nodes, get_community_summary
from importance import compute_node_importance, top_k_important
from reasoning import multi_hop_paths, summarize_paths


class GraphRAGv2:
    def __init__(self):
        import pickle
        self.G = None
        self.parsed_cache = {}

    def load_graph(self):
        import pickle
        v2_fp = Path(__file__).resolve().parent.parent / 'graphrag' / 'graph_v2.pkl'
        v1_fp = Path(__file__).resolve().parent.parent / 'graphrag' / 'graph.pkl'
        fp = v2_fp if v2_fp.exists() else v1_fp
        with open(fp, 'rb') as f:
            self.G = pickle.load(f)
        return self.G

    def query(self, nl_text, verbose=False):
        """主入口: NL 查询 → GraphRAG v2 全流程"""
        if self.G is None:
            self.load_graph()

        # 1. NL → 结构化查询
        parsed = nl_to_query(nl_text)
        if verbose:
            print(f'\n>>> NL: "{nl_text}"')
            print(query_to_str(parsed))

        # 2. 路由
        from query_graphrag import query as v1_query
        strategy = route(parsed)
        result = strategy.execute(v1_query, G=self.G)

        # 3. 多模态重排 (literature 候选)
        if isinstance(result, dict) and 'literatures' in result:
            result['literatures'] = multimodal_rerank(
                result['literatures'][:30], nl_text, G=self.G)
            result['literatures'] = result['literatures'][:10]

        result['parsed_query'] = parsed
        result['graphrag_version'] = 'v2'
        return result

    def build_indexes(self):
        """构建 importance + community 索引"""
        print('Building v2 indexes...\n')
        # 1. importance
        print('[1/2] Node Importance...')
        compute_node_importance()
        # 2. communities
        print('[2/2] Community Detection...')
        communities = detect_communities(levels=2)
        self.G = self.load_graph()
        add_community_nodes(self.G, communities)
        print('\n✓ v2 indexes built')


def print_result(result, top_n=5):
    """打印 query 结果"""
    q = result.get('parsed_query', {}).get('original', '?')
    print(f'\n>>> GraphRAG v2 查询: "{q}"')
    print(f'   意图: {result.get("parsed_query", {}).get("intent", "?")}')

    if 'reactions' in result:
        print(f'\n=== TOP {top_n} 反应 ===')
        for h in result['reactions'][:top_n]:
            r = h.get('data') or {}
            mm = h.get('multimodal_score', h.get('score', 0))
            print(f'  [{mm:.3f}] {h["id"]}')
            print(f'    {r.get("aldehyde_name", "?")[:40]} + {r.get("amine_name", "?")[:40]}')
            print(f'    溶剂: {r.get("solvent", "?")[:50]} | 温度: {r.get("temperature", "?")}')

    if 'literatures' in result:
        print(f'\n=== TOP {top_n} 文献 (多模态重排) ===')
        for h in result['literatures'][:top_n]:
            l = h.get('data') or {}
            mm = h.get('multimodal_score', h.get('score', 0))
            bd = h.get('score_breakdown', {})
            print(f'  [{mm:.3f}] {h["id"]}')
            if l:
                print(f'    [{l.get("journal", "?")[:30]}] {l.get("system", "?")[:80]}')
            if bd:
                print(f'    breakdown: kw={bd["keyword"]:.2f} emb={bd["embedding"]:.2f} '
                      f'imp={bd["importance"]:.2f} comm={bd["community"]:.2f}')

    if 'communities' in result:
        print(f'\n=== TOP {top_n} 社区 (global 模式) ===')
        for c in result['communities'][:top_n]:
            print(f'  [{c["id"]}] size={c["size"]}')
            print(f'    {c["top_text"][:150]}')

    if 'multi_hop_paths' in result:
        print(f'\n=== 多跳路径 (relational 模式) ===')
        print(summarize_paths(_get_G(), result['multi_hop_paths']))


def _get_G():
    import pickle
    from pathlib import Path
    GRAPH_DIR = Path(__file__).resolve().parent.parent / 'graphrag'
    v2_fp = GRAPH_DIR / 'graph_v2.pkl'
    with open(v2_fp, 'rb') as f:
        return pickle.load(f)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'build':
        g = GraphRAGv2()
        g.build_indexes()
    elif len(sys.argv) > 1 and sys.argv[1] == 'test':
        g = GraphRAGv2()
        result = g.query('TAPT 含氟 膜 120', verbose=True)
        print_result(result)
    else:
        print('用法:')
        print('  python -m graphrag_v2 build    # 构建 importance + community')
        print('  python -m graphrag_v2 test     # 测试查询')