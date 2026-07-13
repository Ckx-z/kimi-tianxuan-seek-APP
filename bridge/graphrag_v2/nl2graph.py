"""
graphrag_v2/nl2graph.py
========================
自然语言 → 图查询翻译 (v2 升级)

输入: 自然语言查询
输出: 结构化查询 {entities, filters, intent, edge_types, depth}

实现: 规则 + 关键词提取 (LLM 可选增强)
"""
import re


def nl_to_query(nl_text):
    """自然语言 → 结构化图查询

    返回: {
        'entities': [{'type': 'monomer|reaction|literature', 'name': ..., 'filter': {...}}],
        'filters': [{'field': 'temperature', 'op': '>', 'value': 100}, ...],
        'edge_types': ['uses_aldehyde', ...],
        'depth': int,
        'intent': 'local|global|relational|temporal|entity',
        'original': str,
    }
    """
    q = nl_text.lower()

    entities = []
    filters = []
    edge_types = []
    intent = 'local'
    depth = 2

    # 1. 提取单体名
    monomer_keywords = ['tapt', 'tfpt', 'tapb', 'tfpb', 'tfmb', 'fpda', 'tfta', 'boc']
    for kw in monomer_keywords:
        if kw in q:
            entities.append({
                'type': 'monomer',
                'name': kw.upper(),
                'keyword': kw,
            })

    # 2. 提取醛/胺名
    aldehyde_patterns = ['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7',
                         'b1', 'b2', 'b3', 'b4', 'b5', 'b6',
                         'h1', 'h2', 'h3', 'h4']
    for pat in aldehyde_patterns:
        if pat in q:
            entities.append({
                'type': 'monomer',
                'name': pat.upper(),
                'keyword': pat,
            })

    # 3. 提取条件 (温度)
    temp_match = re.search(r'(\d+)\s*°?\s*c', q)
    if temp_match:
        temp = int(temp_match.group(1))
        filters.append({
            'field': 'temperature',
            'op': 'contains',
            'value': f'{temp}°',
        })

    # 4. 提取产物
    if '膜' in q or 'film' in q:
        filters.append({'field': 'outcome', 'op': '=', 'value': 'film'})
    if '粉末' in q or '沉淀' in q or 'powder' in q:
        filters.append({'field': 'outcome', 'op': '=', 'value': 'powder'})
    if '晶' in q or 'crystal' in q:
        filters.append({'field': 'outcome', 'op': '=', 'value': 'crystal'})

    # 5. 提取合成模式
    if '异相' in q:
        filters.append({'field': 'synthesis_mode', 'op': 'contains', 'value': '异相'})
    if '均相' in q:
        filters.append({'field': 'synthesis_mode', 'op': 'contains', 'value': '均相'})
    if '液-液' in q or '液液' in q:
        filters.append({'field': 'interface_type', 'op': 'contains', 'value': 'liquid-liquid'})

    # 6. 提取溶剂
    solvents = ['mesitylene', 'dioxane', 'dmf', 'dmso', 'chloroform', 'dcm', 'water']
    for s in solvents:
        if s in q:
            filters.append({'field': 'solvent', 'op': 'contains', 'value': s})

    # 7. 提取含氟
    if '含氟' in q or 'fluorine' in q or 'cf3' in q:
        filters.append({'field': 'has_fluorine', 'op': '=', 'value': True})

    # 8. 提取 BOC
    if 'boc' in q or '保护' in q or '脱保护' in q:
        filters.append({'field': 'innovation', 'op': 'contains', 'value': 'Boc'})

    # 9. intent 分类
    if any(k in q for k in ['为什么', '原因', '怎么', '如何']):
        intent = 'relational'
        depth = 3
    elif any(k in q for k in ['总结', '综述', '所有', '全部']):
        intent = 'global'
        depth = 1
    elif any(k in q for k in ['进展', '近年', '趋势']):
        intent = 'temporal'
        depth = 2
    elif len(entities) == 0:
        intent = 'entity'
        depth = 1

    # 10. edge_types 推断
    if entities and any(e['type'] == 'monomer' for e in entities):
        edge_types = ['uses_aldehyde', 'uses_amine', 'produces']

    return {
        'entities': entities,
        'filters': filters,
        'edge_types': edge_types,
        'depth': depth,
        'intent': intent,
        'original': nl_text,
    }


def query_to_str(parsed):
    """结构化查询 → 可读字符串"""
    lines = [f'意图: {parsed["intent"]}']
    if parsed['entities']:
        lines.append(f'实体: {[e["name"] for e in parsed["entities"]]}')
    if parsed['filters']:
        lines.append('过滤:')
        for f in parsed['filters']:
            lines.append(f'  - {f["field"]} {f["op"]} {f["value"]}')
    if parsed['edge_types']:
        lines.append(f'边类型: {parsed["edge_types"]}')
    return '\n'.join(lines)


if __name__ == '__main__':
    tests = [
        "TAPT + 含氟二胺 形成膜 120°C",
        "为什么自反应法失败？",
        "总结所有含氟 COF 体系",
        "TFPT + TFMB 在 120°C 是否能形成膜",
        "Boc 保护 缓慢释放 策略",
    ]
    for t in tests:
        parsed = nl_to_query(t)
        print(f'\n>>> "{t}"')
        print(query_to_str(parsed))