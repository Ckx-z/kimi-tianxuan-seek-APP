/**
 * 实验过程时间线面板（记录详情 / 草稿编辑内嵌）
 * - 「完整实验流程」长文本区
 * - 时间点记录条目：时间标注（日期时间或第几天/第几小时）+ 过程描述 + 照片/附件
 * - 附件：图片缩略图预览、点击放大（Dialog lightbox）、非图片显示下载链接、可删除
 * - 「保存流程与时间线」统一 PUT /api/records/{id}
 */
import { useRef, useState } from 'react';
import { toast } from 'sonner';
import { ImageIcon, Loader2, Paperclip, Plus, Save, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  attachmentUrl,
  deleteAttachment,
  getRecord,
  updateRecord,
  uploadAttachment,
  type AttachmentMeta,
  type RecordItem,
  type TimelineEntry,
} from './api';

export interface ProcessPanelProps {
  rec: RecordItem;
  /** 记录发生变更（保存/上传/删除附件）后的回调，参数为最新记录 */
  onChanged: (rec: RecordItem) => void;
}

/** 客户端生成条目 id（服务端保留非空 entry_id，保证跨保存稳定） */
function newEntryId(): string {
  return `tl_${Math.random().toString(16).slice(2, 10)}`;
}

/** 附件大小人性化显示 */
function sizeLabel(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
  return `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

export default function ProcessPanel({ rec, onChanged }: ProcessPanelProps) {
  const [processNotes, setProcessNotes] = useState(rec.process_notes || '');
  const [entries, setEntries] = useState<TimelineEntry[]>(
    (rec.timeline || []).map((e) => ({ ...e, attachments: [...(e.attachments || [])] })),
  );
  const [saving, setSaving] = useState(false);
  const [uploadingEntry, setUploadingEntry] = useState<string | null>(null);
  /** 点击图片放大预览 */
  const [preview, setPreview] = useState<AttachmentMeta | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  /** 保存流程文本 + 时间线条目，返回最新记录 */
  const save = async (silent = false): Promise<RecordItem | null> => {
    setSaving(true);
    try {
      const updated = await updateRecord(rec.record_id, {
        process_notes: processNotes,
        timeline: entries,
      });
      onChanged(updated);
      if (!silent) toast.success('实验流程与时间线已保存');
      return updated;
    } catch {
      return null;
    } finally {
      setSaving(false);
    }
  };

  const addEntry = () =>
    setEntries((prev) => [
      ...prev,
      { entry_id: newEntryId(), time_label: '', description: '', attachments: [] },
    ]);

  const removeEntry = (entryId: string) =>
    setEntries((prev) => prev.filter((e) => e.entry_id !== entryId));

  const patchEntry = (entryId: string, patch: Partial<TimelineEntry>) =>
    setEntries((prev) => prev.map((e) => (e.entry_id === entryId ? { ...e, ...patch } : e)));

  /** 上传附件：先确保时间线已落盘（条目在服务端存在），再上传 */
  const handleUpload = async (entryId: string, file: File) => {
    if (file.size > 20 * 1024 * 1024) {
      toast.error('附件超过大小限制（20MB）');
      return;
    }
    setUploadingEntry(entryId);
    try {
      const fresh = await save(true); // 静默保存，保证 entry_id 已在服务端登记
      if (!fresh) return;
      await uploadAttachment(rec.record_id, entryId, file);
      // 以服务端为准同步本地条目（含附件元数据）
      const latest = await getRecord(rec.record_id);
      setEntries(latest.timeline || []);
      onChanged(latest);
      toast.success(`附件「${file.name}」已上传`);
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setUploadingEntry(null);
    }
  };

  /** 删除附件（服务端立即生效） */
  const handleDeleteAttachment = async (att: AttachmentMeta) => {
    try {
      await deleteAttachment(rec.record_id, att.attachment_id);
      const latest = await getRecord(rec.record_id);
      setEntries(latest.timeline || []);
      onChanged(latest);
      toast.success(`附件「${att.filename}」已删除`);
    } catch {
      // 错误提示已由 api 封装弹出
    }
  };

  return (
    <div className="space-y-4 rounded-lg border border-border bg-background p-4">
      <h3 className="text-base font-semibold text-foreground">实验过程时间线</h3>

      {/* 完整实验流程 */}
      <div className="space-y-1.5">
        <Label className="text-sm text-muted-foreground">完整实验流程</Label>
        <Textarea
          value={processNotes}
          onChange={(e) => setProcessNotes(e.target.value)}
          placeholder="整体流程描述，如：投料 → 密封陈化 → 丙酮洗涤 → 真空干燥……"
          rows={3}
        />
      </div>

      {/* 时间点记录条目 */}
      <div className="space-y-3">
        <Label className="text-sm text-muted-foreground">时间点记录</Label>
        {entries.length === 0 && (
          <p className="text-sm text-muted-foreground">暂无时间点记录，点击下方「添加时间点」。</p>
        )}
        {entries.map((entry, idx) => (
          <div key={entry.entry_id} className="space-y-2 rounded-lg border border-border bg-card p-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">#{idx + 1}</span>
              <Input
                value={entry.time_label}
                onChange={(e) => patchEntry(entry.entry_id, { time_label: e.target.value })}
                placeholder="时间：如 2025-01-03 14:00 或 第2天 / 第12小时"
                className="h-8 flex-1 text-sm"
              />
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => removeEntry(entry.entry_id)}
                title="删除该时间点"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
            <Textarea
              value={entry.description}
              onChange={(e) => patchEntry(entry.entry_id, { description: e.target.value })}
              placeholder="该时间点的实验过程描述……"
              rows={2}
              className="text-sm"
            />

            {/* 附件区 */}
            <div className="flex flex-wrap items-center gap-2">
              {entry.attachments.map((att) =>
                att.is_image ? (
                  <div key={att.attachment_id} className="group relative">
                    <img
                      src={attachmentUrl(rec.record_id, att.attachment_id)}
                      alt={att.filename}
                      title={`${att.filename}（${sizeLabel(att.size)}），点击放大`}
                      className="h-16 w-16 cursor-zoom-in rounded-md border border-border object-cover"
                      onClick={() => setPreview(att)}
                    />
                    <button
                      type="button"
                      title="删除附件"
                      onClick={() => handleDeleteAttachment(att)}
                      className="absolute -right-1.5 -top-1.5 hidden h-4 w-4 items-center justify-center rounded-full bg-destructive text-destructive-foreground group-hover:flex"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ) : (
                  <div
                    key={att.attachment_id}
                    className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs"
                  >
                    <Paperclip className="h-3.5 w-3.5 text-muted-foreground" />
                    <a
                      href={attachmentUrl(rec.record_id, att.attachment_id)}
                      target="_blank"
                      rel="noreferrer"
                      className="max-w-32 truncate text-foreground underline-offset-2 hover:underline"
                      title={att.filename}
                    >
                      {att.filename}
                    </a>
                    <span className="text-muted-foreground">{sizeLabel(att.size)}</span>
                    <button
                      type="button"
                      title="删除附件"
                      onClick={() => handleDeleteAttachment(att)}
                      className="text-destructive hover:text-destructive"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ),
              )}
              {/* 上传按钮（隐藏 input） */}
              <input
                ref={(el) => {
                  fileInputs.current[entry.entry_id] = el;
                }}
                type="file"
                accept="image/*,.pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleUpload(entry.entry_id, f);
                  e.target.value = '';
                }}
              />
              <Button
                variant="outline"
                size="sm"
                disabled={uploadingEntry === entry.entry_id || saving}
                onClick={() => fileInputs.current[entry.entry_id]?.click()}
              >
                {uploadingEntry === entry.entry_id ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ImageIcon className="mr-1 h-3.5 w-3.5" />
                )}
                上传照片/附件
              </Button>
            </div>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={addEntry}>
          <Plus className="mr-1 h-3.5 w-3.5" /> 添加时间点
        </Button>
      </div>

      <Button onClick={() => void save()} disabled={saving} size="sm">
        {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Save className="mr-1.5 h-4 w-4" />}
        保存流程与时间线
      </Button>

      {/* 图片放大预览 */}
      <Dialog open={preview !== null} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent className="max-h-[90vh] overflow-auto sm:max-w-3xl">
          {preview && (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                {preview.filename}（{sizeLabel(preview.size)}）
              </p>
              <img
                src={attachmentUrl(rec.record_id, preview.attachment_id)}
                alt={preview.filename}
                className="max-h-[75vh] w-full rounded-md object-contain"
              />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
