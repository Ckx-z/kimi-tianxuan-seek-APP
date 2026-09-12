# UI 革新方案（讨论稿 · 待用户拍板）

> 你的反馈：「全局 UI 没有高级感，想要革新的创作」。本稿先**只讨论不实施**：
> 先诊断「不高级」的具体成因，再给 3 条可选路线（每条都有公开可查的成熟参考项目），
> 最后给推荐组合与需要你拍板的问题。

---

## 〇、已实施：第 1 批「杠杆三件套」（2026-09-12，可一键回退）

用户确认「按推荐来 + 热更新 + 保证可回退」后落地了第 1 批（风险最低、收益最大）：

| 改动 | 文件 | 效果 |
|------|------|------|
| **字体体系**（最大杠杆） | `webapp/tailwind.config.js` | 正文/界面 `font-sans` 由 Times+宋体 → **无衬线**（Inter/Segoe UI/system-ui + 微软雅黑/PingFang）；新增 `font-display` 保留衬线栈 |
| 标题学术签名 | `index.css`（`main h1`） | 页面主标题仍用衬线 + `letter-spacing:-0.015em`，保持学术气质 |
| **表面分层** | `components/ui/card.tsx`、`dialog.tsx`、`index.css` | 卡片改 `rounded-2xl border-border/70 shadow-card`（更柔、更少「边框感」）；弹窗由 `bg-background` → `bg-card` + `shadow-pop` 形成浮层层次；菜单/列表浮层用卡片底色 |
| 数字对齐 | `index.css` | 表格统一 `tabular-nums`（KPI/分数/数值列对齐更稳） |
| **栅格与节奏** | `components/layout/AppLayout.tsx` | 主内容 `max-w-6xl py-8` → `max-w-[1200px] py-7`（注：栅格原本已居中约束，此行仅为节奏微调） |

**验证**：`tsc -b` + `vite build` 通过；同后端同数据做 A/B 截图（基线 vs 新版，首页/我的/实验记录/设置），
再对**安装版**实拍确认（`E:\cof-build\installed-ui-overhaul\home.png`）。

**回退方式（你要的保险）**：
```powershell
cd C:\Users\ckx\Desktop\全新机器学习实验
.\scripts\rollback_ui.ps1 -List                     # 查看所有 UI 快照
.\scripts\rollback_ui.ps1                           # 回退到最近一个快照（默认 baseline 之前的？见下）
.\scripts\rollback_ui.ps1 -Snapshot baseline-20260912-195107   # 回退到「改动前」基线
```
- 快照位置：`E:\cof-build\ui-snapshots\`（`baseline-*` = 改动前原貌；`ui-overhaul1-*` = 第 1 批）
- 脚本会自动：关闭应用 → 备份当前资源（`pre-rollback-*`）→ 覆盖 dist → **校验 index.html 哈希** → 重启应用
- 整包回退：重装 `webapp\release\cof-film-recommend-setup-1.9.3.exe`（或 GitHub Release 任一历史版本）
- ⚠️ 该脚本以 **UTF-8 BOM** 保存（Windows PowerShell 5.1 读无 BOM 的 .ps1 会按 ANSI 解析、中文串报语法错）——已踩坑修正

**尚未做**（等你对第 1 批的反馈）：组件细节统一、首页仪表盘化（KPI + 迷你图）、动效与图标统一、
逐页排版打磨（第 2/3 批）。

---

## 一、诊断：为什么「不够高级」（基于代码与截图的事实）

| # | 成因 | 代码层面的事实 | 对「高级感」的伤害 |
|---|------|----------------|--------------------|
| 1 | **正文用衬线中文字体** | `tailwind.config.js` 的 `font-sans` = Times New Roman + SimSun（宋体）；界面元素另建了 `font-ui`（Segoe UI/雅黑）但只有侧栏在用 | 12–14px 宋体在 Windows 上以位图字形渲染、笔画发虚——这是「廉价感」最大来源 |
| 2 | **没有表面分层（surface elevation）** | 全站卡片几乎都是「白底 + 1px 边框 + 单一阴影」，缺少 canvas / panel / raised 三级层次 | 缺乏纵深，信息全在同一平面上「糊」在一起 |
| 3 | **节奏不统一** | padding/gap 在 `p-2/p-3/p-4`、`gap-1.5/gap-2/gap-3/gap-4` 之间随机 | 眼睛能察觉「没对齐」，但说不出哪里不对 |
| 4 | **栅格缺约束**（**已修正判断**） | 主内容区其实已有 `mx-auto max-w-6xl`；真正问题是**宽屏下卡片被拉宽**、缺少列宽节奏 | 大屏显得「空而不精」 |
| 5 | **几乎零动效** | 只有 hover 变色；没有进入/切换/展开的微动效 | 缺少「精致」的体感反馈 |
| 6 | **字体层级弱** | 标题/正文/说明的差异主要靠字号（16/14/12），字重与颜色层次用得少 | 视线没有落点，读起来平 |
| 7 | **数字不「仪表化」** | KPI 只是大号数字；没有单位弱化、趋势、迷你图、对比基准 | 数据页看起来像表格，不像仪表盘 |
| 8 | **图标语言不统一** | lucide 尺寸 14/16/20 混用，部分位置图标颜色随文字 | 细节不齐，累积成「不专业」 |

**关键判断**：1、2、4 三条是「高级感」的杠杆点（字体、表面分层、栅格）；
其余是打磨项。**不需要重写信息架构**也能有质变。

---

## 二、三条可选路线（都基于公开成熟的参考，可查可学）

### 路线 A：瑞士网格 · 学术克制（**我的推荐骨架**）
**参考**：[shadcn/ui](https://ui.shadcn.com/docs)（我们已在用它的组件形态）、
[Radix Themes](https://www.radix-ui.com/)（可访问性 + 主题令牌）、
[Vercel Geist 设计原则](https://github.com/VoltAgent/awesome-design-md/blob/main/design-md/vercel/DESIGN.md)（明确提出「不要用 border 做容器，用背景分层」）、
[Carbon Design System](https://carbondesignsystem.com/)（IBM 的密度与排版规范，信息密集型 UI 的标杆）。

**视觉语言**
- 字体：正文/界面改**无衬线**（英文 Inter，中文 微软雅黑/PingFang），数字 `tabular-nums`；
  **标题可选保留衬线**（Times/宋体）作为「学术签名」——这点很关键，能兼顾专业感与现代感
- 表面分层：`canvas`（页面底）/ `panel`（卡片）/ `raised`（浮层），**优先用背景差而不是边框**；边框只保留 1px `border/60`
- 栅格与节奏：主内容 `max-w-[1200px]` 居中 + 12 栏；间距统一 8pt 阶梯（4/8/12/16/24/32）
- 动效：150ms 淡入 + 上移 2px；hover 用亮度/描边而非阴影；折叠用高度过渡
- 密度：默认**紧凑**（数据密集场景），设置页保留舒适档（已实现）

**改动范围**：令牌 + 基础组件 + 逐页排版（信息架构不动）
**风险**：低（我们已有令牌体系，主要是「用对 + 收紧」）　**工时**：约 10–14h

---

### 路线 B：仪器感 · 深色专业（**可作为可选皮肤/首页点缀**）
**参考**：[Tremor](https://github.com/tremorlabs/tremor)（仪表盘组件：KPI 卡、迷你图、进度条）、
[Linear 的新视觉（Liquid Glass）](https://linear.app/now/linear-liquid-glass)（玻璃拟态 + 细高光边）、
[Carbon](https://carbondesignsystem.com/) 的数据可视化规范。

**视觉语言**
- 默认**深色**（石墨底 + 低饱和强调），面板用半透明 + `backdrop-blur` + 1px 顶部高光
- KPI 仪表化：大号数字 + 单位弱化 + 迷你趋势图 + 环比
- 图表配色统一到主题令牌，网格线极淡

**改动范围**：主题令牌（深色优先）+ 首页/数据页重构为仪表盘
**风险**：中等——实验室白天使用深色未必舒适；玻璃拟态在低端机上有性能开销
**工时**：约 14–20h

---

### 路线 C：温润学术工作台（**保留现有暖纸+松石，做「高级化」**）
**参考**：[Origin UI](https://21st.dev/@originui/library/origin-ui) 等现代组件集合的柔和细节、
Raycast/Arc 的「大圆角 + 柔和阴影 + 精选字体」处理、
Editorial 排版（衬线标题 + 无衬线正文混排）。

**视觉语言**
- 保留暖纸底与松石/赭石主色（现有主题资产不浪费）
- 正文改无衬线、标题保留衬线；卡片圆角加大到 16–20px、阴影更柔、边框更淡
- 页面标题区统一为「标题 + 一句话副标题 + 右侧主行动」
- 导航加图标语言与分组小标题

**改动范围**：令牌微调 + 组件圆角/阴影 + 逐页标题区
**风险**：低　**工时**：约 8–12h

---

## 三、我的推荐（供讨论）

**A 为骨架 + C 的温度 + B 的点缀**：

1. **先做「杠杆三件套」**（风险最低、收益最大）：
   ① 字体体系（正文无衬线 + 标题衬线签名 + 数字等宽）；
   ② 表面分层（canvas/panel/raised，减少边框依赖）；
   ③ 栅格与 8pt 节奏（主内容 max-width + 统一间距）。
2. **再打磨组件细节**（按钮/徽章/表格/空态/骨架屏统一）。
3. **首页仪表盘化**（借 B 的 KPI 卡 + 迷你图思路，但用浅色主题实现）。
4. **最后**才是动效与图标统一。

**为什么不建议直接上 B（深色仪器风）**：它会推翻现有 6 套主题的调校成果，且实验室白天的
可读性存疑；但可以把它的「数据仪表化」思想移植到浅色主题里，性价比更高。

---

## 四、需要你拍板（讨论点）

1. **字体**：正文是否同意改无衬线（我强烈建议改）？标题是否保留衬线（Times/宋体）作为学术签名？
2. **基调**：骨架走 A（瑞士网格·克制）还是 C（温润学术）？还是你想要 B（深色仪器，默认深色）？
3. **默认密度**：紧凑（推荐，数据密集）还是舒适？
4. **改动边界**：接受「视觉 + 布局层」改动（含首页仪表盘化、标题区重排），还是要严格只动视觉？
   —— 信息架构（菜单项/字段/流程）我建议**不动**。
5. **验证方式**：要不要我先做**一个样板页**（建议「首页」或「查询打分页」）做成可对比的两版截图给你看，
   你选定后再全站推进？这样风险最小（可随时回退）。

---

## 五、参考链接（便于你自行查看）

- shadcn/ui 文档：https://ui.shadcn.com/docs
- Radix Primitives / Themes：https://www.radix-ui.com/
- Vercel 设计原则（DESIGN.md）：https://github.com/VoltAgent/awesome-design-md/blob/main/design-md/vercel/DESIGN.md
- Carbon Design System（IBM）：https://carbondesignsystem.com/
- Tremor（仪表盘组件）：https://github.com/tremorlabs/tremor
- Linear 新视觉（玻璃拟态）：https://linear.app/now/linear-liquid-glass
- Origin UI（现代组件集合）：https://21st.dev/@originui/library/origin-ui
- 2025 React 设计系统横向对比（选型参考）：https://inwald.com/2025/11/modern-design-systems-for-react-in-2025-a-pragmatic-comparison/
