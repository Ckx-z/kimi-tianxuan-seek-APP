"""
graphrag_v2/reasoning.py
========================
多跳推理 (v2 升级)

v1: 1-2 跳 BFS
v2: 3 跳 + 路径收集 + 简化 LLM 总结 (无 LLM 时给模板)

实现: BFS 收集所有路径, 不依赖 LLM (可后续加 LLM 总结)
"""
from pathlib import Path
import sys
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def multi_hop_paths(G, start_nodes, max_hops=3, max_paths=10):
    """从 start_nodes BFS, 收集所有路径

    返回: list of paths [[n1, n2, n3], ...]
    """
    paths = []

    def dfs(node, path, depth):
        if depth >= max_hops:
            return
        if len(paths) >= max_paths:
            return
        for _, next_node, edata in G.out_edges(node, data=True):
            if next_node in path:
                continue
            new_path = path + [next_node]
            paths.append(new_path)
            dfs(next_node, new_path, depth + 1)

    for start in start_nodes:
        if isinstance(start, dict):
            start_id = start.get('id')
        else:
            start_id = start
        if start_id not in G:
            continue
        dfs(start_id, [start_id], 0)

    return paths[:max_paths]


def format_paths(G, paths, max_paths=5):
    """把路径格式化成可读文本"""
    lines = []
    for i, path in enumerate(paths[:max_paths], 1):
        nodes_str = []
        for nid in path:
            data = G.nodes[nid]
            nt = data.get('node_type', '?')
            if nt == 'reaction':
                desc = f'R({data.get("aldehyde_name", "?")[:20]} + {data.get("amine_name", "?")[:20]})'
            elif nt == 'literature':
                desc = f'L[{data.get("journal", "?")[:20]}]'
            elif nt == 'monomer':
                desc = f'M[{data.get("best_name", "?")[:20]}]'
            else:
                desc = f'{nt}'
            nodes_str.append(desc)
        lines.append(f'  路径 {i}: {" → ".join(nodes_str)}')
    return '\n'.join(lines)


def summarize_paths(G, paths, question=None):
    """路径总结 (无 LLM, 用模板)"""
    if not paths:
        return '(无路径)'

    summary_lines = [f'找到 {len(paths)} 条多跳路径:']
    summary_lines.append(format_paths(G, paths, max_paths=5))

    # 统计关联节点类型
    type_count = {}
    for path in paths:
        for nid in path:
            nt = G.nodes[nid].get('node_type', '?')
            type_count[nt] = type_count.get(nt, 0) + 1

    summary_lines.append(f'\n关联节点类型分布: {type_count}')

    if question:
        summary_lines.append(f'\n问题: {question}')
        summary_lines.append('结论: 基于多跳推理, 关键路径涉及上述节点关联.')

    return '\n'.join(summary_lines)


if __name__ == '__main__':
    import pickle
    GRAPH_DIR = Path(__file__).resolve().parent.parent / 'graphrag'
    v2_fp = GRAPH_DIR / 'graph_v2.pkl'
    with open(v2_fp, 'rb') as f:
        G = pickle.load(f)

    # 测试: 从 top reaction R-101-1 多跳
    paths = multi_hop_paths(G, ['R-101-1'], max_hops=3, max_paths=10)
    print(f'从 R-101-1 多跳推理 ({len(paths)} 条路径):')
    print(summarize_paths(G, paths, question='为什么 R-101-1 能形成膜?'))