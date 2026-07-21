import sys
sys.path.insert(0, 'bridge')
from graphrag_v2 import GraphRAGv2

g2 = GraphRAGv2()
g2.build_indexes()
