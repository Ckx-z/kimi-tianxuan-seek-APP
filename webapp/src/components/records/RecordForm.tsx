/**
 * 实验记录录入表单（左栏 2/5）
 * - 关联收藏下拉 / 游离记录开关（开后显示醛/胺 SMILES 输入）
 * - 实验编号必填拦截；conditions 九键；结果三选；机械强度/操作人/备注
 * - 保存成功提示含「重复编号警告」（响应带 duplicate_experiment_no 时金色警示）
 */
import { useState } from 'react';
import { toast } from 'sonner';
import { Loader2, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { createRecord, type FavoriteItem } from './api';

/** conditions 九键（与后端契约一致） */
const CONDITION_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: 'solvent_1', label: '溶剂一', placeholder: '如 均三甲苯' },
  { key: 'solvent_2', label: '溶剂二', placeholder: '如 二氧六环' },
  { key: 'eluent', label: '洗脱剂', placeholder: '如 丙酮' },
  { key: 'modulator', label: '调制剂', placeholder: '如 苯胺' },
  { key: 'catalyst', label: '催化剂', placeholder: '如 6M 醋酸' },
  { key: 'temperature_c', label: '温度（℃）', placeholder: '如 120' },
  { key: 'time_days', label: '时间（天）', placeholder: '如 3' },
  { key: 'vessel', label: '容器', placeholder: '如 Pyrex 管' },
  { key: 'addition_order', label: '加料顺序', placeholder: '如 先醛后胺' },
];

/** 空表单初始值 */
const EMPTY_CONDITIONS: Record<string, string> = Object.fromEntries(
  CONDITION_FIELDS.map((f) => [f.key, '']),
);

export interface RecordFormProps {
  favorites: FavoriteItem[];
  /** 当前选中的收藏 id（受控，切换收藏需清空表单） */
  favoriteId: string;
  onFavoriteChange: (id: string) => void;
  /** 保存成功后的回调（用于刷新时间线） */
  onSaved: () => void;
}

/** 收藏下拉标签：醛名 + 胺名（缺名回退 SMILES 截断） */
function favoriteLabel(fav: FavoriteItem): string {
  const ald = fav.aldehyde?.name || fav.aldehyde?.smiles?.slice(0, 18) || '未知醛';
  const amine = fav.amine?.name || fav.amine?.smiles?.slice(0, 18) || '未知胺';
  return `${ald} + ${amine}`;
}

export default function RecordForm({ favorites, favoriteId, onFavoriteChange, onSaved }: RecordFormProps) {
  // 游离记录开关：开后不关联收藏，需手填醛/胺 SMILES
  const [freeMode, setFreeMode] = useState(false);
  const [aldehydeSmiles, setAldehydeSmiles] = useState('');
  const [amineSmiles, setAmineSmiles] = useState('');
  const [experimentNo, setExperimentNo] = useState('');
  const [conditions, setConditions] = useState<Record<string, string>>(EMPTY_CONDITIONS);
  const [outcome, setOutcome] = useState<'film' | 'partial' | 'failed'>('film');
  const [strength, setStrength] = useState('');
  const [operator, setOperator] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  /** 编号为空的前端拦截提示 */
  const [noError, setNoError] = useState(false);

  /** 清空表单（切换收藏或保存成功后调用） */
  const resetForm = () => {
    setExperimentNo('');
    setConditions(EMPTY_CONDITIONS);
    setOutcome('film');
    setStrength('');
    setOperator('');
    setNotes('');
    setNoError(false);
  };

  /** 切换收藏：清空表单 */
  const handleFavoriteChange = (id: string) => {
    onFavoriteChange(id);
    resetForm();
  };

  /** 切换游离模式：清空关联与 SMILES */
  const handleFreeModeChange = (on: boolean) => {
    setFreeMode(on);
    setAldehydeSmiles('');
    setAmineSmiles('');
    if (on) onFavoriteChange('');
    resetForm();
  };

  const setCond = (key: string, value: string) =>
    setConditions((prev) => ({ ...prev, [key]: value }));

  /** 提交保存 */
  const handleSubmit = async () => {
    // 实验编号必填拦截
    if (!experimentNo.trim()) {
      setNoError(true);
      toast.error('请填写实验编号（必填）');
      return;
    }
    if (!freeMode && !favoriteId) {
      toast.error('请选择关联收藏，或开启「游离记录」并填写醛/胺 SMILES');
      return;
    }
    if (freeMode && (!aldehydeSmiles.trim() || !amineSmiles.trim())) {
      toast.error('游离记录需填写醛单体与胺单体 SMILES');
      return;
    }
    setSaving(true);
    try {
      const rec = await createRecord({
        favorite_id: freeMode ? null : favoriteId,
        aldehyde_smiles: freeMode ? aldehydeSmiles.trim() : '',
        amine_smiles: freeMode ? amineSmiles.trim() : '',
        conditions,
        outcome,
        strength: strength.trim(),
        notes: notes.trim(),
        operator: operator.trim(),
        experiment_no: experimentNo.trim(),
      });
      // 重复编号警告：金色警示（后端不落盘拦截，仅提示）
      if (rec.duplicate_experiment_no) {
        toast.warning(`已保存，但注意：该收藏下实验编号「${rec.experiment_no}」已存在（重复编号警告）`, {
          duration: 8000,
        });
      } else {
        toast.success(`实验记录已保存（编号 ${rec.experiment_no}）`);
      }
      resetForm();
      onSaved();
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4 rounded-xl border border-border bg-card p-5">
      <h2 className="text-lg font-semibold text-foreground">录入实验记录</h2>

      {/* 游离记录开关 */}
      <div className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2">
        <Label htmlFor="free-mode" className="text-sm text-muted-foreground">
          游离记录（不关联收藏）
        </Label>
        <Switch id="free-mode" checked={freeMode} onCheckedChange={handleFreeModeChange} />
      </div>

      {/* 关联收藏下拉 / 游离 SMILES 输入 */}
      {!freeMode ? (
        <div className="space-y-1.5">
          <Label>关联收藏</Label>
          <Select value={favoriteId} onValueChange={handleFavoriteChange}>
            <SelectTrigger>
              <SelectValue placeholder="选择收藏的醛/胺单体对" />
            </SelectTrigger>
            <SelectContent>
              {favorites.length === 0 && (
                <div className="px-3 py-2 text-sm text-muted-foreground">暂无收藏，可开启游离记录</div>
              )}
              {favorites.map((fav) => (
                <SelectItem key={fav.id} value={fav.id}>
                  {favoriteLabel(fav)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          <div className="space-y-1.5">
            <Label>醛单体 SMILES</Label>
            <Input
              value={aldehydeSmiles}
              onChange={(e) => setAldehydeSmiles(e.target.value)}
              placeholder="如 O=CC=O"
            />
          </div>
          <div className="space-y-1.5">
            <Label>胺单体 SMILES</Label>
            <Input
              value={amineSmiles}
              onChange={(e) => setAmineSmiles(e.target.value)}
              placeholder="如 Nc1ccccc1"
            />
          </div>
        </div>
      )}

      {/* 实验编号（必填） */}
      <div className="space-y-1.5">
        <Label>
          实验编号 <span className="text-destructive">*</span>
        </Label>
        <Input
          value={experimentNo}
          onChange={(e) => {
            setExperimentNo(e.target.value);
            if (e.target.value.trim()) setNoError(false);
          }}
          placeholder="如 A5、G2-3"
          className={noError ? 'border-destructive' : ''}
        />
        {noError && <p className="text-xs text-destructive">实验编号为必填项</p>}
      </div>

      {/* 反应条件九键 */}
      <div className="space-y-2">
        <Label className="text-muted-foreground">反应条件</Label>
        <div className="grid grid-cols-2 gap-3">
          {CONDITION_FIELDS.map((f) => (
            <div key={f.key} className="space-y-1">
              <Label className="text-xs text-muted-foreground">{f.label}</Label>
              <Input
                value={conditions[f.key]}
                onChange={(e) => setCond(f.key, e.target.value)}
                placeholder={f.placeholder}
              />
            </div>
          ))}
        </div>
      </div>

      {/* 结果三选 */}
      <div className="space-y-1.5">
        <Label>实验结果</Label>
        <RadioGroup
          value={outcome}
          onValueChange={(v) => setOutcome(v as 'film' | 'partial' | 'failed')}
          className="flex gap-4"
        >
          <div className="flex items-center gap-1.5">
            <RadioGroupItem value="film" id="outcome-film" />
            <Label htmlFor="outcome-film" className="font-normal">成膜</Label>
          </div>
          <div className="flex items-center gap-1.5">
            <RadioGroupItem value="partial" id="outcome-partial" />
            <Label htmlFor="outcome-partial" className="font-normal">部分成膜</Label>
          </div>
          <div className="flex items-center gap-1.5">
            <RadioGroupItem value="failed" id="outcome-failed" />
            <Label htmlFor="outcome-failed" className="font-normal">失败</Label>
          </div>
        </RadioGroup>
      </div>

      {/* 机械强度 / 操作人 */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>机械强度</Label>
          <Input value={strength} onChange={(e) => setStrength(e.target.value)} placeholder="如 柔韧可弯折" />
        </div>
        <div className="space-y-1.5">
          <Label>操作人</Label>
          <Input value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="如 张三" />
        </div>
      </div>

      {/* 备注 */}
      <div className="space-y-1.5">
        <Label>备注</Label>
        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="其他观察、异常情况……"
          rows={3}
        />
      </div>

      <Button onClick={handleSubmit} disabled={saving} className="w-full">
        {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
        保存记录
      </Button>
    </div>
  );
}
