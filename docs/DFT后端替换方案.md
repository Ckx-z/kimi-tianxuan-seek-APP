# DFT 计算后端替换方案（2026-08-27）

> 状态：方案待用户确认后实施
> 背景：当前后端为 **xTB**（半经验紧束缚 GFN2-xTB）。用户反馈计算精度不可接受——3D 吸附位点摆放位置不准、结合能误差大。本方案调研全网开源替代品并给出替换路径。

## 一、问题定义

现有 DFT 模块的功能边界：
1. 二聚体 + 客体物质的结合能计算（选项 1），以及任意两物质结合能（选项 2）
2. 3D 结合构象展示（依赖几何优化得到的吸附位点）
3. 用户期望能输出 Gaussian 风格的 chk / fchk 文件（对接 Gaussian 工作流）

xTB 的短板：
- GFN2-xTB 是**半经验**方法，势能面粗糙，几何优化得到的吸附位点摆放与真实 DFT 结果偏差明显
- 结合能只有定性参考价值，无法达到 kcal/mol 级精度
- 无法输出 fchk（无此写出器）

## 二、候选后端调研结论

| 后端 | 类型 | 精度 | Windows 支持 | 分发许可 | 可打包性 | 结论 |
|------|------|------|-------------|---------|---------|------|
| **Psi4** | 真 DFT（B3LYP-D3、ωB97X-D3BJ + def2 基组）| 科研级，BSSE counterpoise 校正 | ✅ conda-forge 有 win-64 包（1.11，2026-06 更新） | LGPL/BSD/MIT，**可再分发** | 独立 conda 环境子进程调用，约 300MB+ | **推荐主后端** |
| **MACE-OFF23** | ML 力场（有机分子专用） | 近 DFT：大模型 0.5–1.0 meV/atom，分子间力误差仅 DFT 量级 5–10% | ✅ pip 可装（依赖 torch） | MIT，可再分发 | torch 生态体积大 | 可选精度档 |
| ANI-2x (torchani) | ML 力场 | 宣传近 CCSD(T) ~1 kcal/mol；但 MACE-OFF23 基准显示其全面逊于 MACE（二面角、升华焓、密度） | ✅ pip 可装 | MIT | torch 生态 | 次选，被 MACE 取代 |
| xTB | 半经验 GFN2-xTB | 快速筛选级 | ✅ 当前已集成 | LGPL | 已内置 | **保留为快速档** |
| PySCF | 真 DFT | 科研级 | ❌ 无官方 Windows wheel（pip 编译失败，社区 2025-07 实证） | Apache | — | **排除** |
| ORCA | 真 DFT | 科研级（学界金标准） | ✅ 官方 Windows 版 | 免费但**非开源、禁止再分发打包** | 只能用户自装后对接 | 远期可选对接 |

**来源**：conda-forge/psi4-feedstock 仓库、arXiv:2312.15211（MACE-OFF23，发表于 JACS 2025）、PySCF GitHub、torchani GitHub、ORCA 官方许可条款。

### 为什么不是 PySCF / ORCA
- PySCF：无官方 Windows 二进制，源码编译在本机 Windows 环境不可行，课题组其他成员也无法复现安装。
- ORCA：精度最好但许可证明确禁止将其打包进再分发安装包。只能做"检测到本机已装 ORCA 则对接"的可选模式，优先级低。

## 三、推荐架构：分层后端

```
DFT 计算模块
├── 快速档（默认）：xTB —— 现状保留，秒级出结果，用于批量筛选
├── 精度档（推荐新增）：Psi4 —— 真 DFT
│     ├── 方法：ωB97X-D3BJ / def2-SVP（默认）→ def2-TZVP（可选）
│     ├── 结合能：counterpoise (BSSE 校正) 结合能
│     ├── 几何优化 → 吸附位点构象（喂给现有 3D 展示）
│     └── 输出 fchk 文件（直接满足用户对接 Gaussian 的需求）
└── 可选 ML 档：MACE-OFF23 —— 近 DFT 精度、速度介于 xTB 与 Psi4 之间
      （依赖 torch，体积大，做成可选按需安装）
```

### Psi4 集成方式（复用已验证的模式）
- 复用 GNN 模型已验证的 **dphuanjing 独立 conda 环境 + subprocess 隔离** 模式：不进 NSIS 主安装包，首次使用时按需安装/检测 `psi4-env`。
- 后端 `src/dft/` 新增 `psi4_backend.py`：生成 Psi4 输入脚本（Python API），子进程调用 `psi4 --input`，解析输出 JSON/stdout，回收能量、优化后坐标、fchk 路径。
- API 层扩展：`POST /api/dft/calculate` 增加 `backend: "xtb" | "psi4" | "mace"` 参数；前端下拉选择，默认 xTB，选 Psi4 未安装时引导一键安装。
- 3D 展示、收藏、历史记录、导出等现有管线不变，只换计算内核。

### 精度基准（方案文档口径，实施后用本机基准数据替换）
- ωB97X-D3BJ/def2-TZVP 结合能 vs CCSD(T)/CBS：典型误差 < 0.5 kcal/mol（文献口径）
- MACE-OFF23 large：分子间力误差仅 DFT 量级的 5–10%，远优于 ANI-2x 与 GFN2-xTB
- GFN2-xTB：结合能定性参考，误差常达数 kcal/mol

## 四、实施计划（确认后执行）

| 步骤 | 内容 | 预估 |
|------|------|------|
| 1 | 创建 psi4-env conda 环境安装脚本 + 检测/引导安装 UI | 0.5 天 |
| 2 | `psi4_backend.py`：输入生成、子进程调用、输出解析、fchk 写出 | 1 天 |
| 3 | API `backend` 参数 + 前端档位选择 | 0.5 天 |
| 4 | 结合能 BSSE counterpoise 封装 + 与 xTB 结果并排展示 | 0.5 天 |
| 5 | 本机基准验证（选 3–5 个文献已知结合能的体系对比） | 0.5 天 |
| 6 | MACE-OFF23 可选档（二期） | 1 天 |

总计约 3–4 天（不含 MACE 档）。

## 五、风险与注意

- **体积**：psi4-env 约 300MB+，按需安装避免主安装包膨胀；安装过程需要网络（conda-forge）。
- **计算时长**：真 DFT 几何优化从 xTB 的秒级变为分钟级（小分子二聚体+客体在 def2-SVP 下约数分钟），前端需要进度提示。
- **许可合规**：Psi4 可再分发无风险；ORCA 任何时候都不打进安装包。
