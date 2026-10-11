/**
 * 科研日记编辑对话框（新建 / 编辑共用）。
 * - 正文为 Markdown 原文（列表页用 react-markdown + @/lib/markdown 的 mdComponents 渲染）
 * - @ 实验记录：从实验记录列表多选（MentionPicker），存为结构化 mentions，
 *   同时在正文光标处插入可点击引用 `@编号(record:rec_xxx)`
 * - 只读引用实验记录：不写、不改实验记录模块的任何数据
 */
import { useEffect, useRef, useState } from 'react';
import { AtSign, Loader2, Save, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { type RecordItem } from '@/components/records/api';
import { pairLabel } from '@/components/records/meta';
import MentionPicker from './MentionPicker';
import { createDiary, updateDiary, type DiaryEntry } from './api';
import { cn } from '@/lib/utils';

export interface DiaryEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** null = 新建；否则编辑该篇 */
  entry: DiaryEntry | null;
  /** 保存成功回调（created 标记是否新建，供列表页提示/刷新） */
  onSaved: (entry: DiaryEntry, created: boolean) => void;
}

/** 本地日期 YYYY-MM-DD（新建默认今天） */
function today(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 正文里的结构化引用写法（列表页把它渲染成可点击 chip） */
export function mentionMarkdown(recordId: string, label: string): string {
  return `@${label}(record:${recordId})`;
}

export default function DiaryEditorDialog({
  open,
  onOpenChange,
  entry,
  onSaved,
}: DiaryEditorDialogProps) {
  const [date, setDate] = useState(today());
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [mentions, setMentions] = useState<string[]>([]);
  /** id → 「编号 单体对」展示文本 */
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const contentRef = useRef<HTMLTextAreaElement>(null);

  // 打开（或切换条目）时用条目内容初始化表单
  useEffect(() => {
    if (!open) return;
    setDate(entry?.date || today());
    setTitle(entry?.title || '');
    setContent(entry?.content || '');
    setMentions(entry?.mentions ?? []);
    const map: Record<string, string> = {};
    (entry?.mention_details ?? []).forEach((d) => {
      const no = d.experiment_no?.trim() || d.record_id;
      map[d.record_id] = d.label ? `${no} ${d.label}` : no;
    });
    setLabels(map);
    setPickerOpen(false);
  }, [open, entry]);

  /** 在光标处插入文本（并把光标移到插入内容之后） */
  const insertText = (text: string) => {
    const el = contentRef.current;
    const start = el?.selectionStart ?? content.length;
    const end = el?.selectionEnd ?? content.length;
    setContent(content.slice(0, start) + text + content.slice(end));
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const pos = start + text.length;
      el.setSelectionRange(pos, pos);
    });
  };

  /** 选择器确认：更新结构化 mentions，并在正文插入可点击引用 */
  const handlePicked = (records: RecordItem[]) => {
    const nextLabels = { ...labels };
    const inserted: string[] = [];
    records.forEach((rec) => {
      const label = rec.experiment_no?.trim() || pairLabel(rec);
      nextLabels[rec.record_id] = `${label} ${pairLabel(rec)}`;
      if (!mentions.includes(rec.record_id)) inserted.push(mentionMarkdown(rec.record_id, label));
    });
    setLabels(nextLabels);
    setMentions(Array.from(new Set([...mentions, ...records.map((r) => r.record_id)])));
    if (inserted.length > 0) insertText(`${inserted.join(' ')} `);
  };

  const removeMention = (id: string) => {
    setMentions((prev) => prev.filter((x) => x !== id));
  };

  const handleSave = async () => {
    if (!content.trim()) {
      toast.error('日记正文不能为空');
      return;
    }
    setSaving(true);
    try {
      const body = { date, title: title.trim(), content, mentions };
      const saved = entry
        ? await updateDiary(entry.entry_id, body)
        : await createDiary(body);
      toast.success(entry ? '日记已更新' : '日记已保存');
      onSaved(saved, !entry);
      onOpenChange(false);
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex max-h-[90dvh] w-[calc(100vw-1.5rem)] flex-col gap-3 overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-left">
              {entry ? '编辑日记' : '写日记'}
            </DialogTitle>
            <DialogDescription className="text-left">
              正文支持 Markdown（标题 / 列表 / 表格 / 代码块）；@ 关联的实验记录只做引用，
              不会改动实验记录本身
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[160px_1fr]">
            <div className="space-y-1.5">
              <Label htmlFor="diary-date">日期</Label>
              <Input
                id="diary-date"
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="diary-title">标题（可空）</Label>
              <Input
                id="diary-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="如：A5 体系界面聚合第二天"
                maxLength={200}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label htmlFor="diary-content">正文（Markdown）</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 gap-1 px-2 text-xs"
                onClick={() => setPickerOpen(true)}
              >
                <AtSign className="h-3.5 w-3.5" /> 关联实验记录
              </Button>
            </div>
            <Textarea
              id="diary-content"
              ref={contentRef}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={'## 今天做了什么\n\n- 投料比例 …\n- 现象 …'}
              className="min-h-52 font-mono text-sm"
            />
          </div>

          {mentions.length > 0 && (
            <div className="space-y-1.5">
              <Label>已关联的实验记录（{mentions.length}）</Label>
              <div className="flex flex-wrap gap-1.5">
                {mentions.map((id) => {
                  const missing = entry?.mention_details?.find((d) => d.record_id === id)?.exists === false;
                  return (
                    <span
                      key={id}
                      className={cn(
                        'inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-0.5 text-xs',
                        missing
                          ? 'border-destructive/40 bg-destructive/5 text-destructive'
                          : 'border-primary/30 bg-primary/5 text-primary',
                      )}
                      title={id}
                    >
                      <AtSign className="h-3 w-3 shrink-0" />
                      <span className="truncate">{labels[id] || id}</span>
                      {missing && <span className="shrink-0">（记录已删除）</span>}
                      <button
                        type="button"
                        onClick={() => removeMention(id)}
                        className="shrink-0 rounded p-0.5 hover:bg-black/10"
                        aria-label={`取消关联 ${id}`}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
              取消
            </Button>
            <Button onClick={() => void handleSave()} disabled={saving}>
              {saving ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-1 h-4 w-4" />
              )}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <MentionPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        value={mentions}
        onConfirm={handlePicked}
      />
    </>
  );
}
