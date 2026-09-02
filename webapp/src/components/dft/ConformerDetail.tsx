/**
 * 单个低能构象详情（v1.5.1）：能量/玻尔兹曼占比 + 3D 构象（复用 DftViewer3D）
 * + XYZ 坐标文本（可复制/下载 .xyz，纯前端 Blob，无需后端接口）。
 */
import { useState } from 'react';
import { Check, Copy, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import DftViewer3D from './DftViewer3D';
import type { ConformerItem, DftFragmentRanges } from './api';

interface Props {
  item: ConformerItem;
  /** 复合物片段区间（有则 3D 两色渲染分子 A/B） */
  fragmentRanges?: DftFragmentRanges | null;
  labelA?: string;
  labelB?: string;
}

export default function ConformerDetail({ item, fragmentRanges, labelA = '分子 A', labelB = '分子 B' }: Props) {
  const [copied, setCopied] = useState(false);

  const copyXyz = async () => {
    try {
      await navigator.clipboard.writeText(item.xyz);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板受限（非 https/未授权）时静默，用户仍可手动选择文本
    }
  };

  const downloadXyz = () => {
    const blob = new Blob([item.xyz], { type: 'chemical/x-xyz;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${item.id}.xyz`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const atomCount = item.xyz.trim().split('\n')[0]?.trim() || '?';

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>
          <span className="font-medium tabular-nums">{item.id}</span>
          <span className="ml-2 tabular-nums text-muted-foreground">
            ΔE {item.rel_e_kj.toFixed(3)} kJ/mol（{item.rel_e_kcal.toFixed(3)} kcal/mol）·
            玻尔兹曼占比 {(item.boltzmann_w * 100).toFixed(1)}%
          </span>
        </span>
        <span className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => void copyXyz()}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? '已复制' : '复制 XYZ'}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={downloadXyz}
          >
            <Download className="h-3.5 w-3.5" />
            下载 .xyz
          </Button>
        </span>
      </div>
      <DftViewer3D
        xyz={item.xyz}
        fragmentRanges={fragmentRanges ?? null}
        labelA={labelA}
        labelB={labelB}
        title={fragmentRanges ? '3D 复合物构象（青=分子 A · 品红=分子 B）' : '3D 构象（拖动旋转 · 滚轮缩放）'}
      />
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          XYZ 坐标（{atomCount} 个原子）
        </p>
        <pre className="max-h-64 overflow-auto rounded border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">
          {item.xyz}
        </pre>
      </div>
    </div>
  );
}
