/**
 * 复合物低能构象检索画廊（v1.5.2 重构）：
 * 对「分子 A + 分子 B 的相对位置与取向」做构象采样，输出 A+B 组合体
 * 低能构象（含 fragment_ranges），选用后直接作为 complex_xyz 注入计算。
 *
 * B 内部柔性引擎：auto（CREST 可用优先，否则 ETKDG）| etkdg | crest | rigid
 * （rigid=跳过内部采样，纯相对位姿，最快）；每个内部构象采样 n_poses 个
 * 相对位姿（随机取向 + 不重叠摆放 + UFF 预优化），再经 xTB 分级排名。
 * 每个复合物构象可查看 3D（青=分子 A / 品红=分子 B）与 XYZ 坐标。
 */
import { useEffect, useState } from 'react';
import { Eye, Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import ConformerDetail from './ConformerDetail';
import {
  fetchConformerEngines,
  generateComplexConformers,
  type ComplexConformerItem,
  type ConformerEnginesResponse,
} from './api';

type ConformerEngine = 'auto' | 'etkdg' | 'crest' | 'rigid';

interface Props {
  /** 主体 SMILES（pair：分子 A；dimer：缩合二聚体预览结果） */
  aSmiles: string;
  /** 客体 SMILES（pair：分子 B；dimer：X 物质） */
  bSmiles: string;
  disabled?: boolean;
  /** 选用复合物构象后回调（直接注入 complex_xyz） */
  onApply: (xyz: string | null) => void;
}

export default function ConformerGallery({ aSmiles, bSmiles, disabled, onApply }: Props) {
  const [engine, setEngine] = useState<ConformerEngine>('auto');
  const [complexes, setComplexes] = useState<ComplexConformerItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  /** 详情弹层中的复合物构象 */
  const [detailItem, setDetailItem] = useState<ComplexConformerItem | null>(null);
  /** CREST 并行线程（空=按分子大小自动 4–24） */
  const [crestThreads, setCrestThreads] = useState('');
  /** 相对位姿采样数（每个 B 内部构象） */
  const [nPoses, setNPoses] = useState(8);
  /** 引擎可用性（CREST 未安装时的细分提示） */
  const [enginesInfo, setEnginesInfo] = useState<ConformerEnginesResponse['engines'] | null>(null);

  useEffect(() => {
    fetchConformerEngines()
      .then((r) => setEnginesInfo(r.engines))
      .catch(() => setEnginesInfo(null));
  }, []);

  const crestHint = enginesInfo?.crest?.installed === false
    ? enginesInfo.crest.install_hint : null;

  const runGenerate = async () => {
    if (!aSmiles || !bSmiles) {
      toast.warning('请先填写分子 A 与分子 B（客体）的 SMILES');
      return;
    }
    setLoading(true);
    setComplexes([]);
    setSelected(null);
    onApply(null);
    try {
      const res = await generateComplexConformers({
        a_smiles: aSmiles,
        b_smiles: bSmiles,
        engine,
        n_poses: nPoses,
        max_confs: 8,
        threads: engine === 'crest' && crestThreads ? Number(crestThreads) : undefined,
      });
      if (res.complexes.length === 0) {
        toast.warning('未采样到低能复合物构象');
        return;
      }
      setComplexes(res.complexes);
      if (!res.cached) {
        toast.success(`已采样 ${res.complexes.length} 个 A+B 复合物低能构象`);
      }
    } catch {
      // toast 已在 api 辅助弹出
    } finally {
      setLoading(false);
    }
  };

  const applySelected = (item: ComplexConformerItem) => {
    setSelected(item.id);
    onApply(item.xyz);
    toast.success(`已选用复合物构象 ${item.id}（ΔE=${item.rel_e_kj.toFixed(2)} kJ/mol），提交时按此几何计算`);
  };

  const atomCount = (item: ComplexConformerItem) =>
    item.xyz.trim().split('\n')[0]?.trim() || '?';

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">B 内部构象引擎</Label>
          <RadioGroup
            value={engine}
            onValueChange={(v) => setEngine(v as ConformerEngine)}
            className="flex flex-wrap gap-3"
          >
            {(['auto', 'etkdg', 'crest', 'rigid'] as const).map((e) => (
              <span key={e} className="flex items-center gap-1.5">
                <RadioGroupItem value={e} id={`conf-eng-${e}`} />
                <Label htmlFor={`conf-eng-${e}`} className="text-xs">
                  {e === 'auto' ? '自动' : e === 'etkdg' ? 'ETKDG' : e === 'crest' ? 'CREST' : '刚性（最快）'}
                </Label>
              </span>
            ))}
          </RadioGroup>
        </div>
        <div className="space-y-1">
          <Label htmlFor="complex-poses" className="text-xs text-muted-foreground">相对位姿数</Label>
          <Input
            id="complex-poses"
            type="number"
            min={1}
            max={24}
            value={nPoses}
            disabled={loading || disabled}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v)) setNPoses(Math.min(24, Math.max(1, Math.round(v))));
            }}
            className="h-8 w-16"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="crest-threads" className="text-xs text-muted-foreground">CREST 线程</Label>
          <Input
            id="crest-threads"
            type="number"
            min={1}
            max={64}
            placeholder="自动"
            value={crestThreads}
            disabled={loading || disabled}
            onChange={(e) => {
              const v = e.target.value.trim();
              if (v === '') { setCrestThreads(''); return; }
              const n = Number(v);
              if (Number.isFinite(n)) setCrestThreads(String(Math.min(64, Math.max(1, Math.round(n)))));
            }}
            className="h-8 w-20"
          />
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void runGenerate()}
          disabled={loading || disabled || !aSmiles || !bSmiles}
        >
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          采样复合物构象
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        对 A+B 的相对位置与取向采样（表面接触位姿 + 随机取向，top 候选经 gfn2 色散校正
        松弛）；输出为包含两分子的复合物 3D 坐标。小体系数十秒，大体系数分钟。
      </p>
      {crestHint && engine !== 'rigid' && engine !== 'etkdg' && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          ⚠ CREST 不可用：{crestHint}（自动模式将回落 ETKDG）
        </p>
      )}
      {complexes.length > 0 && (
        <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
          {complexes.map((c) => (
            <li key={c.id} className="flex items-center gap-1">
              <button
                type="button"
                disabled={disabled}
                onClick={() => applySelected(c)}
                className={`flex-1 rounded px-2 py-1.5 text-left transition-colors ${
                  selected === c.id
                    ? 'bg-gold-muted font-medium text-gold-foreground'
                    : 'hover:bg-accent'
                }`}
                title="选用此复合物构象（提交时按此几何计算）"
              >
                <span className="font-medium tabular-nums">{c.id}</span>
                <span className="ml-2 tabular-nums text-muted-foreground">
                  ΔE {c.rel_e_kj.toFixed(2)} kJ/mol · {atomCount(c)} 原子（A+B）
                </span>
              </button>
              <button
                type="button"
                title="查看 XYZ 坐标与 3D 复合物构象"
                onClick={() => setDetailItem(c)}
                className="shrink-0 rounded p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <Eye className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <Dialog open={detailItem !== null} onOpenChange={(v) => { if (!v) setDetailItem(null); }}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>复合物低能构象详情（A+B）</DialogTitle>
          </DialogHeader>
          {detailItem && (
            <ConformerDetail
              item={detailItem}
              fragmentRanges={detailItem.fragment_ranges}
              labelA="分子 A"
              labelB="分子 B"
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
