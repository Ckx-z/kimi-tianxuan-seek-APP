/**
 * 通用实验记录编辑对话框（草稿继续编辑 / 正式记录整体修改共用）
 * - mode="draft"：底部两键「保存草稿」（宽松校验）/「转为正式记录」（编号必填 + 结果三选）
 * - mode="final"：正式记录全字段修改（编号 / 结果 / 条件 / 备注 / 自我总结 / 失误 / 流程时间线），
 *   底部一键「保存修改」，保持必填校验（experiment_no、outcome），后端走 final 完整校验
 * - 内嵌实验过程时间线面板（ProcessPanel，受控模式）：流程文本与时间线随
 *   「保存草稿 / 保存修改 / 转为正式记录」一次提交，不再要求先点面板内的单独保存；
 *   保存失败时对话框保持打开、已填内容不丢（错误提示由 api 层弹出）
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
import { updateRecord, type RecordItem, type TimelineEntry } from './api';

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

export interface RecordEditDialogProps {
  rec: RecordItem;
  /** 编辑模式；缺省按 rec.status 推断（draft → 草稿编辑，否则正式记录整体修改） */
  mode?: 'draft' | 'final';
  onClose: () => void;
  /** 保存成功后的回调 */
  onSaved: () => void;
}

export default function RecordEditDialog({ rec, mode, onClose, onSaved }: RecordEditDialogProps) {
  const isDraft = (mode ?? (rec.status === 'draft' ? 'draft' : 'final')) === 'draft';
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
  const [selfSummary, setSelfSummary] = useState(rec.self_summary || '');
  const [mistakes, setMistakes] = useState(rec.mistakes || '');
  const [saving, setSaving] = useState<'draft' | 'final' | null>(null);
  /** 编号为空的前端拦截提示（转正式 / 正式保存时） */
  const [noError, setNoError] = useState(false);
  /** 面板内流程/时间线变更后记录有更新，但本体未变，关闭时也需提示父级 */
  const [currentRec, setCurrentRec] = useState(rec);
  /** 流程文本与时间线（受控持有，随保存一并提交） */
  const [processNotes, setProcessNotes] = useState(rec.process_notes || '');
  const [timeline, setTimeline] = useState<TimelineEntry[]>(
    (rec.timeline || []).map((e) => ({ ...e, attachments: [...(e.attachments || [])] })),
  );

  /** 上传附件前由 ProcessPanel 调用：先把当前流程/时间线静默落盘 */
  const ensureTimelineSaved = async (): Promise<RecordItem | null> => {
    try {
      const updated = await updateRecord(rec.record_id, {
        process_notes: processNotes,
        timeline,
      });
      setCurrentRec(updated);
      return updated;
    } catch {
      // 错误提示已由 api 层弹出
      return null;
    }
  };

  /**
   * 提交：draft 模式下 finalize=false 保存草稿、true 转正式；
   * final 模式下固定按正式记录完整校验保存（编号 + 结果必填）。
   */
  const handleSubmit = async (finalize: boolean) => {
    if (finalize && !experimentNo.trim()) {
      setNoError(true);
      toast.error(isDraft ? '转为正式记录前请填写实验编号（必填）' : '实验编号为必填项');
      return;
    }
    if (finalize && !outcome) {
      toast.error(
        isDraft
          ? '转为正式记录前请选择实验结果（成膜 / 部分成膜 / 失败）'
          : '请选择实验结果（成膜 / 部分成膜 / 失败）',
      );
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
        self_summary: selfSummary.trim(),
        mistakes: mistakes.trim(),
        conditions,
        // 流程文本与时间线随主保存一次提交（合并原面板内的单独保存动作）
        process_notes: processNotes,
        timeline,
      });
      toast.success(
        isDraft
          ? finalize
            ? `已转为正式记录（编号 ${experimentNo.trim()}）`
            : '草稿已保存'
          : `记录 ${experimentNo.trim()} 已保存修改`,
      );
      onSaved();
      onClose();
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setSaving(null);
    }
  };

  const requiredHint = isDraft ? '（转正式时必填）' : '（必填）';

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[90dvh] w-[calc(100vw-1.5rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="border-b border-border px-5 py-4">
          <DialogTitle className="text-lg">
            {isDraft
              ? `编辑草稿 ${rec.experiment_no || `（${rec.record_id}）`}`
              : `编辑实验记录 ${rec.experiment_no || `（${rec.record_id}）`}`}
          </DialogTitle>
          <DialogDescription>
            {isDraft
              ? `${rec.date}｜草稿暂存中，可继续编辑后保存草稿，或转为正式记录`
              : `${rec.date}｜正式记录：全部字段均可修改，保存时保持编号与结果必填校验`}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {/* 实验编号（必填） */}
          <div className="space-y-1.5">
            <Label>
              实验编号 <span className="text-destructive">*{requiredHint}</span>
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

          {/* 结果三选（草稿可留空，正式必填） */}
          <div className="space-y-1.5">
            <Label>实验结果{isDraft ? '（草稿可留空）' : ''}</Label>
            <RadioGroup value={outcome} onValueChange={setOutcome} className="flex flex-wrap gap-4">
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="film" id="edit-outcome-film" />
                <Label htmlFor="edit-outcome-film" className="font-normal">成膜</Label>
              </div>
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="partial" id="edit-outcome-partial" />
                <Label htmlFor="edit-outcome-partial" className="font-normal">部分成膜</Label>
              </div>
              <div className="flex items-center gap-1.5">
                <RadioGroupItem value="failed" id="edit-outcome-failed" />
                <Label htmlFor="edit-outcome-failed" className="font-normal">失败</Label>
              </div>
            </RadioGroup>
          </div>

          {/* 反应条件九键 */}
          <div className="space-y-2">
            <Label className="text-muted-foreground">反应条件</Label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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

          {/* 自我总结（可后补） */}
          <div className="space-y-1.5">
            <Label>自我总结</Label>
            <Textarea
              value={selfSummary}
              onChange={(e) => setSelfSummary(e.target.value)}
              placeholder="本次实验的收获、结论与体会……"
              rows={3}
            />
          </div>

          {/* 我认为的失误（可后补） */}
          <div className="space-y-1.5">
            <Label>我认为的失误</Label>
            <Textarea
              value={mistakes}
              onChange={(e) => setMistakes(e.target.value)}
              placeholder="本次操作中认为存在的失误、可能的根因……"
              rows={3}
            />
          </div>

          {/* 实验过程时间线（受控：随底部「保存」一次提交） */}
          <ProcessPanel
            rec={currentRec}
            onChanged={setCurrentRec}
            value={{ processNotes, entries: timeline }}
            onValueChange={(v) => {
              setProcessNotes(v.processNotes);
              setTimeline(v.entries);
            }}
            hideSaveButton
            ensureSaved={ensureTimelineSaved}
          />
        </div>

        {/* 底部操作（固定不随内容滚动） */}
        <div className="flex gap-3 border-t border-border px-5 py-3">
          {isDraft ? (
            <>
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
            </>
          ) : (
            <Button
              className="flex-1"
              disabled={saving !== null}
              onClick={() => void handleSubmit(true)}
            >
              {saving === 'final' ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              保存修改
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
