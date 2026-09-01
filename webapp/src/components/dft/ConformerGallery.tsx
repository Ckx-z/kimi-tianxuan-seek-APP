/**
 * 低能构象检索画廊（v1.5.0）：自动检索客体构象（ETKDG/CREST/auto），
 * 能量排序列表 + 玻尔兹曼占比；选用某构象后经 manualConformer(b_xyz=选中项)
 * 合成复合物几何，回调给页面在提交时注入 complex_xyz。
 */
import { useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  generateConformers,
  manualConformer,
  type ConformerItem,
} from './api';

type ConformerEngine = 'auto' | 'etkdg' | 'crest';

interface Props {
  /** 主体 SMILES（pair：分子 A；dimer：缩合二聚体预览结果） */
  aSmiles: string;
  /** 客体 SMILES（pair：分子 B；dimer：X 物质） */
  bSmiles: string;
  disabled?: boolean;
  /** 选用构象并合成复合物几何后回调（null=清除选用） */
  onApply: (xyz: string | null) => void;
}

export default function ConformerGallery({ aSmiles, bSmiles, disabled, onApply }: Props) {
  const [engine, setEngine] = useState<ConformerEngine>('auto');
  const [conformers, setConformers] = useState<ConformerItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const runGenerate = async () => {
    if (!bSmiles) {
      toast.warning('请先填写客体（X / 分子 B）的 SMILES');
      return;
    }
    setLoading(true);
    setConformers([]);
    setSelected(null);
    onApply(null);
    try {
      const res = await generateConformers({ smiles: bSmiles, engine });
      if (res.conformers.length === 0) {
        toast.warning('未检索到低能构象（分子过小或引擎不可用）');
        return;
      }
      setConformers(res.conformers);
      if (!res.cached) {
        toast.success(`已生成 ${res.conformers.length} 个低能构象（引擎：${res.engine}）`);
      }
    } catch {
      // toast 已在 api 辅助弹出
    } finally {
      setLoading(false);
    }
  };

  const applySelected = async (item: ConformerItem) => {
    if (!aSmiles) {
      toast.warning('请先完成主体（二聚体预览 / 分子 A）输入');
      return;
    }
    setApplying(true);
    try {
      const res = await manualConformer({
        a_smiles: aSmiles,
        b_smiles: bSmiles,
        tx: 0, ty: 0, tz: 0, rx_deg: 0, ry_deg: 0, rz_deg: 0,
        b_xyz: item.xyz,
      });
      setSelected(item.id);
      onApply(res.xyz);
      toast.success(`已选用构象 ${item.id}（ΔE=${item.rel_e_kj.toFixed(2)} kJ/mol）`);
    } catch {
      // toast 已弹
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">构象引擎</Label>
          <RadioGroup
            value={engine}
            onValueChange={(v) => setEngine(v as ConformerEngine)}
            className="flex gap-3"
          >
            {(['auto', 'etkdg', 'crest'] as const).map((e) => (
              <span key={e} className="flex items-center gap-1.5">
                <RadioGroupItem value={e} id={`conf-eng-${e}`} />
                <Label htmlFor={`conf-eng-${e}`} className="text-xs">
                  {e === 'auto' ? '自动' : e === 'etkdg' ? 'ETKDG' : 'CREST'}
                </Label>
              </span>
            ))}
          </RadioGroup>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void runGenerate()}
          disabled={loading || disabled || !bSmiles}
        >
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          检索低能构象
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        能量窗口默认 ΔE ≤ 10 kJ/mol、最多 20 个；CREST 需已安装（自动模式在未安装时回落 ETKDG）。
      </p>
      {conformers.length > 0 && (
        <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
          {conformers.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                disabled={applying || disabled}
                onClick={() => void applySelected(c)}
                className={`w-full rounded px-2 py-1.5 text-left transition-colors ${
                  selected === c.id
                    ? 'bg-gold-muted font-medium text-gold-foreground'
                    : 'hover:bg-accent'
                }`}
                title="选用此构象并合成复合物几何（提交时按此几何计算）"
              >
                <span className="font-medium tabular-nums">{c.id}</span>
                <span className="ml-2 tabular-nums text-muted-foreground">
                  ΔE {c.rel_e_kj.toFixed(2)} kJ/mol · 占比 {(c.boltzmann_w * 100).toFixed(1)}%
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
