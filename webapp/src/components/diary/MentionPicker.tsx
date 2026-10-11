/**
 * @ 实验记录选择器（科研日记用）。
 * 从实验记录列表里多选，每行展示「实验编号 + 标题（单体对）+ 实验时间 + 结果」。
 * 实验记录模块**只读**引用：本组件只调用 GET /api/records，绝不修改实验记录。
 */
import { useEffect, useMemo, useState } from 'react';
import { AtSign, Loader2, Search } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { listRecords, type RecordItem } from '@/components/records/api';
import { OUTCOME_META, experimentTime, pairLabel } from '@/components/records/meta';
import { cn } from '@/lib/utils';

export interface MentionPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 已关联的实验记录 id（打开时作为初始勾选） */
  value: string[];
  /** 确认选择（返回完整记录，供正文插入引用文本） */
  onConfirm: (records: RecordItem[]) => void;
}

/** 实验编号 + 标题（单体对）——选择器主行文本 */
export function recordTitle(rec: RecordItem): string {
  const no = rec.experiment_no?.trim() || '（未填编号）';
  return `${no}｜${pairLabel(rec)}`;
}

export default function MentionPicker({
  open,
  onOpenChange,
  value,
  onConfirm,
}: MentionPickerProps) {
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [failed, setFailed] = useState(false);

  // 打开时拉取实验记录列表并把已有关联设为初始勾选（value 只作初值，不进依赖）
  useEffect(() => {
    if (!open) return;
    setSelected(new Set(value));
    setKeyword('');
    setFailed(false);
    setLoading(true);
    listRecords()
      .then((rs) => setRecords(rs))
      .catch(() => {
        setRecords([]);
        setFailed(true);
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /** 新的在前 + 关键词过滤（编号 / 单体名 / record_id） */
  const filtered = useMemo(() => {
    const sorted = [...records].reverse();
    const kw = keyword.trim().toLowerCase();
    if (!kw) return sorted;
    return sorted.filter((r) =>
      `${r.experiment_no ?? ''} ${pairLabel(r)} ${r.record_id}`.toLowerCase().includes(kw),
    );
  }, [records, keyword]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleConfirm = () => {
    const picked = records.filter((r) => selected.has(r.record_id));
    onConfirm(picked);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85dvh] w-[calc(100vw-1.5rem)] flex-col sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-left">
            <AtSign className="h-4 w-4 text-primary" /> 关联实验记录
          </DialogTitle>
          <DialogDescription className="text-left">
            选择要 @ 的实验记录（可多选，只建立引用，不会改动实验记录本身）
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="按实验编号 / 单体名搜索"
            className="pl-8"
          />
        </div>

        <ScrollArea className="h-72 rounded-md border border-border">
          {loading ? (
            <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 加载实验记录…
            </div>
          ) : failed ? (
            <div className="flex h-72 items-center justify-center px-4 text-center text-sm text-muted-foreground">
              实验记录读取失败（后端未连接？）；请稍后重试
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex h-72 items-center justify-center px-4 text-center text-sm text-muted-foreground">
              {records.length === 0
                ? '还没有实验记录可关联；先到「实验记录」页录入'
                : '没有匹配的实验记录'}
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {filtered.map((rec) => {
                const on = selected.has(rec.record_id);
                const outcome = OUTCOME_META[rec.outcome] ?? OUTCOME_META.failed;
                return (
                  <li key={rec.record_id}>
                    <label
                      className={cn(
                        'flex cursor-pointer items-start gap-3 px-3 py-2.5 transition-colors hover:bg-muted/60',
                        on && 'bg-primary/5',
                      )}
                    >
                      <Checkbox
                        checked={on}
                        onCheckedChange={() => toggle(rec.record_id)}
                        className="mt-0.5"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">
                          {recordTitle(rec)}
                        </span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <span>{experimentTime(rec).date}</span>
                          <Badge className={cn('px-1.5 py-0 text-[10px]', outcome.className)}>
                            {outcome.label}
                          </Badge>
                          {rec.status === 'draft' && <span className="text-gold-foreground">草稿</span>}
                          <span className="font-mono text-[10px]">{rec.record_id}</span>
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </ScrollArea>

        <DialogFooter className="flex-row items-center justify-between gap-2 sm:justify-between">
          <span className="text-xs text-muted-foreground">已选 {selected.size} 条</span>
          <span className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button size="sm" onClick={handleConfirm}>
              确定关联
            </Button>
          </span>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
