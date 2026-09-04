---
name: literature_search_sop
description: 文献检索标准作业程序（联网/学术检索场景）
default-enabled: true
---
# 文献检索 SOP

涉及文献、方法学、最新进展的问题，按此顺序检索：

1. **学术源优先**：academic_search（arXiv / PubMed / Semantic Scholar /
   Crossref 聚合）。检索词用英文关键词 + 年份范围（如 "covalent organic
   framework membrane 2023 2024"）。
2. **核实 DOI**：拿到 DOI 后用 lookup_paper_doi 核实物元数据（标题/作者/
   期刊/年份），引用时给出「标题 + DOI 链接」；arXiv 预印本注明"预印本，
   未经同行评审"。
3. **网页补充**：学术源查不到（如工业新闻、试剂供应商、最新预印）时才用
   web_search；网页内容需进一步确认时用 fetch_page 读全文。
4. **本地证据对照**：与本系统图谱（query_graphrag）与用户实验记录
   （read_experiment_records）对照，标注"文献结论 vs 本组历史经验"的异同。
5. **引用纪律**：只引用本轮检索真实返回的内容；每条论断标注来源；检索不到
   时如实说明，不编造文献。
