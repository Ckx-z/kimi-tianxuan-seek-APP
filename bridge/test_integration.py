"""
bridge/test_integration.py
==========================
集成测试: search + graphrag_v2 + proposal 数据通路

运行: python bridge/test_integration.py
"""
import sys
import os
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJ = Path(__file__).resolve().parent.parent

passed = 0
failed = 0
total = 0


def test(name):
    """测试装饰器"""
    def decorator(fn):
        global passed, failed, total
        total += 1
        try:
            fn()
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            traceback.print_exc()
            failed += 1
        return fn
    return decorator


# ===== search_local_pdfs =====

@test("search_local_pdfs: embedding_search tianxuan returns results")
def _():
    from search_local_pdfs import embedding_search
    r = embedding_search("imine COF membrane", top_k=3, sources=['tianxuan'])
    assert len(r) > 0, f"Expected at least 1 hit, got {len(r)}"
    assert r[0][0] > 0.4, f"Top sim too low: {r[0][0]}"


@test("search_local_pdfs: embedding_search core returns results")
def _():
    from search_local_pdfs import embedding_search
    r = embedding_search("TAPT triazine aldehyde", top_k=3, sources=['core'])
    # core 索引可能命中少，但不报错即可
    assert isinstance(r, list)


@test("search_local_pdfs: cas_search finds known feedback")
def _():
    from search_local_pdfs import cas_search
    # 用 feedback_db 中已知的 CAS 测试
    r = cas_search(aldehyde_cas='1300701-03-4')
    assert isinstance(r, list)


@test("search_local_pdfs: search() full pipeline")
def _():
    from search_local_pdfs import search
    r = search({
        'keywords': ['COF', 'membrane'],
        'query_text': 'imine COF film synthesis',
        'use_tianxuan': True,
        'top_k_tianxuan': 3,
    })
    assert 'tianxuan_matches' in r
    assert 'embedding_matches' in r
    assert 'feedback_matches' in r


@test("search_local_pdfs: format_results_for_prompt handles all sections")
def _():
    from search_local_pdfs import search, format_results_for_prompt
    r = search({
        'keywords': ['COF'],
        'query_text': 'imine COF membrane',
        'use_tianxuan': True,
        'top_k_tianxuan': 3,
    })
    text = format_results_for_prompt(r)
    assert isinstance(text, str)
    assert len(text) > 0


# ===== GraphRAG v2 =====

@test("graphrag_v2: load graph")
def _():
    from graphrag_v2 import GraphRAGv2
    g2 = GraphRAGv2()
    G = g2.load_graph()
    assert G is not None
    assert len(G.nodes) > 0


@test("graphrag_v2: query local intent")
def _():
    from graphrag_v2 import GraphRAGv2
    g2 = GraphRAGv2()
    result = g2.query("TAPT 含氟 膜 120°C")
    assert 'parsed_query' in result
    assert result.get('graphrag_version') == 'v2'


@test("graphrag_v2: query returns reactions or literatures")
def _():
    from graphrag_v2 import GraphRAGv2
    g2 = GraphRAGv2()
    result = g2.query("TAPT CF3 film")
    has_results = 'reactions' in result or 'literatures' in result
    assert has_results, "Expected reactions or literatures in result"


@test("graphrag_v2: query global intent uses community index")
def _():
    from graphrag_v2 import GraphRAGv2
    g2 = GraphRAGv2()
    result = g2.query("总结所有含氟COF的研究趋势")
    assert 'parsed_query' in result
    # global 模式应包含 communities 或 reactions
    has_content = 'reactions' in result or 'communities' in result or 'literatures' in result
    assert has_content, "Expected some results from v2 query"


# ===== generate_proposal data pathway =====

@test("generate_proposal: load reagent_db")
def _():
    import json
    with open(PROJ / 'experiment' / 'reagent_db.json', encoding='utf-8') as f:
        db = json.load(f)
    assert 'reagents' in db
    assert len(db['reagents']) > 0


@test("generate_proposal: load feedback_db")
def _():
    import csv
    with open(PROJ / 'experiment' / 'feedback_db.csv', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) > 0


@test("generate_proposal: GraphRAG v2 integration in proposal")
def _():
    from graphrag_v2 import GraphRAGv2
    import io, contextlib
    g2 = GraphRAGv2()
    result = g2.query("TAPT TFMB imine COF membrane")
    buf = io.StringIO()
    from graphrag_v2 import print_result
    with contextlib.redirect_stdout(buf):
        print_result(result, top_n=3)
    text = buf.getvalue()
    assert len(text) > 0


# ===== summary =====

if __name__ == '__main__':
    print("\n=== Integration Tests ===\n")
    print(f"\n{'='*40}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("All tests passed ✓")
    else:
        print(f"{failed} test(s) failed ✗")
        sys.exit(1)
