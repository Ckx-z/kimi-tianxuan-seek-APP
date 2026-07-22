"""
graphrag_v2/nl2graph.py
========================
自然语言 → 图查询翻译 (v2 升级 · 波次2 中文解析增强)

输入: 自然语言查询
输出: 结构化查询 {entities, filters, keywords, intent, edge_types, depth}

实现: 规则 + 关键词提取 (LLM 可选增强)

波次2 新增能力:
- 中文温度: "120度" / "120 度" / "室温"
- 中文溶剂/催化剂/调制剂词表 (模块级 dict, 便于扩充)
- 用量/比例词: 保留为关键词, 不做硬过滤
- 后处理词: 洗脱/索氏/洗涤/干燥 → intent='workup' + 关键词直通
- 失败诊断词: 失败/不连续/不牢/裂/粉 → intent='failure_diagnosis',
  关联 outcome 过滤 (powder/unknown 等失败/部分成膜反应) 做反面教材检索

所有词表均为模块级 dict, 直接追加条目即可扩充。
"""
import re

# ===================== 模块级词表 (可直接扩充) =====================

# 单体缩写 → 标准名 (原有 9 个硬编码, 保留并可扩充)
MONOMER_KEYWORDS = ['tapt', 'tfpt', 'tapb', 'tfpb', 'tfmb', 'fpda', 'tfta', 'boc']

# 醛/胺 编号代号
MONOMER_CODES = ['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7',
                 'b1', 'b2', 'b3', 'b4', 'b5', 'b6',
                 'h1', 'h2', 'h3', 'h4']

# 溶剂词表: 触发词(小写) → 图中文本匹配 token 列表 (中英双语, 因 solvent 字段为中英混排)
SOLVENT_DICT = {
    '甲苯': ['甲苯', 'toluene'],
    'toluene': ['甲苯', 'toluene'],
    '氯仿': ['氯仿', 'chloroform', 'chcl3'],
    '三氯甲烷': ['氯仿', 'chloroform', 'chcl3'],
    'chloroform': ['氯仿', 'chloroform', 'chcl3'],
    '二氧六环': ['二氧六环', 'dioxane'],
    '二噁烷': ['二氧六环', 'dioxane'],
    'dioxane': ['二氧六环', 'dioxane'],
    '均三甲苯': ['均三甲苯', 'mesitylene'],
    'mesitylene': ['均三甲苯', 'mesitylene'],
    'dmf': ['dmf', 'n,n-二甲基甲酰胺'],
    'dmso': ['dmso', '二甲基亚砜'],
    '甲醇': ['甲醇', 'methanol', 'meoh'],
    'methanol': ['甲醇', 'methanol', 'meoh'],
    '乙醇': ['乙醇', 'ethanol', 'etoh'],
    '丙酮': ['丙酮', 'acetone'],
    '乙腈': ['乙腈', 'acetonitrile'],
    '正丁醇': ['正丁醇', 'butanol', 'n-butanol'],
    '二氯甲烷': ['二氯甲烷', 'dcm', 'ch₂cl₂', 'ch2cl2'],
    'dcm': ['二氯甲烷', 'dcm', 'ch₂cl₂', 'ch2cl2'],
    '水': ['water', '水'],  # 水相/水热常见, 单词"水"命中过多时由打分兜底
    'water': ['water', '水'],
    '邻二氯苯': ['邻二氯苯', 'o-dichlorobenzene'],
    'nmp': ['nmp', 'n-甲基吡咯烷酮'],
    '四氢呋喃': ['四氢呋喃', 'thf'],
    'thf': ['四氢呋喃', 'thf'],
}

# 催化剂词表
CATALYST_DICT = {
    '乙酸': ['乙酸', 'acetic acid', 'acoh', 'hoac', '醋酸'],
    '醋酸': ['乙酸', 'acetic acid', 'acoh', 'hoac', '醋酸'],
    'acetic': ['乙酸', 'acetic acid', 'acoh', '醋酸'],
    '氨水': ['氨水', 'ammonia', 'nh4oh', 'nh3·h2o', '氢氧化铵'],
    'sc(otf)3': ['sc(otf)', 'scandium triflate', '三氟甲磺酸钪'],
    '三氟甲磺酸钪': ['sc(otf)', 'scandium triflate', '三氟甲磺酸钪'],
    '对甲苯磺酸': ['对甲苯磺酸', 'ptsa', 'p-toluenesulfonic'],
    'ptsa': ['对甲苯磺酸', 'ptsa', 'p-toluenesulfonic'],
    '三氟乙酸': ['三氟乙酸', 'tfa', 'trifluoroacetic'],
    'tfa': ['三氟乙酸', 'tfa', 'trifluoroacetic'],
    '盐酸': ['盐酸', 'hcl'],
    '氢氧化钠': ['氢氧化钠', 'naoh'],
    '三乙胺': ['三乙胺', 'triethylamine', 'et3n', 'tea'],
    '吡啶': ['吡啶', 'pyridine'],
    '哌啶': ['哌啶', 'piperidine'],
}

# 调制剂词表 (调制剂/调节剂/modulator 概念 → 关键词直通; 具体调制剂如苯胺 → 兼作单体/添加剂实体)
MODULATOR_DICT = {
    '苯胺': ['苯胺', 'aniline'],
    'aniline': ['苯胺', 'aniline'],
    '调制剂': ['调制剂', '调节剂', 'modulator'],
    '调节剂': ['调制剂', '调节剂', 'modulator'],
    'modulator': ['调制剂', '调节剂', 'modulator'],
    '苯甲醛': ['苯甲醛', 'benzaldehyde'],
    'benzaldehyde': ['苯甲醛', 'benzaldehyde'],
    '吡咯烷': ['吡咯烷', 'pyrrolidine'],
}

# 用量/比例词: 不过滤, 仅保留为关键词 (图中无量化字段, 命中 stoichiometry 文本)
DOSAGE_WORDS = {
    '用量': ['用量', 'stoichiometry', '当量'],
    '比例': ['比例', 'stoichiometry', '1:1', '当量'],
    '当量': ['当量', 'eq', 'stoichiometry'],
    'eq': ['当量', 'eq', 'stoichiometry'],
    '醛胺比': ['比例', 'stoichiometry', '1:1'],
}

# 后处理词: 新 intent 'workup' + 关键词直通 (中英对照, 防零结果)
WORKUP_DICT = {
    '洗脱': ['洗脱', 'eluent', 'elution', 'wash'],
    '洗脱剂': ['洗脱', 'eluent', 'elution', 'wash'],
    '索氏': ['索氏', 'soxhlet', '提取'],
    '索氏提取': ['索氏', 'soxhlet', '提取'],
    'soxhlet': ['索氏', 'soxhlet', '提取'],
    '洗涤': ['洗涤', 'wash', 'rinse'],
    '干燥': ['干燥', 'dry', 'vacuum'],
    '纯化': ['纯化', 'purif', 'wash'],
    '后处理': ['后处理', 'workup', 'work-up', 'purif'],
    '活化': ['活化', 'activat', 'exchange'],
}

# 失败诊断词 → (outcome 过滤值列表, 补充关键词)
# 失败/部分成膜反应 = outcome 为 powder / unknown 的反应, 做反面教材检索
FAILURE_DICT = {
    '失败': (['powder', 'unknown'], ['powder', '沉淀', '失败']),
    '不连续': (['powder', 'unknown'], ['powder', 'film', '不连续']),
    '不牢': (['powder', 'unknown'], ['powder', 'film', '脱落']),
    '裂': (['powder', 'unknown'], ['powder', 'film', 'crack', '裂']),
    '碎': (['powder', 'unknown'], ['powder', 'film', '裂']),
    '粉': (['powder'], ['powder', '粉末', '沉淀']),
    '沉淀': (['powder'], ['powder', '沉淀']),
    '不成膜': (['powder', 'unknown'], ['powder', '沉淀']),
    '脱落': (['powder', 'unknown'], ['powder', 'film', '脱落']),
}

# 温度中文词 → (过滤 token 列表)
ROOM_TEMP_WORDS = ['室温', '常温', 'rt', 'room temperature']


def _add_filter(filters, field, op, value):
    """追加过滤条件 (去重)"""
    f = {'field': field, 'op': op, 'value': value}
    if f not in filters:
        filters.append(f)


def nl_to_query(nl_text):
    """自然语言 → 结构化图查询

    返回: {
        'entities': [{'type': 'monomer|reaction|literature', 'name': ..., 'filter': {...}}],
        'filters': [{'field': 'temperature', 'op': 'contains', 'value': ...}, ...],
        'keywords': [str, ...],   # 波次2: 扁平检索词列表 (中英 token), 供检索底座直接使用
        'edge_types': ['uses_aldehyde', ...],
        'depth': int,
        'intent': 'local|global|relational|temporal|entity|workup|failure_diagnosis',
        'original': str,
    }
    """
    q = nl_text.lower()

    entities = []
    filters = []
    keywords = []  # 波次2: 所有命中的检索 token (中英), 供 query_graphrag 直接打分
    edge_types = []
    intent = 'local'
    depth = 2

    def add_kw(tokens):
        for t in tokens:
            if t and t not in keywords:
                keywords.append(t)

    # 1. 提取单体名
    for kw in MONOMER_KEYWORDS:
        if kw in q:
            entities.append({'type': 'monomer', 'name': kw.upper(), 'keyword': kw})
            add_kw([kw])

    # 2. 提取醛/胺编号 (词边界, 防 'a1' 误伤)
    for pat in MONOMER_CODES:
        if re.search(rf'(?<![a-z0-9]){pat}(?![a-z0-9])', q):
            entities.append({'type': 'monomer', 'name': pat.upper(), 'keyword': pat})
            add_kw([pat])

    # 3. 提取温度: 数字°C / 数字度 / 中文"度" / 室温
    temp_hit = False
    m = re.search(r'(\d+)\s*°\s*c', q)
    if not m:
        m = re.search(r'(\d+)\s*度', nl_text)  # 中文 "120度" / "120 度" (原文匹配, 度不在 lower 影响范围)
    if m:
        temp = int(m.group(1))
        _add_filter(filters, 'temperature', 'contains', str(temp))
        add_kw([str(temp)])
        temp_hit = True
    if not temp_hit and any(w in q for w in ROOM_TEMP_WORDS):
        _add_filter(filters, 'temperature', 'contains', '室温')
        add_kw(['室温', '25'])
        temp_hit = True

    # 4. 提取产物
    if '膜' in q or 'film' in q:
        _add_filter(filters, 'outcome', '=', 'film')
        add_kw(['film'])
    if '粉末' in q or '沉淀' in q or 'powder' in q:
        _add_filter(filters, 'outcome', '=', 'powder')
        add_kw(['powder'])
    if '晶' in q or 'crystal' in q:
        _add_filter(filters, 'outcome', '=', 'crystal')
        add_kw(['crystal'])

    # 5. 提取合成模式 / 界面
    if '异相' in q:
        _add_filter(filters, 'synthesis_mode', 'contains', '异相')
        add_kw(['异相', 'heterogeneous'])
    if '均相' in q:
        _add_filter(filters, 'synthesis_mode', 'contains', '均相')
        add_kw(['均相', 'homogeneous'])
    if '界面' in q or '液-液' in q or '液液' in q or 'interface' in q:
        _add_filter(filters, 'interface_type', 'contains', '界面')
        add_kw(['界面', '液-液', 'liquid-liquid', 'interface'])
    if '溶剂热' in q or 'solvothermal' in q:
        _add_filter(filters, 'synthesis_mode', 'contains', '溶剂热')
        add_kw(['溶剂热', 'solvothermal'])

    # 6. 溶剂词表 (中英 → solvent 过滤 + 关键词)
    for trig, tokens in SOLVENT_DICT.items():
        if trig.lower() in q:
            _add_filter(filters, 'solvent', 'in', tokens)
            add_kw(tokens)

    # 7. 催化剂词表
    for trig, tokens in CATALYST_DICT.items():
        if trig.lower() in q:
            _add_filter(filters, 'catalyst', 'in', tokens)
            add_kw(tokens)

    # 8. 调制剂词表 (modulator 概念节点/关键词)
    for trig, tokens in MODULATOR_DICT.items():
        if trig.lower() in q:
            _add_filter(filters, 'modulator', 'keyword', tokens)
            add_kw(tokens)
            if trig in ('苯胺', 'aniline', '苯甲醛', 'benzaldehyde'):
                entities.append({'type': 'modulator', 'name': tokens[0], 'keyword': trig})

    # 9. 用量/比例词: 不过滤, 仅保留关键词
    for trig, tokens in DOSAGE_WORDS.items():
        if trig.lower() in q:
            add_kw(tokens)

    # 10. 后处理词: intent='workup' + 关键词直通
    workup_hit = False
    for trig, tokens in WORKUP_DICT.items():
        if trig.lower() in q:
            add_kw(tokens)
            workup_hit = True
    if workup_hit:
        intent = 'workup'
        depth = 1

    # 11. 失败诊断词: intent='failure_diagnosis', outcome 过滤失败/部分成膜做反面教材
    failure_hit = False
    for trig, (outcomes, tokens) in FAILURE_DICT.items():
        if trig in nl_text:  # 中文词用原文匹配, 避免 lower 对全角的副作用
            _add_filter(filters, 'outcome', 'in', outcomes)
            add_kw(tokens)
            failure_hit = True
    if failure_hit:
        intent = 'failure_diagnosis'
        depth = 2

    # 12. 提取含氟
    if '含氟' in q or 'fluorine' in q or 'cf3' in q or '氟' in q:
        _add_filter(filters, 'has_fluorine', '=', True)
        add_kw(['fluor', '氟', 'cf3'])  # 避免单字母 'f' 泛匹配; 命中 fluoro-/trifluoromethyl/中文氟

    # 13. 提取 BOC
    if 'boc' in q or '保护' in q or '脱保护' in q:
        _add_filter(filters, 'innovation', 'contains', 'Boc')
        add_kw(['boc'])

    # 14. intent 分类 (workup / failure_diagnosis 优先, 不被覆盖)
    if intent == 'local':
        if any(k in q for k in ['为什么', '原因', '怎么', '如何']):
            intent = 'relational'
            depth = 3
        elif any(k in q for k in ['总结', '综述', '所有', '全部']):
            intent = 'global'
            depth = 1
        elif any(k in q for k in ['进展', '近年', '趋势']):
            intent = 'temporal'
            depth = 2
        elif len(entities) == 0 and not filters:
            intent = 'entity'
            depth = 1

    # 15. edge_types 推断
    if entities and any(e['type'] == 'monomer' for e in entities):
        edge_types = ['uses_aldehyde', 'uses_amine', 'produces']

    return {
        'entities': entities,
        'filters': filters,
        'keywords': keywords,
        'edge_types': edge_types,
        'depth': depth,
        'intent': intent,
        'original': nl_text,
    }


def query_to_str(parsed):
    """结构化查询 → 可读字符串 (可解释性: 打印解析结果)"""
    lines = [f'意图: {parsed["intent"]}']
    if parsed['entities']:
        lines.append(f'实体: {[e["name"] for e in parsed["entities"]]}')
    if parsed['filters']:
        lines.append('过滤:')
        for f in parsed['filters']:
            lines.append(f'  - {f["field"]} {f["op"]} {f["value"]}')
    if parsed.get('keywords'):
        lines.append(f'关键词: {parsed["keywords"]}')
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
        # 波次2: 任务B 实测 6 问
        "TAPT 含氟二胺 成膜条件",
        "界面法 甲苯 120度 乙酸催化",
        "反应时间不够 膜不连续",
        "苯胺 调制剂 用量",
        "洗脱剂 索氏提取",
        "醛胺比例 结晶度",
    ]
    for t in tests:
        parsed = nl_to_query(t)
        print(f'\n>>> "{t}"')
        print(query_to_str(parsed))
