"""
bridge/query_graphrag.py
========================
GraphRAG 查询接口 (Phase 3 demo · 波次2 检索底座升级)

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

# 波次2: nl2graph 中文解析 (加载失败静默降级为旧硬编码关键词)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'graphrag_v2'))
    from nl2graph import nl_to_query
    HAS_NL2GRAPH = True
except Exception:
    HAS_NL2GRAPH = False

GRAPH_DIR = Path(__file__).resolve().parent / 'graphrag'

# 波次2: importance.json 惰性加载 (加载失败静默跳过, 不加权)
_IMPORTANCE = None
_IMPORTANCE_TRIED = False


def _load_importance():
    """加载 graphrag_v2/cached/importance.json; 任何失败都静默返回 {}"""
    global _IMPORTANCE, _IMPORTANCE_TRIED
    if _IMPORTANCE_TRIED:
        return _IMPORTANCE or {}
    _IMPORTANCE_TRIED = True
    try:
        fp = Path(__file__).resolve().parent / 'graphrag_v2' / 'cached' / 'importance.json'
        raw = json.load(open(fp, encoding='utf-8'))
        _IMPORTANCE = {nid: float(v.get('importance', 0.0)) for nid, v in raw.items()}
    except Exception:
        _IMPORTANCE = {}
    return _IMPORTANCE


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


# 波次2: 过滤条件 → reaction 节点字段文本 的映射
REACTION_FIELD_MAP = {
    'temperature': ['temperature'],
    'solvent': ['solvent'],
    'catalyst': ['catalyst'],
    'outcome': ['outcome'],
    'synthesis_mode': ['synthesis_mode'],
    'interface_type': ['interface_type'],
    # modulator 无独立字段, 在单体名/催化剂/溶剂文本里近似匹配
    'modulator': ['aldehyde_name', 'amine_name', 'catalyst', 'solvent', 'stoichiometry'],
}


def filter_bonus(filters, data, extra_text=''):
    """波次2: 结构化过滤条件软加权 (可解释, 不做硬过滤, 永不清空结果)

    - op='contains': 字段文本包含 value → +2
    - op='in': 字段文本包含 value 列表中任一 token → +2
    - op='=': 字段值相等 (如 outcome=film) → +2
    - op='keyword': 任意字段文本/extra_text 含 token → +1
    - outcome 'in' 过滤 (失败诊断反面教材) 命中时 +3, 让失败/部分成膜反应排前
    """
    bonus = 0
    for f in filters:
        field, op, value = f.get('field'), f.get('op'), f.get('value')
        cols = REACTION_FIELD_MAP.get(field)
        if cols is None:
            continue  # has_fluorine / innovation 等无 reaction 字段, 由关键词或 literature 侧处理
        text = ' '.join(str(data.get(c, '')) for c in cols).lower() + ' ' + extra_text.lower()
        if op == 'contains' and str(value).lower() in text:
            bonus += 2
        elif op == 'in':
            tokens = value if isinstance(value, list) else [value]
            if any(str(t).lower() in text for t in tokens):
                bonus += 3 if field == 'outcome' else 2
        elif op == '=':
            if field == 'outcome':
                if str(data.get('outcome', '')).lower() == str(value).lower():
                    bonus += 2
            elif str(value).lower() in text:
                bonus += 2
        elif op == 'keyword':
            tokens = value if isinstance(value, list) else [value]
            if any(str(t).lower() in text for t in tokens):
                bonus += 1
    return bonus


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

    # 关键词提取 (旧硬编码, 保留兜底)
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
        keywords.append('fluor')
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

    # 波次2: nl2graph 中文解析 → 补充关键词 + 结构化过滤条件 (可解释)
    parsed = None
    filters = []
    if HAS_NL2GRAPH:
        try:
            parsed = nl_to_query(query_text)
            filters = parsed.get('filters', [])
            for kw in parsed.get('keywords', []):
                if kw and kw not in keywords:
                    keywords.append(kw)
        except Exception as e:
            if verbose:
                print(f'  (nl2graph 解析失败, 降级旧关键词: {e})')

    if not keywords:
        keywords = [query_text.lower()]

    if verbose and parsed:
        print(f'  波次2解析: intent={parsed.get("intent")}, filters={len(filters)} 条')

    # 扫 reaction 节点 (关键词打分 + 过滤条件软加权)
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
        if filters:
            score += filter_bonus(filters, r)
        if score > 0:
            reaction_hits.append({'id': rid, 'score': score, 'data': dict(r)})

    # 扫 literature 节点
    lit_hits = []
    for lid, l in get_literatures(G):
        text = ' '.join([
            str(l.get('journal', '')),
            str(l.get('system', '')),
            str(l.get('innovation', '')),
        ]).lower()
        score = match_score(text, keywords)
        if filters:
            score += filter_bonus(filters, {}, extra_text=text)
        if score > 0:
            lit_hits.append({'id': lid, 'score': score, 'data': dict(l)})

    # 波次2: importance 加权 (score *= (1+imp)); importance.json 缺失/加载失败静默跳过
    importance = _load_importance()
    if importance:
        for h in reaction_hits:
            h['score'] = h['score'] * (1 + importance.get(h['id'], 0.0))
        for h in lit_hits:
            h['score'] = h['score'] * (1 + importance.get(h['id'], 0.0))

    reaction_hits.sort(key=lambda x: -x['score'])
    lit_hits.sort(key=lambda x: -x['score'])

    # 波次2: 零结果兜底 (降级链: 永不整体失败) —— 双链全空时按单词边界逐词重试
    if not reaction_hits and not lit_hits and len(keywords) > 1:
        for kw in keywords:
            kw_low = kw.lower()
            for rid, r in get_reactions(G):
                text = ' '.join(str(r.get(c, '')) for c in
                                ['aldehyde_name', 'amine_name', 'solvent', 'temperature',
                                 'outcome', 'synthesis_mode', 'interface_type', 'catalyst']).lower()
                if kw_low in text:
                    reaction_hits.append({'id': rid, 'score': 0.5, 'data': dict(r)})
            for lid, l in get_literatures(G):
                text = ' '.join(str(l.get(c, '')) for c in
                                ['journal', 'system', 'innovation']).lower()
                if kw_low in text:
                    lit_hits.append({'id': lid, 'score': 0.5, 'data': dict(l)})
            if reaction_hits or lit_hits:
                break

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