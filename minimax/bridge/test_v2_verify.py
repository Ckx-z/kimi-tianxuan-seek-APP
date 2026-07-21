import sys
sys.path.insert(0, 'bridge')
from graphrag_v2 import GraphRAGv2, print_result

g2 = GraphRAGv2()

# Test global query (uses community index)
print("=== Global query (community-based) ===")
r = g2.query("含氟COF膜的研究趋势", verbose=True)
print_result(r, top_n=3)

print("\n=== Relational query (multi-hop) ===")
r2 = g2.query("TAPT和哪些醛反应能成膜", verbose=True)
print_result(r2, top_n=3)
