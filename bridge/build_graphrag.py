"""
bridge/build_graphrag.py
========================
GraphRAG 索引构建脚本 (Phase 1-2)

输入:
- tianxuan-seek/data/structured/*.yaml (954 篇文献)
- tianxuan-seek/data/processed/merged_monomer_pool.csv (1059 单体)
- tianxuan-seek/data/processed/v5_train_stage1*.csv (6201-6392 反应)

输出:
- minimax/bridge/graphrag/nodes_*.jsonl (7 类节点)
- minimax/bridge/graphrag/edges_*.jsonl (8 类边)
- minimax/bridge/graphrag/graph.pkl (NetworkX 图)
- minimax/bridge/graphrag/meta.json (统计)

运行:
    python bridge/build_graphrag.py
"""
import os
import csv
import json
import glob
import pickle
import hashlib
import re
from collections import defaultdict, Counter
from pathlib import Path
import yaml as pyyaml

# 路径
TIANXUAN = Path(r'C:\Users\ckx\Desktop\tianxuan seek\data')
OUT_DIR = Path(__file__).resolve().parent / 'graphrag'
OUT_DIR.mkdir(exist_ok=True)


# ====== 工具函数 ======

def smiles_hash(smiles: str) -> str:
    """SMILES → 稳定 id 哈希"""
    return 'M-' + hashlib.md5(smiles.encode()).hexdigest()[:12]


def normalize_solvent(text: str) -> list:
    """从 yaml solvent 字段提取多个溶剂
    例: 'mesitylene/1,4-dioxane (1/9)' → ['mesitylene', '1,4-dioxane']
    """
    if not text or text == 'null':
        return []
    # 拆分: 用 / , ; 等分隔
    parts = re.split(r'[/,;、，；]+', text)
    result = []
    for p in parts:
        p = p.strip()
        # 去括号说明 (1/9, 体积比, 等等)
        p = re.sub(r'\([^)]*\)', '', p).strip()
        if p and len(p) > 2 and not p.lower().startswith(('v ', 'for ', 'used ')):
            result.append(p)
    return result


def normalize_catalyst(text: str) -> list:
    """从 yaml catalyst 字段提取催化剂"""
    if not text or text == 'null':
        return []
    # 简单拆分 (如果含多个)
    parts = re.split(r'[/,;、，；]+', text)
    result = []
    for p in parts:
        p = p.strip()
        p = re.sub(r'\([^)]*\)', '', p).strip()
        if p and len(p) > 1:
            result.append(p)
    return result


def normalize_outcome(text: str, mode: str) -> str:
    """从 yaml film_crystallinity_fluorine + synthesis_mode 抽取产物类型
    返回: 'film' | 'powder' | 'crystal' | 'no_product'
    """
    text = (text or '').lower()
    mode = (mode or '').lower()
    # 检测 '膜' / 'film'
    if '定向薄膜' in text or 'thin film' in text or '薄膜' in text:
        return 'film'
    if '膜' in text and '粉末' not in text:
        return 'film'
    if '晶' in text or 'crystal' in text or 'crystallin' in text:
        return 'crystal'
    if '沉淀' in text or '粉末' in text or 'powder' in text or 'precipitate' in text:
        return 'powder'
    if '异相' in mode:
        return 'powder'  # 异相通常 = 沉淀
    if '均相' in mode:
        return 'crystal'
    return 'unknown'


def normalize_interface(text: str) -> str:
    """从 yaml interface_type 提取界面类型"""
    if not text:
        return 'unknown'
    if '固-液' in text or '固液' in text:
        return 'solid-liquid'
    if '液-液' in text or '液液' in text:
        return 'liquid-liquid'
    if '气-液' in text or '气液' in text:
        return 'gas-liquid'
    if '气-固' in text:
        return 'gas-solid'
    if '液相' in text:
        return 'liquid-phase'
    return 'other'


# ====== 节点构造 ======

def build_monomer_nodes():
    """从 merged_monomer_pool.csv 构造 Monomer 节点"""
    fp = TIANXUAN / 'processed' / 'merged_monomer_pool.csv'
    nodes = []
    with open(fp, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            smi = row['smiles'].strip()
            if not smi:
                continue
            nodes.append({
                'id': smiles_hash(smi),
                'smiles': smi,
                'best_name': row.get('best_name', ''),
                'type': row.get('monomer_type', ''),
                'has_fluorine': row.get('has_fluorine', 'False') == 'True',
                'n_f_atoms': int(row.get('n_f_atoms', 0) or 0),
                'has_cf3': row.get('has_cf3', 'False') == 'True',
                'n_aldehyde': int(row.get('n_aldehyde', 0) or 0),
                'n_amine': int(row.get('n_amine', 0) or 0),
                'n_papers': int(row.get('n_papers', 0) or 0),
                'source': row.get('source', ''),
            })
    return nodes


def find_yamls_for_reaction(ald_name, am_name, yaml_lookup, reagent_to_lid):
    """用 aldehyde_name + amine_name fuzzy 找含这两个单体的 yaml
    返回: [(lit_id, yaml_data, score), ...]
    """
    if not ald_name or not am_name:
        return []
    results = []
    ald_base = re.split(r'[（(]', ald_name)[0].strip().lower()
    am_base = re.split(r'[（(]', am_name)[0].strip().lower()
    for lid, data in yaml_lookup.items():
        reagent = (data.get('reagent') or '').lower()
        if ald_base in reagent and am_base in reagent:
            results.append((lid, data, 2))  # 同时匹配
        elif ald_base in reagent or am_base in reagent:
            results.append((lid, data, 1))  # 单匹配
    return results


def build_reaction_nodes_with_yamls(monomer_lookup, yaml_lookup):
    """从 v5_train_stage1*.csv 构造 Reaction 节点 + 用 yaml fuzzy 关联 outcome"""
    reactions = []
    for fname in ['v5_train_stage1.csv', 'v5_train_stage1_aug_v2.csv']:
        fp = TIANXUAN / 'processed' / fname
        with open(fp, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                paper_id = str(row['paper_id']).strip()
                group_id = str(row['group_id']).strip().rstrip('.0')
                ald_smi = row['aldehyde_smiles'].strip()
                am_smi = row['amine_smiles'].strip()
                ald_name = row.get('aldehyde_name', '')
                am_name = row.get('amine_name', '')

                if not ald_smi or not am_smi:
                    continue

                # 用 yaml fuzzy 找 outcome / catalyst / interface
                matched_yamls = find_yamls_for_reaction(ald_name, am_name, yaml_lookup, None)
                # 取最优 (score=2 优先)
                matched_yamls.sort(key=lambda x: -x[2])
                best_yaml = matched_yamls[0][1] if matched_yamls else {}

                film_text = best_yaml.get('film_crystallinity_fluorine', '') or ''
                mode = best_yaml.get('synthesis_mode', '') or ''
                outcome = normalize_outcome(film_text, mode)

                catalyst_str = best_yaml.get('catalyst', '') or ''
                interface_str = best_yaml.get('interface_type', '') or ''

                # solvent 从 CSV 优先, fallback yaml
                solvent_str = row.get('solvent', '') or best_yaml.get('solvent', '')

                # yaml lit_id (用于 cited_in 边)
                yaml_lid = best_yaml.get('literature_id', '') if best_yaml else ''

                reactions.append({
                    'id': f'R-{paper_id}-{group_id}',
                    'paper_id': paper_id,
                    'group_id': group_id,
                    'aldehyde_smiles': ald_smi,
                    'amine_smiles': am_smi,
                    'aldehyde_name': ald_name,
                    'amine_name': am_name,
                    'stoichiometry': row.get('stoichiometry', ''),
                    'solvent': solvent_str,
                    'temperature': row.get('temperature', ''),
                    'source_db': row.get('source_db', fname),
                    'outcome': outcome,
                    'synthesis_mode': mode,
                    'catalyst': catalyst_str,
                    'interface_type': interface_str,
                    'yaml_lid': yaml_lid,
                })
    # 去重
    seen = {}
    for r in reactions:
        if r['id'] not in seen:
            seen[r['id']] = r
    return list(seen.values())


def build_literature_nodes():
    """从 yaml 构造 Literature 节点"""
    nodes = []
    yaml_files = sorted(glob.glob(str(TIANXUAN / 'structured' / '*.yaml')))
    for yp in yaml_files:
        with open(yp, encoding='utf-8') as f:
            data = pyyaml.safe_load(f)
        if not data:
            continue
        lit_id = data.get('literature_id') or Path(yp).stem
        nodes.append({
            'id': 'L-' + hashlib.md5(lit_id.encode()).hexdigest()[:12],
            'literature_id': lit_id,
            'yaml_path': yp,
            'journal': data.get('journal', ''),
            'system': data.get('system', ''),
            'innovation': data.get('innovation', ''),
        })
    return nodes, yaml_files


# ====== 边构造 ======

def build_edges(reactions, monomers, literature):
    """构造 8 类边"""
    # ID 索引
    mono_by_smi = {m['smiles']: m['id'] for m in monomers}
    lit_by_id = {l['literature_id']: l['id'] for l in literature}
    # 也支持 yaml 文件名匹配
    for l in literature:
        if 'yaml_path' in l:
            stem = Path(l['yaml_path']).stem
            lit_by_id[stem] = l['id']

    edges = {
        'reaction_uses_aldehyde': [],
        'reaction_uses_amine': [],
        'reaction_cited_in': [],
        'reaction_produces': [],
        'monomer_cooccurs': [],
        'reaction_uses_solvent': [],
        'reaction_uses_catalyst': [],
        'reaction_at_interface': [],
    }

    # 用到的集合
    solvents_seen = set()
    catalysts_seen = set()
    interfaces_seen = set()
    outcomes_seen = set()
    cooccur_count = Counter()

    for r in reactions:
        rid = r['id']

        # 醛/胺 节点
        ald_id = mono_by_smi.get(r['aldehyde_smiles'])
        am_id = mono_by_smi.get(r['amine_smiles'])
        if ald_id:
            edges['reaction_uses_aldehyde'].append({'from': rid, 'to': ald_id})
        if am_id:
            edges['reaction_uses_amine'].append({'from': rid, 'to': am_id})

        # co-occurrence
        if ald_id and am_id:
            pair = tuple(sorted([ald_id, am_id]))
            cooccur_count[pair] += 1

        # solvent / catalyst / interface / outcome (聚合到节点)
        for s in normalize_solvent(r.get('solvent', '')):
            sid = 'S-' + hashlib.md5(s.lower().encode()).hexdigest()[:12]
            edges['reaction_uses_solvent'].append({'from': rid, 'to': sid, 'solvent_name': s})
            solvents_seen.add((sid, s))

        for c in normalize_catalyst(r.get('synthesis_mode', '')):  # synthesis_mode 不是 catalyst, fallback
            pass
        # catalyst
        for c in normalize_catalyst(r.get('catalyst', '')):
            cid = 'C-' + hashlib.md5(c.lower().encode()).hexdigest()[:12]
            edges['reaction_uses_catalyst'].append({'from': rid, 'to': cid, 'catalyst_name': c})
            catalysts_seen.add((cid, c))

        # interface
        iface = normalize_interface(r.get('interface_type', ''))
        if iface != 'unknown':
            iid = 'I-' + iface
            edges['reaction_at_interface'].append({'from': rid, 'to': iid, 'interface_name': iface})
            interfaces_seen.add((iid, iface))

        # outcome
        otype = r.get('outcome', 'unknown')
        if otype == 'unknown' or otype == '':
            otype = 'unknown'
        oid = 'O-' + otype
        edges['reaction_produces'].append({'from': rid, 'to': oid, 'outcome_type': otype})
        outcomes_seen.add((oid, otype))

        # literature 关联 (通过 yaml_lid)
        yaml_lid = r.get('yaml_lid', '')
        if yaml_lid and yaml_lid in lit_by_id:
            edges['reaction_cited_in'].append({'from': rid, 'to': lit_by_id[yaml_lid]})

    # co-occurrence 边
    for (a, b), cnt in cooccur_count.items():
        edges['monomer_cooccurs'].append({'from': a, 'to': b, 'weight': cnt})

    # 附加节点（solvent/catalyst/interface/outcome）
    extra_nodes = {
        'solvent': [{'id': sid, 'name': name} for sid, name in solvents_seen],
        'catalyst': [{'id': cid, 'name': name} for cid, name in catalysts_seen],
        'interface': [{'id': iid, 'name': iname} for iid, iname in interfaces_seen],
        'outcome': [{'id': oid, 'type': otype} for oid, otype in outcomes_seen],
    }

    return edges, extra_nodes


# ====== 主流程 ======

def main():
    print('=== GraphRAG 索引构建 ===\n')

    # 1. yaml 加载
    print('[1/5] 加载 yaml 文献...')
    yaml_files = sorted(glob.glob(str(TIANXUAN / 'structured' / '*.yaml')))
    yaml_lookup = {}
    for yp in yaml_files:
        with open(yp, encoding='utf-8') as f:
            data = pyyaml.safe_load(f)
        if data:
            lid = data.get('literature_id', '') or Path(yp).stem
            yaml_lookup[lid] = data
            yaml_lookup[Path(yp).stem] = data  # 文件名也可查
    print(f'  yaml 文献: {len(yaml_files)}')

    # 2. Monomer 节点
    print('[2/5] 构造 Monomer 节点...')
    monomers = build_monomer_nodes()
    print(f'  单体: {len(monomers)}')

    # 3. Literature 节点
    print('[3/5] 构造 Literature 节点...')
    literature, _ = build_literature_nodes()
    print(f'  文献: {len(literature)}')

    # 4. Reaction 节点
    print('[4/5] 构造 Reaction 节点...')
    reactions = build_reaction_nodes_with_yamls(monomers, yaml_lookup)
    print(f'  反应: {len(reactions)}')

    # 5. 边 + 附加节点
    print('[5/5] 构造边...')
    edges, extra_nodes = build_edges(reactions, monomers, literature)
    for k, v in edges.items():
        print(f'  {k}: {len(v)}')

    # 写 JSONL
    print('\n=== 写 JSONL ===')
    nodes_by_type = {
        'monomer': monomers,
        'reaction': reactions,
        'literature': literature,
        'solvent': extra_nodes['solvent'],
        'catalyst': extra_nodes['catalyst'],
        'interface': extra_nodes['interface'],
        'outcome': extra_nodes['outcome'],
    }

    for nt, nodelist in nodes_by_type.items():
        fp = OUT_DIR / f'nodes_{nt}.jsonl'
        with open(fp, 'w', encoding='utf-8') as f:
            for n in nodelist:
                f.write(json.dumps(n, ensure_ascii=False) + '\n')
        print(f'  {fp.name}: {len(nodelist)} nodes')

    for et, edgelist in edges.items():
        fp = OUT_DIR / f'edges_{et}.jsonl'
        with open(fp, 'w', encoding='utf-8') as f:
            for e in edgelist:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
        print(f'  {fp.name}: {len(edgelist)} edges')

    # 写 NetworkX 图
    print('\n=== 构造 NetworkX 图 ===')
    try:
        import networkx as nx
        G = nx.MultiDiGraph()

        for nt, nodelist in nodes_by_type.items():
            for n in nodelist:
                G.add_node(n['id'], node_type=nt, **n)

        for et, edgelist in edges.items():
            for e in edgelist:
                G.add_edge(e['from'], e['to'], edge_type=et, **{k: v for k, v in e.items() if k not in ('from', 'to')})

        graph_fp = OUT_DIR / 'graph.pkl'
        with open(graph_fp, 'wb') as f:
            pickle.dump(G, f, pickle.HIGHEST_PROTOCOL)
        print(f'  graph.pkl: {len(G.nodes)} nodes, {len(G.edges)} edges')
    except ImportError:
        print('  (networkx 未装, 跳过 graph.pkl)')

    # meta
    meta = {
        'build_date': '2026-07-13',
        'data_source': str(TIANXUAN),
        'node_counts': {k: len(v) for k, v in nodes_by_type.items()},
        'edge_counts': {k: len(v) for k, v in edges.items()},
    }
    meta_fp = OUT_DIR / 'meta.json'
    with open(meta_fp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f'\n  meta: {meta_fp}')

    print('\n✓ GraphRAG 索引构建完成')
    print(f'  索引目录: {OUT_DIR}')


if __name__ == '__main__':
    main()