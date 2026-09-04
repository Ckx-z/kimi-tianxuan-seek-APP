---
name: iterate_methodology
description: 方案迭代建议的解读与深化方法论（页⑤ 建议 → 助手讨论场景）
default-enabled: true
---
# 方案迭代方法论

当讨论来自「方案迭代」的建议（suggestion_ids 上下文）时，按以下框架展开：

1. **先定位建议类型**：condition_adjust（调参）/ new_candidate（新候选单体）/
   literature（文献证据）。
2. **调参类**：逐字段解释为什么改（温度/时间/催化剂/溶剂各影响哪一步——席夫碱
   成键动力学、结晶成核、界面成膜），改动之间的耦合（如降温度必须延长反应时间）。
3. **新候选类**：结合 predict_film 打分与 OOD 标记评估风险，给出该候选与现用
   单体在电子性质/位阻上的差异（可调 get_monomer_props）。
4. **证据类**：把每条证据对应回原始文献（标题 + DOI），说清它支持结论的哪一环，
   证据不足处明说"证据有限"。
5. **收尾**：把讨论结论归纳成 1–3 条可执行的下一步，并提示可用
   generate_plan_card 落成实验方案卡。
