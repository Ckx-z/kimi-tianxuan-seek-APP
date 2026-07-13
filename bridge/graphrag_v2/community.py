"""
graphrag_v2/community.py
========================
分层社区发现: Louvain/Greedy Modularity

v2 升级 - 不删减 v1
"""
import json
import pickle
from pathlib import Path
from collections import defaultdict

GRAPH_DIR = Path(__file__).resolve().parent.parent / 'graphrag'
CACHE_DIR = Path(__file__).resolve().parent / 'cached'


def load_graph():
    v2_fp = GRAPH_DIR / 'graph_v2.pkl'
    v1_fp = GRAPH_DIR / 'graph.pkl'
    fp = v2_fp if v2_fp.exists() else v1_fp
    with open(fp, 'rb') as f:
        return pickle.load(f)


def detect_communities(G=None, method='greedy', levels=2):
    """分层社区发现

    Level 1: 全图粗分 (大社区)
    Level 2: 在每个 L1 社区内细分 (子社区)

    返回: dict of {level: {community_id: [node_ids]}}
    """
    if G is None:
        G = load_graph()

    print(f'Running {method} community detection on {G.number_of_nodes()} nodes...')

    communities_by_level = {}

    # Level 1
    L1 = _greedy_modularity(G)
    communities_by_level[1] = L1
    print(f'  Level 1: {len(L1)} communities')

    # Level 2: 在每个 L1 社区内再分
    if levels >= 2:
        L2 = {}
        idx = 0
        for comm_id, members in L1.items():
            if len(members) < 10:  # 太小不分
                L2[f'L2-{idx}'] = members
                idx += 1
                continue
            # 提取子图
            subG = G.subgraph(members).copy()
            sub_comms = _greedy_modularity(subG)
            for sub_id, sub_members in sub_comms.items():
                L2[f'L2-{idx}'] = sub_members
                idx += 1
        communities_by_level[2] = L2
        print(f'  Level 2: {len(L2)} sub-communities')

    return communities_by_level


def _greedy_modularity(G):
    """NetworkX 贪心模块度社区发现 (不依赖 numpy)"""
    import networkx as nx
    communities = nx.community.greedy_modularity_communities(G)
    return {f'C-{i}': list(c) for i, c in enumerate(communities)}


def add_community_nodes(G, communities_by_level, save=True):
    """给图加 community summary 节点 + belongs_to 边"""
    if G is None:
        G = load_graph()

    print('Adding community nodes...')
    community_text = defaultdict(list)

    for level, comms in communities_by_level.items():
        for cid, members in comms.items():
            # 聚合 community 内节点的文本
            text_parts = []
            for nid in members[:20]:  # 限制
                data = G.nodes[nid]
                if data.get('node_type') == 'reaction':
                    ald = data.get("aldehyde_name") or ''
                    ami = data.get("amine_name") or ''
                    out = data.get("outcome") or ''
                    text_parts.append(f'{ald[:30]} + {ami[:30]} ({out})')
                elif data.get('node_type') == 'literature':
                    j = data.get("journal") or ''
                    s = data.get("system") or ''
                    text_parts.append(f'[{j[:30]}] {s[:60]}')
                elif data.get('node_type') == 'monomer':
                    n = data.get("best_name") or ''
                    text_parts.append(f'{n[:40]}')
            community_text[cid] = ' | '.join(text_parts[:10])

            # 加 summary node
            summary_id = f'L{level}-{cid}'
            G.add_node(summary_id,
                       node_type='community',
                       level=level,
                       size=len(members),
                       summary=community_text[cid],
                       top_text=community_text[cid][:200])

            # 边: members → community
            for member in members:
                G.add_edge(member, summary_id, edge_type='belongs_to')

    print(f'  Added {len(community_text)} community nodes')

    if save:
        with open(GRAPH_DIR / 'graph_v2.pkl', 'wb') as f:
            pickle.dump(G, f, pickle.HIGHEST_PROTOCOL)
        with open(CACHE_DIR / 'communities.json', 'w', encoding='utf-8') as f:
            json.dump({
                'communities': {f'L{lev}': {cid: len(members) for cid, members in comms.items()}
                                for lev, comms in communities_by_level.items()},
                'community_text': dict(community_text),
            }, f, ensure_ascii=False, indent=2)
        print(f'  Saved: graph_v2.pkl + communities.json')

    return G


def get_community_summary(G, level=1, k=10):
    """返回 top-k community summary (按 size)"""
    items = []
    for nid, data in G.nodes(data=True):
        if data.get('node_type') == 'community' and data.get('level') == level:
            items.append({
                'id': nid,
                'size': data.get('size', 0),
                'top_text': data.get('top_text', ''),
                'summary': data.get('summary', ''),
            })
    items.sort(key=lambda x: -x['size'])
    return items[:k]


if __name__ == '__main__':
    print('=== Hierarchical Community Detection ===\n')
    G = load_graph()
    communities = detect_communities(G, levels=2)
    G = add_community_nodes(G, communities)

    print('\n=== Top Level 1 Communities ===')
    top_l1 = get_community_summary(G, level=1, k=5)
    for c in top_l1:
        print(f'\n  [{c["id"]}] size={c["size"]}')
        print(f'    {c["top_text"][:200]}')

    print('\n=== Top Level 2 Communities ===')
    top_l2 = get_community_summary(G, level=2, k=5)
    for c in top_l2:
        print(f'\n  [{c["id"]}] size={c["size"]}')
        print(f'    {c["top_text"][:200]}')