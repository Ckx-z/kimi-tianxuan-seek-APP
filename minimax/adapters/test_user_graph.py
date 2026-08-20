# -*- coding: utf-8 -*-
"""
adapters/test_user_graph.py
===========================
P0/P1 闭环接入测试：

  ① user_graph 记录→图元素转换（EXP 节点 + 单体/溶剂/产物边）
  ② append_records 幂等增量（同 record_id 覆盖更新，不产生重复节点/边）
  ③ merge_into 合并保留包内已有节点属性
  ④ query_graphrag.load_graph(app_root=...) 合并侧车图；query() 命中用户
     失败实验记录（失败诊断提问 outcome 过滤加分）
  ⑤ iterate_suggest.retrieve_evidence 走 v2 检索链并在证据文本中引用
     用户实验记录（含降级链不断档）

运行:
  E:\\python3.12\\python.exe -m pytest minimax/adapters/test_user_graph.py -v
"""
import pickle
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import iterate_suggest as it  # noqa: E402  （模块级把 bridge/ 加进 sys.path）
import query_graphrag  # noqa: E402
import user_graph  # noqa: E402


# ---------------------------------------------------------------- 测试夹具

def _rec(record_id='rec_20990101_001', outcome='failed', **kw):
    rec = {
        'schema_version': '1.0',
        'record_type': 'experiment_record',
        'record_id': record_id,
        'favorite_id': 'fav_test_001',
        'experiment_no': 'T1',
        'date': '2099-01-01',
        'aldehyde': {'cas': '111-222-3', 'smiles': 'O=Cc1ccc(C=O)cc1',
                     'name': 'TAL 对苯二甲醛'},
        'amine': {'cas': '444-555-6', 'smiles': 'Nc1ccc(N)cc1',
                  'name': 'PPD 对苯二胺'},
        'conditions': {'solvent_1': '均三甲苯/二氧六环', 'catalyst': '乙酸',
                       'temperature_c': 120},
        'outcome': outcome,
        'failure_class': 'Class C',
        'strength': '得到黄色粉末而非连续膜',
        'notes': '',
        'mistakes': '升温过快未分段保温',
        'self_summary': '成膜失败，怀疑成核速率过快',
        'process_notes': '醛胺溶于均三甲苯，加乙酸，直接升至120度',
        'timeline': [{'time_label': '第1天', 'description': '出现浑浊'},
                     {'time_label': '第3天', 'description': '瓶底黄色粉末'}],
    }
    rec.update(kw)
    return rec


@pytest.fixture()
def app_root(tmp_path):
    """临时用户数据应用根（侧车图落在 tmp_path/data/graphrag_user/）"""
    return tmp_path


# ---------------------------------------------------------------- ① 记录→图元素

def test_record_to_graph_items():
    nodes, edges = user_graph.record_to_graph_items(_rec())
    exp_id = 'EXP-rec_20990101_001'
    assert exp_id in nodes
    d = nodes[exp_id]
    assert d['node_type'] == 'reaction'
    assert d['source'] == 'user_experiment'
    assert d['outcome'] == 'failed'
    assert d['failure_class'] == 'Class C'
    assert '升温过快' in d['mistakes']
    assert '成核速率' in d['self_summary']
    assert '第1天' in d['timeline']
    # 边：醛/胺/溶剂(2 组分)/催化剂/产物
    etypes = sorted(e[2] for e in edges)
    assert 'reaction_uses_aldehyde' in etypes
    assert 'reaction_uses_amine' in etypes
    assert etypes.count('reaction_uses_solvent') == 2
    assert 'reaction_uses_catalyst' in etypes
    assert 'reaction_produces' in etypes
    # 单体 ID 与包内图同一方案（md5(smiles) 前 12 位）
    import hashlib
    expect_mid = 'M-' + hashlib.md5('O=Cc1ccc(C=O)cc1'.encode()).hexdigest()[:12]
    assert expect_mid in nodes
    # 无 record_id 的记录不入图
    assert user_graph.record_to_graph_items({'outcome': 'failed'}) == ({}, [])


# ---------------------------------------------------------------- ② 幂等增量

def test_append_records_idempotent(app_root):
    rec = _rec()
    n, fp = user_graph.append_records([rec], app_root=app_root)
    assert n == 1 and fp.exists()
    G1 = user_graph.load_user_graph(app_root)
    n1, e1 = G1.number_of_nodes(), G1.number_of_edges()
    # 同 record_id 再摄入（改了总结）：覆盖更新而非新增
    rec2 = _rec(self_summary='更新后的总结：降温速率是关键')
    n, _ = user_graph.append_records([rec2], app_root=app_root)
    assert n == 1
    G2 = user_graph.load_user_graph(app_root)
    assert G2.number_of_nodes() == n1
    assert G2.number_of_edges() == e1
    assert G2.nodes['EXP-rec_20990101_001']['self_summary'] == \
        '更新后的总结：降温速率是关键'
    # 空摄入不写文件
    n, _ = user_graph.append_records([], app_root=app_root / 'empty')
    assert n == 0
    assert not (app_root / 'empty' / 'data' / 'graphrag_user'
                / 'graph_user.pkl').exists()


# ---------------------------------------------------------------- ③ 合并保留包内节点

def test_merge_into_preserves_package_nodes(app_root):
    import networkx as nx
    user_graph.append_records([_rec()], app_root=app_root)
    # 模拟包内图：已有同 SMILES 单体节点（带包内属性）与 O-film
    import hashlib
    mid = 'M-' + hashlib.md5('O=Cc1ccc(C=O)cc1'.encode()).hexdigest()[:12]
    G = nx.MultiDiGraph()
    G.add_node(mid, node_type='monomer', best_name='包内名称', source='package')
    n_exp = user_graph.merge_into(G, app_root=app_root)
    assert n_exp == 1
    assert G.nodes[mid]['best_name'] == '包内名称'  # 包内属性不被覆盖
    assert 'EXP-rec_20990101_001' in G
    assert G.has_edge('EXP-rec_20990101_001', mid)
    # 无侧车图时返回 0
    assert user_graph.merge_into(G, app_root=app_root / 'nope') == 0


# ---------------------------------------------------------------- ④ 检索命中用户实验记录

def test_query_hits_user_experiment(app_root):
    user_graph.append_records([_rec()], app_root=app_root)
    G = query_graphrag.load_graph(app_root=app_root)
    assert 'EXP-rec_20990101_001' in G  # 侧车图已合并
    res = query_graphrag.query('这个组合成膜失败了怎么改 对苯二甲醛', G=G)
    hits = {h['id'] for h in res['reactions']}
    assert 'EXP-rec_20990101_001' in hits
    # 失败诊断 outcome 过滤加分：用户失败记录应排进前 5
    top5 = [h['id'] for h in res['reactions'][:5]]
    assert 'EXP-rec_20990101_001' in top5


# ---------------------------------------------------------------- ⑤ 迭代检索链接入 v2

def test_retrieve_evidence_v2_cites_user_record(app_root, capsys):
    user_graph.append_records([_rec()], app_root=app_root)
    text, lit_refs, graph_ref_ids = it.retrieve_evidence(
        '这个组合失败了怎么改 对苯二甲醛 对苯二胺',
        _rec()['aldehyde'], _rec()['amine'],
        records=[_rec()], app_root=app_root, favorite=None)
    err = capsys.readouterr().err
    assert 'GraphRAG v2 检索' in err  # v2 链路实际点亮
    assert 'EXP-rec_20990101_001' in text
    assert '我的实验记录 rec_20990101_001' in text
    assert 'rec_20990101_001' in graph_ref_ids  # 纳入引用白名单


def test_retrieve_evidence_fallback_without_v2(app_root, monkeypatch, capsys):
    """v2 模块不可 import 时降级 v1 直查，证据不断档"""
    user_graph.append_records([_rec()], app_root=app_root)
    real_import = __import__

    def fake_import(name, *a, **kw):
        if name.startswith('graphrag_v2'):
            raise ImportError('模拟 v2 不可用')
        return real_import(name, *a, **kw)

    import builtins
    monkeypatch.setattr(builtins, '__import__', fake_import)
    text, lit_refs, graph_ref_ids = it.retrieve_evidence(
        '这个组合失败了怎么改 对苯二甲醛',
        _rec()['aldehyde'], _rec()['amine'],
        records=[_rec()], app_root=app_root, favorite=None)
    err = capsys.readouterr().err
    assert '降级 v1' in err
    assert 'EXP-rec_20990101_001' in text
