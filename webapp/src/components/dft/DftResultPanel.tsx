/**
 * DFT 结果展示面板（2.0）：缩合二聚体 D 与第三物质 X 的结合能；
 * pair 模式（任意双分子）文案适配为分子 A···B 直接结合。
 * 结合能大数字卡 + X 描述 + 二聚体 SMILES 可复制 + 组分描述符表
 * + 结构图（二聚体 / X）+ 3D 结合构象（3Dmol.js，懒加载折叠区块）
 * + 复合物 xyz 下载 + 量化软件输入文件导出（Gaussian .gjf / ORCA .inp）。
 * 固定红线提示（学术诚信底线）常驻底部；多位点单体常驻「示意单点缩合」标注。
 */
import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ChevronDown, Copy, FileDown, FileOutput } from 'lucide-react';
import { toast } from 'sonner';
import { exportDftInput, type DftBackend, type DftExportFormat, type DftResult } from './api';
import DftViewer3D from './DftViewer3D';

interface Props {
  result: DftResult;
  /** 当前任务 id（历史回显无任务时为 null，导出时自动借缓存命中任务） */
  jobId?: string | null;
  /** 同组合另一后端的最近一次结果（精度档/快速档互相对比；无则 null） */
  compare?: {
    backend: DftBackend;
    method_label: string;
    e_bind_kcal: number;
    e_bind_kj: number;
  } | null;
}

function fmt(v: number | null | undefined, digits = 2, unit = ''): string {
  return v == null ? '—' : `${v.toFixed(digits)}${unit}`;
}

/** 下载复合物优化后几何（xyz）；有任务 id 时走 geometry 端点，否则本地 blob */
function downloadXyz(result: DftResult, jobId?: string | null) {
  if (jobId) {
    const a = document.createElement('a');
    a.href = `/api/dft/jobs/${encodeURIComponent(jobId)}/geometry`;
    a.download = `dft_complex_${result.method}.xyz`;
    a.click();
    return;
  }
  const blob = new Blob([result.complex_xyz], { type: 'chemical/x-xyz' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `dft_complex_${result.method}.xyz`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function DftResultPanel({ result, jobId, compare }: Props) {
  const bound = result.e_bind_kcal < 0;
  const isPair = result.mode === 'pair';
  const isPsi4 = result.backend === 'psi4';
  /** 导出进行中（按格式记，防重复点击） */
  const [exporting, setExporting] = useState<DftExportFormat | null>(null);

  /** 复制文本（二聚体 / 分子 SMILES） */
  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label}已复制`);
    } catch {
      toast.warning('复制失败，请手动选择文本复制');
    }
  };

  /** 导出量化软件输入文件（无可用 xyz 时拦截） */
  const handleExport = async (format: DftExportFormat) => {
    if (!result.complex_xyz?.trim()) {
      toast.warning('该结果不含复合物几何，无法导出输入文件');
      return;
    }
    setExporting(format);
    try {
      await exportDftInput(result, format, jobId);
      toast.success(format === 'gaussian'
        ? '已导出 Gaussian 输入（.gjf），提交前请检查电荷与自旋多重度'
        : '已导出 ORCA 输入（.inp），提交前请检查电荷与自旋多重度');
    } catch {
      // 错误提示已由 api 层弹出
    } finally {
      setExporting(null);
    }
  };
  const rows: { label: string; d: string; x: string; c: string }[] = [
    {
      label: '总能量 (Eh)',
      d: fmt(result.energies_hartree.dimer, 6),
      x: fmt(result.energies_hartree.x, 6),
      c: fmt(result.energies_hartree.complex, 6),
    },
    {
      label: 'HOMO-LUMO 能隙 (eV)',
      d: fmt(result.gap_ev.dimer),
      x: fmt(result.gap_ev.x),
      c: fmt(result.gap_ev.complex),
    },
    {
      label: '偶极矩 (Debye)',
      d: fmt(result.dipole_debye.dimer),
      x: fmt(result.dipole_debye.x),
      c: fmt(result.dipole_debye.complex),
    },
  ];

  return (
    <div className="space-y-4">
      {/* 结合能大数字卡 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            {isPair ? '双分子结合能' : '二聚体结合能'}
            <Badge variant="outline">{result.method_label}</Badge>
            {isPsi4 ? (
              <Badge className="border-gold bg-gold-muted/60 text-amber-800 dark:text-gold">
                Psi4 精度档
              </Badge>
            ) : (
              <Badge variant="secondary">xTB 快速档</Badge>
            )}
            {isPsi4 && result.psi4_detail?.bsse_type === 'cp' && (
              <Badge variant="outline" title="counterpoise 基组重叠误差（BSSE）校正">
                BSSE 校正
              </Badge>
            )}
            {isPair && <Badge variant="secondary">任意双分子模式</Badge>}
            {result.cached && <Badge variant="secondary">缓存结果</Badge>}
            {!isPair && result.dimer_multi_site && (
              <Badge variant="outline" className="border-amber-400 text-amber-700 dark:text-amber-400">
                示意单点缩合
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
            <div>
              {/* kJ/mol 主显示，kcal/mol 次要显示 */}
              <span className={`text-4xl font-bold tabular-nums ${bound ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
                {result.e_bind_kj.toFixed(2)}
              </span>
              <span className="ml-1 text-sm text-muted-foreground">kJ/mol</span>
            </div>
            <div className="text-lg tabular-nums text-muted-foreground">
              （{result.e_bind_kcal.toFixed(2)}
              <span className="ml-1 text-sm">kcal/mol）</span>
            </div>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            {isPair
              ? 'E(结合) = E(A···B 复合物) − E(A) − E(B)，两分子任意选取、不经过缩合反应。'
              : `E(结合) = E(二聚体·X 复合物) − E(二聚体) − E(X)，其中 X = ${result.x_description}。`}
            {isPsi4 && (
              <span className="block mt-1">
                方法/基组：{result.psi4_detail?.method ?? 'wb97x-d3bj'} /{' '}
                {result.psi4_detail?.basis ?? 'def2-svp'}，结合能经 counterpoise（BSSE）校正
                {result.psi4_detail?.psi4_version ? `；Psi4 ${result.psi4_detail.psi4_version}` : ''}。
                {result.psi4_detail?.e_bind_raw_kcal != null && (
                  <>未校正参考值：{result.psi4_detail.e_bind_raw_kcal.toFixed(2)} kcal/mol。</>
                )}
                {result.psi4_detail?.fchk_available && (
                  <>fchk 检查点文件已生成（可对接 Gaussian 工作流）。</>
                )}
              </span>
            )}
            {compare && (
              <span className="block mt-1">
                对比｜同组合{compare.backend === 'psi4' ? ' Psi4 精度档' : ' xTB 快速档'}（{compare.method_label}）：
                {compare.e_bind_kj.toFixed(2)} kJ/mol（{compare.e_bind_kcal.toFixed(2)} kcal/mol）。
              </span>
            )}
            {bound
              ? '负值：形成复合物在能量上有利，数值越负结合越强。'
              : '正值：该初猜取向下复合物能量高于组分之和，结合不利（或构象未找到有利取向）。'}
            耗时 {result.elapsed_sec.toFixed(1)} s。
          </p>
        </CardContent>
      </Card>

      {/* 二聚体 SMILES（可复制）；pair 模式展示分子 A/B */}
      {isPair ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">双分子（A···B 直接结合）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {[
              { label: '分子 A', smiles: result.smiles_a },
              { label: '分子 B', smiles: result.smiles_b },
            ].map((m) => (
              <div key={m.label} className="flex items-center gap-2">
                <span className="w-14 shrink-0 text-xs text-muted-foreground">{m.label}</span>
                <code className="flex-1 break-all rounded border bg-muted/50 px-2 py-1.5 font-mono text-xs">
                  {m.smiles}
                </code>
                <Button variant="outline" size="sm" onClick={() => void copyText(m.smiles, `${m.label} SMILES `)}>
                  <Copy className="mr-1 h-4 w-4" />
                  复制
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">缩合二聚体（亚胺键 C=N）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded border bg-muted/50 px-2 py-1.5 font-mono text-xs">
                {result.dimer_smiles}
              </code>
              <Button
                variant="outline"
                size="sm"
                onClick={() => result.dimer_smiles && void copyText(result.dimer_smiles, '二聚体 SMILES ')}
              >
                <Copy className="mr-1 h-4 w-4" />
                复制
              </Button>
            </div>
            {result.dimer_multi_site && result.dimer_note && (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                ⚠️ {result.dimer_note}：多位点单体的真实产物可能多位点缩合或形成寡聚体，
                本结果为示意性单点缩合二聚体。
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* 组分描述符表 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">量化描述符</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-1.5 pr-2 font-medium">指标</th>
                <th className="py-1.5 pr-2 font-medium">{isPair ? '分子 A' : '二聚体'}</th>
                <th className="py-1.5 pr-2 font-medium">{isPair ? '分子 B' : 'X'}</th>
                <th className="py-1.5 font-medium">复合物</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label} className="border-b last:border-0">
                  <td className="py-1.5 pr-2">{r.label}</td>
                  <td className="py-1.5 pr-2 tabular-nums">{r.d}</td>
                  <td className="py-1.5 pr-2 tabular-nums">{r.x}</td>
                  <td className="py-1.5 tabular-nums">{r.c}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.method === 'gfnff' && (
            <p className="mt-2 text-xs text-muted-foreground">
              GFN-FF 为力场方法，无轨道信息，能隙不可用（—）。
            </p>
          )}
        </CardContent>
      </Card>

      {/* 结构图：2D 结构 + 3D 结合构象 + 复合物 xyz 下载 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">化学结构</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {(isPair
              ? [
                { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(result.smiles_a)}`, label: '分子 A' },
                { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(result.smiles_b)}`, label: '分子 B' },
              ]
              : [
                { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(result.dimer_smiles ?? '')}`, label: '缩合二聚体' },
                { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(result.x_smiles)}`, label: `X（${result.x_description}）` },
              ]
            ).map((im) => (
              <figure key={im.label} className="text-center">
                <img
                  src={im.src}
                  alt={im.label}
                  className="mx-auto max-h-44 rounded border bg-white object-contain p-1"
                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                />
                <figcaption className="mt-1 text-xs text-muted-foreground">{im.label}</figcaption>
              </figure>
            ))}
          </div>

          {/* 3D 结合构象（懒加载折叠区块，按片段区间双色渲染） */}
          {result.complex_xyz?.trim() && (
            <DftViewer3D
              xyz={result.complex_xyz}
              fragmentRanges={result.fragment_ranges}
              labelA={isPair ? '分子 A' : '二聚体'}
              labelB={isPair ? '分子 B' : 'X'}
            />
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => downloadXyz(result, jobId)}>
              <FileDown className="mr-1 h-4 w-4" />
              下载复合物优化后 3D 几何（.xyz）
            </Button>
            {/* 导出量化软件输入文件（Gaussian / ORCA，后端生成） */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" disabled={exporting !== null}>
                  <FileOutput className="mr-1 h-4 w-4" />
                  {exporting ? '导出中…' : '导出量化输入文件'}
                  <ChevronDown className="ml-1 h-3.5 w-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem onClick={() => void handleExport('gaussian')}>
                  Gaussian 输入（.gjf，b3lyp/6-31g(d) scrf=smd）
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => void handleExport('orca')}>
                  ORCA 输入（.inp，B3LYP def2-SVP OPT）
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <span className="text-xs text-muted-foreground">
              可用 Avogadro / VMD / GaussView 打开查看，或导出后作为高精度 DFT 复算的输入；
              导出的输入文件默认电荷 0、自旋多重度 1，提交前请自行检查。
            </span>
          </div>
        </CardContent>
      </Card>

      {/* 固定红线提示（学术诚信底线，常驻） */}
      <p className="rounded-lg border border-dashed border-gold/50 bg-gold-muted/40 px-4 py-2 text-xs text-muted-foreground">
        {isPsi4
          ? '学术诚信提示：Psi4 精度档为团簇模型（二聚体·客体）真 DFT 结果，已做 BSSE 校正；周期性框架级吸附请导出几何/输入文件到超算复算。'
          : '学术诚信提示：半经验方法（xTB）结果仅供相对比较，精确能量请切换到 Psi4 精度档或导出输入文件用 DFT 复算。'}
      </p>
    </div>
  );
}
