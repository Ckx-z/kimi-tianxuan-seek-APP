"""
graphrag_v2/test_nl2graph.py
============================
nl2graph 中文解析增强 (波次2) 单元测试

运行:
    python bridge/graphrag_v2/test_nl2graph.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nl2graph import nl_to_query, query_to_str


def check(name, cond):
    print(f'  {"✓" if cond else "✗"} {name}')
    return cond


def run():
    ok = True

    # 1. 中文温度: "120度" / "120 度" / "室温"
    p = nl_to_query('界面法 甲苯 120度 乙酸催化')
    ok &= check('120度 → temperature contains 120',
                any(f['field'] == 'temperature' and f['value'] == '120' for f in p['filters']))
    p = nl_to_query('在 120 度下反应')
    ok &= check('120 度(带空格) → temperature contains 120',
                any(f['field'] == 'temperature' and f['value'] == '120' for f in p['filters']))
    p = nl_to_query('室温下成膜')
    ok &= check('室温 → temperature contains 室温',
                any(f['field'] == 'temperature' and f['value'] == '室温' for f in p['filters']))
    p = nl_to_query("120°C 溶剂热")
    ok &= check("120°C 旧格式仍识别",
                any(f['field'] == 'temperature' and f['value'] == '120' for f in p['filters']))

    # 2. 溶剂词表
    p = nl_to_query('界面法 甲苯 120度 乙酸催化')
    ok &= check('甲苯 → solvent 过滤含 toluene',
                any(f['field'] == 'solvent' and 'toluene' in f['value'] for f in p['filters']))
    p = nl_to_query('氯仿和均三甲苯混合溶剂')
    sols = [t for f in p['filters'] if f['field'] == 'solvent' for t in f['value']]
    ok &= check('氯仿 → chloroform', 'chloroform' in sols)
    ok &= check('均三甲苯 → mesitylene', 'mesitylene' in sols)
    p = nl_to_query('DMF 中回流')
    ok &= check('DMF (大写) → solvent 过滤',
                any(f['field'] == 'solvent' for f in p['filters']))

    # 3. 催化剂词表
    p = nl_to_query('乙酸催化 成膜')
    ok &= check('乙酸 → catalyst 过滤含 acetic acid',
                any(f['field'] == 'catalyst' and 'acetic acid' in f['value'] for f in p['filters']))
    p = nl_to_query('氨水催化的体系')
    ok &= check('氨水 → catalyst 过滤',
                any(f['field'] == 'catalyst' for f in p['filters']))
    p = nl_to_query('Sc(OTf)3 催化')
    ok &= check('Sc(OTf)3 → catalyst 过滤',
                any(f['field'] == 'catalyst' for f in p['filters']))

    # 4. 调制剂词表
    p = nl_to_query('苯胺 调制剂 用量')
    ok &= check('苯胺 → modulator 实体',
                any(e['type'] == 'modulator' for e in p['entities']))
    ok &= check('调制剂 → modulator 过滤/关键词',
                any(f['field'] == 'modulator' for f in p['filters']))

    # 5. 用量/比例: 不过滤但保留关键词
    p = nl_to_query('醛胺比例 结晶度')
    ok &= check('比例 → 无硬过滤',
                not any(f['field'] in ('stoichiometry', 'ratio') for f in p['filters']))
    ok &= check('比例 → 关键词保留 stoichiometry',
                'stoichiometry' in p['keywords'])

    # 6. 后处理: 新 intent + 关键词直通
    p = nl_to_query('洗脱剂 索氏提取')
    ok &= check("洗脱/索氏 → intent='workup'", p['intent'] == 'workup')
    ok &= check('索氏 → 关键词含 soxhlet', 'soxhlet' in p['keywords'])

    # 7. 失败诊断: outcome 过滤 powder/unknown
    p = nl_to_query('反应时间不够 膜不连续')
    ok &= check("不连续 → intent='failure_diagnosis'", p['intent'] == 'failure_diagnosis')
    ok &= check('不连续 → outcome in [powder, unknown]',
                any(f['field'] == 'outcome' and f['op'] == 'in'
                    and 'powder' in f['value'] for f in p['filters']))

    # 8. 任务B 实测 6 问: 全部有解析产出 (实体/过滤/关键词至少其一非空)
    six = [
        'TAPT 含氟二胺 成膜条件',
        '界面法 甲苯 120度 乙酸催化',
        '反应时间不够 膜不连续',
        '苯胺 调制剂 用量',
        '洗脱剂 索氏提取',
        '醛胺比例 结晶度',
    ]
    for q in six:
        p = nl_to_query(q)
        has_out = bool(p['entities'] or p['filters'] or p['keywords'])
        ok &= check(f'6问解析非空: "{q}"', has_out)
        print('    ---')
        for line in query_to_str(p).splitlines():
            print('    ' + line)

    print(f'\n{"✓ 全部通过" if ok else "✗ 存在失败用例"}')
    return ok


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
