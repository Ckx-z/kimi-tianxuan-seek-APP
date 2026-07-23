/**
 * 方案卡面板：模板下拉切换 + docx 上传提取模板 + 方案卡渲染（条件/步骤/checklist/单体提示）
 */
import { useRef, useState } from 'react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { uploadPlanTemplate, type PlanCardData, type PlanTemplateItem } from './api';

interface Props {
  card: PlanCardData | null;
  loading: boolean;
  error: string | null;
  templates: PlanTemplateItem[];
  templatesLoading: boolean;
  templateId: string; // '' 表示内置默认模板
  onTemplateChange: (id: string) => void;
  onTemplateUploaded: (tpl: PlanTemplateItem) => void;
  disabled?: boolean;
}

export default function PlanCardPanel({
  card,
  loading,
  error,
  templates,
  templatesLoading,
  templateId,
  onTemplateChange,
  onTemplateUploaded,
  disabled,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  /** docx 上传 → LLM 提取为模板 */
  const handleUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.docx')) {
      toast.error('仅支持 .docx 文件');
      return;
    }
    setUploading(true);
    try {
      const tpl = await uploadPlanTemplate(file);
      toast.success(`模板「${tpl.name}」提取成功`);
      onTemplateUploaded(tpl);
    } catch {
      // toast 已在 api 辅助中弹出
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
          <span>实验方案卡</span>
          <div className="flex items-center gap-2">
            {/* 模板下拉 */}
            {/* Radix Select 不允许空字符串 value，用 'default' 哨兵表示内置模板 */}
            <Select
              value={templateId || 'default'}
              onValueChange={(v) => onTemplateChange(v === 'default' ? '' : v)}
              disabled={disabled || templatesLoading}
            >
              <SelectTrigger className="w-[200px]">
                <SelectValue placeholder={templatesLoading ? '模板加载中…' : '选择模板'} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">内置默认（侯老师法 v3.9）</SelectItem>
                {templates.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                    {t.builtin ? '' : '（自定义）'}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* docx 上传 */}
            <input
              ref={fileRef}
              type="file"
              accept=".docx"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])}
            />
            <Button variant="outline" size="sm" disabled={disabled || uploading} onClick={() => fileRef.current?.click()}>
              {uploading ? '提取中…' : '上传 docx 提取模板'}
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* 加载骨架屏 */}
        {loading && (
          <div className="space-y-2">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        )}

        {/* 错误态 */}
        {!loading && error && (
          <div className="rounded-lg border border-dashed border-red-300 p-4 text-sm text-red-600 dark:text-red-400">
            方案卡生成失败：{error}
          </div>
        )}

        {/* 空态 */}
        {!loading && !error && !card && (
          <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            打分后自动生成方案卡
          </div>
        )}

        {/* 正常内容 */}
        {!loading && !error && card && (
          <div className="space-y-4">
            <div className="text-sm text-muted-foreground">
              模板：{card.template}
              {card.defaults_note ? ` ｜ ${card.defaults_note}` : ''}
            </div>

            {/* 反应条件 */}
            {Object.keys(card.conditions ?? {}).length > 0 && (
              <div>
                <h4 className="mb-1.5 text-sm font-semibold text-foreground">默认条件</h4>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg border p-3 text-sm md:grid-cols-3">
                  {Object.entries(card.conditions).map(([k, v]) => (
                    <div key={k}>
                      <span className="text-muted-foreground">{k}：</span>
                      <span className="font-medium text-foreground">{String(v)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 操作步骤 */}
            {card.steps?.length > 0 && (
              <div>
                <h4 className="mb-1.5 text-sm font-semibold text-foreground">操作步骤</h4>
                <ol className="list-inside list-decimal space-y-1 rounded-lg border p-3 text-sm text-foreground">
                  {card.steps.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ol>
              </div>
            )}

            {/* checklist 易错点 */}
            {card.checklist?.length > 0 && (
              <div>
                <h4 className="mb-1.5 text-sm font-semibold text-foreground">检查清单（易错点）</h4>
                <ul className="space-y-1.5 rounded-lg border border-gold/50 bg-gold-muted p-3 text-sm">
                  {card.checklist.map((c, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="text-gold">✓</span>
                      <span>
                        <span className="font-medium text-foreground">{c.item}</span>
                        {c.detail && <span className="text-muted-foreground"> — {c.detail}</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* 单体特异提示 */}
            {card.monomer_hints?.length > 0 && (
              <div>
                <h4 className="mb-1.5 text-sm font-semibold text-foreground">单体特异提示</h4>
                <ul className="list-inside list-disc space-y-1 rounded-lg border p-3 text-sm text-muted-foreground">
                  {card.monomer_hints.map((h, i) => (
                    <li key={i}>{h}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
