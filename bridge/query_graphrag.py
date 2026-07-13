"""
bridge/query_graphrag.py
========================
GraphRAG 查询接口 (Phase 3 demo)

直接以 reaction 节点为核心查询 (因为 monomer_pool.csv 有 LLM 抽取错误)

用法:
    python bridge/query_graphrag.py "TAPT 含氟二胺 膜"
    python bridge/query_graphrag.py "Boc 保护 缓慢释放"
    python bridge/query_graphrag.py "异相合成 粉末"

逻辑:
1. 关键词提取
2. 直接扫 reaction 节点 (醛+胺名+溶剂+产物+温度+... 文本匹配)
3. 扫 literature 节点 (innovation + system 文本匹配)
4. 评分排序, top-k 输出
"""
import sys
import json
import pickle
import re
from pathlib import Path

# Phase 4: 可选的 embedding rerank
try:
    from embedding_rerank import rerank, load_lit_embeddings
    HAS_EMBED = (Path(__file__).resolve().parent / 'graphrag' / 'lit_embeddings.jsonl').exists()
except ImportError:
    HAS_EMBED = False

GRAPH_DIR = Path(__file__).resolve().parent / 'graphrag'


def load_graph():
    with open(GRAPH_DIR / 'graph.pkl', 'rb') as f:
        return pickle.load(f)


def get_reactions(G):
    """返回所有 reaction 节点"""
    for nid, data in G.nodes(data=True):
        if data.get('node_type') == 'reaction':
            yield nid, data


def get_literatures(G):
    for nid, data in G.nodes(data=True):
        if data.get('node_type') == 'literature':
            yield nid, data


def match_score(text, keywords):
    """关键词命中率打分"""
    if not text:
        return 0
    text_low = text.lower()
    score = 0
    for kw in keywords:
        if kw.lower() in text_low:
            score += 1
    return score


def query(query_text, verbose=False):
    """GraphRAG 查询

    返回: {
        'query': str,
        'keywords': list,
        'reactions': [{'id': ..., 'score': N, 'data': {...}}, ...],
        'literatures': [{'id': ..., 'score': N, 'data': {...}}, ...],
        'summary': {...}
    }
    """
    G = load_graph()

    q = query_text.lower()

    # 关键词提取
    keywords = []
    if 'tapt' in q:
        keywords.append('tapt')
    if 'tfpt' in q:
        keywords.append('tfpt')
    if 'tapb' in q:
        keywords.append('tapb')
    if 'tfpb' in q:
        keywords.append('tfpb')
    if 'tfmb' in q:
        keywords.append('tfmb')
    if '含氟' in q or 'fluorine' in q:
        keywords.append('f')
    if '二胺' in q or 'diamine' in q:
        keywords.append('diamine')
    if '膜' in q:
        keywords.append('film')
    if '粉末' in q or '沉淀' in q:
        keywords.append('powder')
    if '晶' in q or 'crystal' in q:
        keywords.append('crystal')
    if 'mesitylene' in q or '均三甲苯' in q or 'tmb' in q:
        keywords.append('mesitylene')
    if 'dioxane' in q or '二氧六环' in q:
        keywords.append('dioxane')
    if 'boc' in q or '脱保护' in q or '保护' in q:
        keywords.append('boc')
    if '异相' in q:
        keywords.append('heterogeneous')
    if '均相' in q:
        keywords.append('homogeneous')
    if '120' in q or '120°c' in q:
        keywords.append('120')
    if '160' in q:
        keywords.append('160')

    if not keywords:
        keywords = [query_text.lower()]

    # 扫 reaction 节点
    reaction_hits = []
    for rid, r in get_reactions(G):
        text = ' '.join([
            str(r.get('aldehyde_name', '')),
            str(r.get('amine_name', '')),
            str(r.get('solvent', '')),
            str(r.get('temperature', '')),
            str(r.get('outcome', '')),
            str(r.get('synthesis_mode', '')),
            str(r.get('interface_type', '')),
            str(r.get('catalyst', '')),
        ]).lower()
        score = match_score(text, keywords)
        if score > 0:
            reaction_hits.append({'id': rid, 'score': score, 'data': dict(r)})

    reaction_hits.sort(key=lambda x: -x['score'])

    # 扫 literature 节点
    lit_hits = []
    for lid, l in get_literatures(G):
        text = ' '.join([
            str(l.get('journal', '')),
            str(l.get('system', '')),
            str(l.get('innovation', '')),
        ]).lower()
        score = match_score(text, keywords)
        if score > 0:
            lit_hits.append({'id': lid, 'score': score, 'data': dict(l)})

    lit_hits.sort(key=lambda x: -x['score'])

    # summary
    outcomes_count = {}
    solvents_count = {}
    for h in reaction_hits:
        outcome = h['data'].get('outcome', '?')
        outcomes_count[outcome] = outcomes_count.get(outcome, 0) + 1
        sol = h['data'].get('solvent', '')
        if sol:
            solvents_count[sol[:40]] = solvents_count.get(sol[:40], 0) + 1

    # Phase 4: embedding rerank literature (混合: keyword + embedding cosine)
    if HAS_EMBED and lit_hits:
        try:
            reranked = rerank(query_text, lit_hits[:30], top_k=30)
            lit_hits = reranked
        except Exception as e:
            if verbose:
                print(f'  (rerank 失败: {e})')

    result = {
        'query': query_text,
        'keywords': keywords,
        'reactions': reaction_hits[:30],
        'literatures': lit_hits[:30],
        'summary': {
            'outcome_dist': outcomes_count,
            'top_solvents': dict(sorted(solvents_count.items(), key=lambda x: -x[1])[:5]),
            'n_reactions': len(reaction_hits),
            'n_literatures': len(lit_hits),
        }
    }

    if verbose:
        print(f'\n>>> 查询: "{query_text}"')
        print(f'  关键词: {keywords}')
        print(f'  命中: {len(reaction_hits)} reactions, {len(lit_hits)} literatures')
        print(f'  产物分布: {outcomes_count}')

    return result


def print_result(result):
    """打印 query 结果 (命令行模式)"""
    q = result['query']
    print(f'\n>>> 查询: "{q}"\n')

    rh = result['reactions']
    lh = result['literatures']

    print('=' * 70)
    print(f'  TOP {min(10, len(rh))} 反应 (按 score 排序)')
    print('=' * 70)
    for h in rh[:10]:
        r = h['data']
        print(f'\n  [{h["score"]}★] {h["id"]}')
        print(f'    醛:  {r.get("aldehyde_name", "?")}')
        print(f'    胺:  {r.get("amine_name", "?")}')
        print(f'    溶剂: {r.get("solvent", "?")}')
        print(f'    温度: {r.get("temperature", "?")}')
        print(f'    产物: {r.get("outcome", "?")}')

    print('\n' + '=' * 70)
    print(f'  TOP {min(10, len(lh))} 文献 (按 score 排序)')
    print('=' * 70)
    for h in lh[:10]:
        l = h['data']
        print(f'\n  [{h["score"]}★] {h["id"]}')
        print(f'    期刊: {l.get("journal", "?")}')
        print(f'    体系: {l.get("system", "?")[:120]}')
        print(f'    创新: {l.get("innovation", "?")[:200]}')

    print('\n' + '=' * 70)
    print('  SUMMARY')
    print('=' * 70)
    print(f'  产物分布: {result["summary"]["outcome_dist"]}')
    print(f'  主要溶剂: {result["summary"]["top_solvents"]}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python bridge/query_graphrag.py "<query>"')
        sys.exit(1)
    result = query(' '.join(sys.argv[1:]))
    print_result(result)