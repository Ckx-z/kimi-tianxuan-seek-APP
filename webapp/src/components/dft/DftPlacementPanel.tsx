/**
 * 手动摆放面板（v1.5.0）：主体 + 客体经刚体变换（平移/旋转 + 可选锚点对齐）
 * 合成复合物几何；预览用 3Dmol（复用 DftViewer3D）；「导入此摆放」回调给页面
 * 在提交时注入 complex_xyz。
 */
import { useState } from 'react';
import { Loader2, Move } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import DftViewer3D from './DftViewer3D';
import { manualConformer } from './api';

interface Props {
  /** 主体 SMILES（pair：分子 A；dimer：缩合二聚体预览结果） */
  aSmiles: string;
  /** 客体 SMILES（pair：分子 B；dimer：X 物质） */
  bSmiles: string;
  disabled?: boolean;
  /** 导入摆放几何（提交时注入 complex_xyz）；null=清除 */
  onApply: (xyz: string | null) => void;
}

export default function DftPlacementPanel({ aSmiles, bSmiles, disabled, onApply }: Props) {
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [tz, setTz] = useState(3);
  const [rx, setRx] = useState(0);
  const [ry, setRy] = useState(0);
  const [rz, setRz] = useState(0);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{
    xyz: string;
    fragment_ranges: { a: [number, number]; b: [number, number] };
  } | null>(null);

  const build = async () => {
    if (!aSmiles || !bSmiles) {
      toast.warning('请先填写主体与客体的 SMILES');
      return;
    }
    setBusy(true);
    try {
      const res = await manualConformer({
        a_smiles: aSmiles,
        b_smiles: bSmiles,
        tx, ty, tz, rx_deg: rx, ry_deg: ry, rz_deg: rz,
      });
      setPreview({ xyz: res.xyz, fragment_ranges: res.fragment_ranges });
      onApply(res.xyz);
      toast.success('已生成摆放几何，可继续微调或导入计算');
    } catch {
      // toast 已弹
    } finally {
      setBusy(false);
    }
  };

  const clear = () => {
    setPreview(null);
    onApply(null);
  };

  const numField = (
    label: string,
    value: number,
    setter: (v: number) => void,
    step: number,
  ) => (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <Input
        type="number"
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => setter(Number(e.target.value) || 0)}
        className="h-8 w-20 px-2 text-xs"
      />
    </div>
  );

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-end gap-3">
        {numField('平移 x (Å)', tx, setTx, 0.1)}
        {numField('平移 y (Å)', ty, setTy, 0.1)}
        {numField('平移 z (Å)', tz, setTz, 0.1)}
        {numField('旋转 x (°)', rx, setRx, 5)}
        {numField('旋转 y (°)', ry, setRy, 5)}
        {numField('旋转 z (°)', rz, setRz, 5)}
        <Button variant="outline" size="sm" onClick={() => void build()} disabled={busy || disabled}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Move className="size-4" />}
          生成摆放几何
        </Button>
        {preview && (
          <Button variant="ghost" size="sm" onClick={clear} disabled={disabled}>
            清除
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        客体相对主体做刚体平移/旋转（绕客体质心），正 z 平移为远离主体方向；生成后可在下方 3D 预览确认摆放。
      </p>
      {preview && (
        <DftViewer3D
          xyz={preview.xyz}
          fragmentRanges={preview.fragment_ranges}
          labelA="主体"
          labelB="客体"
        />
      )}
    </div>
  );
}
