/**
 * 草稿继续编辑对话框
 * - 核心字段：实验编号 / 结果三选（可留空）/ 反应条件九键 / 机械强度 / 操作人 / 备注
 * - 内嵌实验过程时间线面板（ProcessPanel）
 * - 底部两键：「保存草稿」（宽松校验）/「转为正式记录」（编号必填 + 结果三选，走后端完整校验）
 */
import { useState } from 'react';
import { toast } from 'sonner';
import { CheckCircle2, Loader2, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import ProcessPanel from './ProcessPanel';
import { updateRecord, type RecordItem } from './api';

/** conditions 九键（与后端契约一致） */
const CONDITION_FIELDS: { key: string; label: string }[] = [
  { key: 'solvent_1', label: '溶剂一' },
  { key: 'solvent_2', label: '溶剂二' },
  { key: 'eluent', label: '洗脱剂' },
  { key: 'modulator', label: '调制剂' },
  { key: 'catalyst', label: '催化剂' },
  { key: 'temperature_c', label: '温度（℃）' },
  { key: 'time_days', label: '时间（天）' },
  { key: 'vessel', label: '容器' },
  { key: 'addition_order', label: '加料顺序' },
];

export interface DraftEditDialogProps {
  rec: RecordItem;
  onClose: () => void;
  /** 保存（草稿或转正式）成功后的回调 */
  onSaved: () => void;
}

export default function DraftEditDialog({ rec, onClose, onSaved }: DraftEditDialogProps) {
  const [experimentNo, setExperimentNo] = useState(rec.experiment_no || '');
  const [outcome, setOutcome] = useState<string>(rec.outcome || '');
  const [conditions, setConditions] = useState<Record<string, string>>(() => {
    const base: Record<string, string> = {};
    for (const f of CONDITION_FIELDS) {
      const v = rec.conditions?.[f.key];
      base[f.key] = v == null ? '' : String(v);
    }
    return base;
  });
  const [strength, setStrength] = useState(rec.strength || '');
  const [operator, setOperator] = useState(rec.operator || '');
  const [notes, setNotes] = useState(rec.notes || '');
  const [saving, setSaving] = useState<'draft' | 'final' | null>(null);
  /** 编号为空的前端拦截提示（仅转正式时） */
  const [noError, setNoError] = useState(false);
  /** 面板内流程/时间线变更后记录有更新，但草稿本体未变，关闭时也需提示父级 */
  const [currentRec, setCurrentRec] = useState(rec);

  /** 提交：finalize=false 保存草稿；true 转正式 */
  const handleSubmit = async (finalize: boolean) => {
    if (finalize && !experimentNo.trim()) {
      setNoError(true);
      toast.error('转为正式记录前请填写实验编号（必填）');
      return;
    }
    if (finalize && !outcome) {
      toast.error('转为正式记录前请选择实验结果（成膜 / 部分成膜 / 失败）');
      return;
    }
    setSaving(finalize ? 'final' : 'draft');
    try {
      await updateRecord(rec.record_id, {
        status: finalize ? 'final' : 'draft',
        experiment_no: experimentNo.trim(),
        outcome,
        strength: strength.trim(),
        operator: operator.trim(),
        notes: notes.trim(),
        conditions,
      });
      toast.success(
        finalize
          ? `已转为正式记录（编号 ${experimentNo.trim()}）`
          : '草稿已保存',
      );
      onSaved();
      onClose();
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setSaving(null);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-xl">编辑草稿 {rec.experiment_no || `（${rec.record_id}）`}</DialogTitle>
          <DialogDescription>
            {rec.date}｜草稿暂存中，可继续编辑后保存草稿，或转为正式记录
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 实验编号（转正式必填） */}
          <div className="space-y-1.5">
            <Label>
              实验编号 <span className="text-destructive">*（转正式时必填）</span>
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
            {noError && <p className="text-xs text-destructive">转为正式记录时实验编号为必填项</p>}
          </div>

          {/* 结果三选（草稿可留空） */}
          <div className="space-y-1.5">
            <Label>实验结果（草稿可留空）</Label>
            <RadioGroup value={outcome} onValueChange={setOutcome} className="flex gap-4">
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="film" id="draft-outcome-film" />
                <Label htmlFor="draft-outcome-film" className="font-normal">成膜</Label>
              </div>
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="partial" id="draft-outcome-partial" />
                <Label htmlFor="draft-outcome-partial" className="font-normal">部分成膜</Label>
              </div>
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="failed" id="draft-outcome-failed" />
                <Label htmlFor="draft-outcome-failed" className="font-normal">失败</Label>
              </div>
            </RadioGroup>
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
                    onChange={(e) =>
                      setConditions((prev) => ({ ...prev, [f.key]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
          </div>

          {/* 机械强度 / 操作人 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>机械强度</Label>
              <Input value={strength} onChange={(e) => setStrength(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>操作人</Label>
              <Input value={operator} onChange={(e) => setOperator(e.target.value)} />
            </div>
          </div>

          {/* 备注 */}
          <div className="space-y-1.5">
            <Label>备注</Label>
            <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
          </div>

          {/* 实验过程时间线（草稿也可维护） */}
          <ProcessPanel rec={currentRec} onChanged={setCurrentRec} />

          {/* 底部操作 */}
          <div className="flex gap-3">
            <Button
              variant="outline"
              className="flex-1"
              disabled={saving !== null}
              onClick={() => void handleSubmit(false)}
            >
              {saving === 'draft' ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              保存草稿
            </Button>
            <Button
              className="flex-1"
              disabled={saving !== null}
              onClick={() => void handleSubmit(true)}
            >
              {saving === 'final' ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="mr-2 h-4 w-4" />
              )}
              转为正式记录
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
