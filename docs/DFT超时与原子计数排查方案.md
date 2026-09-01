# DFT 计算超时与原子计数问题排查方案（v1.5.1 候选）

> 状态：**仅诊断与方案，待确认后实施**。所有结论基于 2026-09-01 实际取证。

---

## 问题 1：计算超时诊断报告

### 原因分析（结论：不是"假死"，是真实计算量 > 超时上限 × 线程数不足）

测试体系：分子 A（70 原子，双缩合亚胺 + 三嗪）· 分子 B（23 原子，溴代芳醚），
复合物 93 原子。ETKDG 生成 5 构象仅 **7.8 秒**（A 刚性共轭，非瓶颈）。

真正瓶颈是 Psi4 精度档工作量的三重放大：

1. **一次作业包含 6 次完整 SCF**（`src/dft/psi4_backend.py` `_SCRIPT_TEMPLATE`）：
   counterpoise BSSE（复合物 + 2 个 ghost 单体，3 次）+ 片段单点能（2 次）+
   复合物性质 SCF+wfn（1 次）。93 原子 / def2-SVP 下每次 SCF 约 30–60 分钟
   （4 线程），总计 **3–6 小时**，远超超时上限。
2. **超时上限偏紧**：`psi4_timeout()`（psi4_backend.py:112）按原子数分档
   1800/3600/5400 秒；93 原子落在 >90 档 = **5400 秒（90 分钟）**。而该函数
   自己的 docstring 就写着"89 原子 CP 单点需约 67 min"——仅 CP 三连就逼近上限，
   6 次 SCF 必超。
3. **线程数只用 4**：`runtime_config.psi4_threads()`（runtime_config.py:198）
   默认 4，而本机 32 核。4 线程 vs 16 线程对 SCF 是近乎线性的 4× 提速，
   5400s 的预算本可以完成。
4. 资源无压力：PSI_SCRATCH=E:\psi4_scratch（E 盘剩余 166.6GB），内存上限
   6GB（`generate_psi4_script` memory_mb=6000），无 OOM/磁盘风险。

### 证据来源

- 打包版用户日志 `%APPDATA%\COF-Film-Recommend\data\dft_log.jsonl`：
  `ts=2026-09-01T11:30:36Z status=failed method=wb97xd3bj_svp err="Psi4 计算超时（超过 5400 秒仍未完成）…"`
- 同日志成功基准：45 原子 wb97xd3bj_svp 实跑 **697.16s** 完成——按 N² 外推
  93 原子单次 SCF ≈ 50 分钟，6 次 ≈ 5 小时，与超时事实吻合。
- 实测：ETKDG 分子 A 5 构象 7.83s（每构象 70 原子 xyz 有效）；atom-estimate
  pair 模式 93 = 70 + 23。
- 环境：psi4 1.11 已装（E:\ANACONDA\envs\psi4-env）、32 核 CPU、线程配置=4。

### 关于"任务假死"（单独说明）

**后端不存在无限阻塞的假死**：`_run_psi4_script` 是 0.5s 轮询 + 超时强杀
（psi4_backend.py:472-513），xTB 同理（engine.py `_run_xtb`）；v1.5.0 已支持
`cancel_event` 主动取消。用户感知的"表面在运行"来自：**SCF 阶段没有任何进度
输出**——进度行只在阶段边界打印（模板 line 301/305/310/314/325），一次 SCF
30–60 分钟静默，前端进度条停在 65% 长达 90 分钟 + 无运行时长显示。
另有两点需后续验证排除（非本次根因）：① OMP 线程超订（若用户自行把
OMP_NUM_THREADS 调大，psi4 内部 MKL 会冲突）；② 优化档（optimize=True）时
工作量再加一个完整几何优化。

### 修复方案（分三层，按优先级）

1. **线程（低成本高收益）**：`psi4_threads()` 默认 4 → 建议 16（32 核留余量）；
   DFT 页增加"Psi4 线程数"输入（DftJobRequest 已有 threads 字段，前端
   handleSubmit 未传 → 现恒用默认 4，补上 UI 即可）。
   涉及：src/runtime_config.py:198、webapp/src/pages/Dft.tsx（表单+提交）。
2. **超时与提示（必改）**：`psi4_timeout()` 按"SCF 次数×规模"重算分档
   （建议 ≤50→1800、≤90→10800、>90→21600，或改为"心跳续期"：轮询发现
   psi4_output.dat 仍在增长/CPU 活跃则延长）；失败时**保留 run 目录**
   （现 `finally: shutil.rmtree` 把 psi4_output.dat 证据删光），并把
   psi4_output.dat 尾部写入 dft_log；进度 hint 追加"已运行 X 分钟"。
   涉及：src/dft/psi4_backend.py:112、:472（轮询）、:744/:864（rmtree）。
3. **大体系降本（建议）**：>90 原子默认推荐 `wb97xd3bj_svp_quick`
   （def2-SV(P)、e_conv 3e-6、batch preset，SCF 成本约为 svp 档一半）；
   把"片段单点能 + 复合物性质 + fchk"做成可跳过选项（省 3 次 SCF，仅保
   CP 结合能），界面勾选"仅结合能（最快）"。
   涉及：src/dft/psi4_backend.py（脚本模板参数化）、webapp/src/pages/Dft.tsx。

### 针对本测试用例的具体调试建议

- 先复跑 45 原子基准（日志里 697s 那条）确认基线；再跑 93 原子 quick 档
  + threads=16，预期 40–80 分钟完成。
- 命令行单独验证：用 psi4-env python 跑 `generate_psi4_script(...)` 产物，
  观察 stdout 的 SCF iter 行是否推进（区分"慢"与"发散"）；发散时换
  `guess=gwh` 或加 `damping`。
- 失败现场保留：临时把 COF_DFT_TIMEOUT_PSI4 调大并在超时前手动取消，
  或按修复 2 落 run 目录保留后检查 psi4_output.dat。

---

## 问题 2：原子计数错误修复

### 当前逻辑分析

- 计数位置：后端 `engine.atom_count_with_h`（engine.py:872，`AddHs(mol).
  GetNumAtoms()`，含氢口径，与嵌入 xyz 完全一致）；前端经
  `GET /api/dft/atom-estimate`（api/routers/dft.py）防抖 400ms 取数。
  v1.5.0 起已删除旧"重原子×2"启发式。
- 实测（本次测试体系）：pair 模式 93 = A 70 + B 23；dimer 模式（draft 单体
  双醛 + 三嗪三胺）+ custom B = **89 = 66 + 23**；self_stack = 132 = 2×66。
  全部与后端实际计算的 xyz 原子数逐原子一致——**当前计数在数学上没有错误**。
- 用户感知"不一致"的根源（语义层）：
  1. **多位点单体只缩合第一个位点**：make_dimer 产物 66 原子（单亚胺键），
     而用户心中的"二聚体"（分子 A，双亚胺键）是 70 原子——差值正是第二
     个位点的缩合；
  2. self_stack 口径是"二聚体·二聚体"（×2），前端虽有口径说明文字但
     计数没有常驻卡片可核对；
  3. 原子数只在 Psi4 且 >50 原子的警告里出现，普通场景用户看不到拆解。

### 修复方案

1. DFT 页新增常驻**原子计数卡片**（模式自适应）：pair 显示 A/B/复合物；
   dimer 显示二聚体/X/复合物，multi_site 时附"仅首个位点缩合"注释；
   self_stack 附"复合物=二聚体×2"注释。数据直接用现有 atom-estimate。
2. 结果面板（DftResultPanel）展示 result.atom_budget（dimer/x/complex 三列，
   后端已返回，仅缺 UI）。
3. （可选）atom-estimate 同时返回重原子数与含氢数两套口径，便于核对文献
   口径。

### 涉及文件与代码

- webapp/src/pages/Dft.tsx（新增计数卡片 + 注释）
- webapp/src/components/dft/DftResultPanel.tsx（展示 atom_budget）
- api/routers/dft.py `dft_atom_estimate`（可选加重原子数）
- 后端计数逻辑本身**无需修改**（已精确）

---

## 问题 3：ETKDG 构象查看功能实现

### 当前状态

- 数据齐全：`POST /api/dft/conformers/generate` 已返回每构象
  `{id, xyz, rel_e_kj, rel_e_kcal, boltzmann_w}`；结果缓存落盘
  `data/dft_artifacts/conformers/<key>.json`。**无需新增后端接口**。
- 前端缺口：`ConformerGallery.tsx` 只有"列表 + 选用"按钮，无 XYZ 文本、
  无 3D 查看；但项目已有现成 3D 组件 `DftViewer3D.tsx`（3dmol.js 懒加载，
  已用于结果面板与手动摆放预览），直接复用即可。

### 实现方案

1. ConformerGallery 每个构象卡片加"查看"按钮（展开/弹层），内嵌：
   - `DftViewer3D`（props 传 item.xyz，单分子无需片段着色）；
   - XYZ 文本块（font-mono、可滚动），配"复制"（navigator.clipboard）与
     "下载 .xyz"（Blob + a[download]，纯前端）。
2. 无需新后端接口；如需"历史检索结果回看"，可加
   `GET /api/dft/conformers/{cache_key}` 读缓存文件（可选，不做也行）。

### 需要新增的接口

无必需新增（xyz 已在响应中）；可选 GET 缓存回看接口如上。

### 前端组件设计

- 改：`webapp/src/components/dft/ConformerGallery.tsx`（查看按钮 + 弹层）
- 新：`webapp/src/components/dft/ConformerDetail.tsx`（XYZ 文本 + 复制/下载
  + 复用 DftViewer3D）
- 复用：`webapp/src/components/dft/DftViewer3D.tsx`、ui/dialog

### 涉及文件与代码

- webapp/src/components/dft/ConformerGallery.tsx（现 145 行，无查看逻辑）
- webapp/src/components/dft/ConformerDetail.tsx（新增）
- webapp/src/components/dft/DftViewer3D.tsx（复用，props 已支持 xyz）

---

## 实施顺序建议（确认后）

1. 问题 1 第 1+2 层（线程默认值 + 前端线程输入 + 超时重分档 + 失败现场保留）
2. 问题 2（计数卡片 + 结果面板 atom_budget）
3. 问题 1 第 3 层（大体系 quick 档建议 + 仅结合能模式）与问题 3（查看功能）
4. 回归：pytest 全量 + tsc + vite build + 真实 93 原子 quick 档基准跑通
