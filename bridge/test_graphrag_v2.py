import sys
sys.path.insert(0, 'bridge')
from graphrag_v2 import GraphRAGv2, print_result

print("=== GraphRAG v2 test ===")
g2 = GraphRAGv2()
try:
    result = g2.query("TAPT 含氟 膜 120°C", verbose=True)
    print_result(result)
    print("\n=== OK ===")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
