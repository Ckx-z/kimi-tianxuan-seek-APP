/**
 * DFT 结果展示面板：结合能大数字卡 + 组分描述符表 + 结构图 + xyz 下载
 * + 量化软件输入文件导出（Gaussian .gjf / ORCA .inp，后端生成下载）。
 * 固定红线提示（学术诚信底线）常驻底部。
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
import { ChevronDown, FileDown, FileOutput } from 'lucide-react';
import { toast } from 'sonner';
import { exportDftInput, type DftExportFormat, type DftResult } from './api';

interface Props {
  result: DftResult;
  smilesA: string;
  smilesB: string;
  /** 当前任务 id（历史回显无任务时为 null，导出时自动借缓存命中任务） */
  jobId?: string | null;
}

function fmt(v: number | null | undefined, digits = 2, unit = ''): string {
  return v == null ? '—' : `${v.toFixed(digits)}${unit}`;
}

/** 下载复合物优化后几何（xyz） */
function downloadXyz(result: DftResult) {
  const blob = new Blob([result.complex_xyz], { type: 'chemical/x-xyz' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `dft_complex_${result.method}.xyz`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function DftResultPanel({ result, smilesA, smilesB, jobId }: Props) {
  const bound = result.e_bind_kcal < 0;
  /** 导出进行中（按格式记，防重复点击） */
  const [exporting, setExporting] = useState<DftExportFormat | null>(null);

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
  const rows: { label: string; a: string; b: string; c: string }[] = [
    {
      label: '总能量 (Eh)',
      a: fmt(result.energies_hartree.a, 6),
      b: fmt(result.energies_hartree.b, 6),
      c: fmt(result.energies_hartree.complex, 6),
    },
    {
      label: 'HOMO-LUMO 能隙 (eV)',
      a: fmt(result.gap_ev.a),
      b: fmt(result.gap_ev.b),
      c: fmt(result.gap_ev.complex),
    },
    {
      label: '偶极矩 (Debye)',
      a: fmt(result.dipole_debye.a),
      b: fmt(result.dipole_debye.b),
      c: fmt(result.dipole_debye.complex),
    },
  ];

  return (
    <div className="space-y-4">
      {/* 结合能大数字卡 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            单体间结合能
            <Badge variant="outline">{result.method_label}</Badge>
            {result.cached && <Badge variant="secondary">缓存结果</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
            <div>
              <span className={`text-4xl font-bold tabular-nums ${bound ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
                {result.e_bind_kcal.toFixed(2)}
              </span>
              <span className="ml-1 text-sm text-muted-foreground">kcal/mol</span>
            </div>
            <div className="text-lg tabular-nums text-muted-foreground">
              {result.e_bind_kj.toFixed(2)}
              <span className="ml-1 text-sm">kJ/mol</span>
            </div>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            {bound
              ? '负值：两单体形成复合物在能量上有利，数值越负结合越强。'
              : '正值：该初猜取向下复合物能量高于单体之和，结合不利（或构象未找到有利取向）。'}
            耗时 {result.elapsed_sec.toFixed(1)} s。
          </p>
        </CardContent>
      </Card>

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
                <th className="py-1.5 pr-2 font-medium">单体 A</th>
                <th className="py-1.5 pr-2 font-medium">单体 B</th>
                <th className="py-1.5 font-medium">复合物</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label} className="border-b last:border-0">
                  <td className="py-1.5 pr-2">{r.label}</td>
                  <td className="py-1.5 pr-2 tabular-nums">{r.a}</td>
                  <td className="py-1.5 pr-2 tabular-nums">{r.b}</td>
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

      {/* 结构图：单体 2D + 复合物示意 + 3D xyz 下载 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">化学结构</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {[
              { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(smilesA)}`, label: '单体 A' },
              { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(smilesB)}`, label: '单体 B' },
              {
                src: `/api/monomers/dimer.svg?ald=${encodeURIComponent(smilesA)}&amine=${encodeURIComponent(smilesB)}`,
                label: '缩合产物（示意）',
              },
            ].map((im) => (
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
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => downloadXyz(result)}>
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
        学术诚信提示：半经验方法（xTB）结果仅供相对比较，精确能量请导出输入文件用 DFT 复算。
      </p>
    </div>
  );
}
