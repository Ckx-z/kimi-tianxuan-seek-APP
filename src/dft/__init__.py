"""DFT（半经验 xTB）计算模块：结合能 / HOMO-LUMO gap / 偶极矩。

- engine: 计算管线（RDKit 3D 构象 → xtb --opt → 能量解析）
- cache:  单体对 + 方法档位结果缓存（user_data_root()/dft_cache/）
- log:    计算历史 JSONL（user_data_root()/dft_log.jsonl）
- jobs:   内存任务注册表 + 后台线程执行（供 FastAPI 异步轮询）
"""
