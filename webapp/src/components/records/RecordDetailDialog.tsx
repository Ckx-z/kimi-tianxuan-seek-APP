/**
 * 实验记录放大详情对话框（只读，时间线页与「我的」收藏详情嵌套共用）
 * - 大字号全字段展示：单体对 / 结果 / 条件九键 / 强度 / 操作人 / 备注 /
 *   自我总结 / 失误 / 预测快照 / 实验过程时间线（ProcessPanel 可维护流程与附件）
 * - 响应式：窗口限高 + 固定头部 + 内部滚动；窄屏条件网格自动换行
 * - onEdit 提供时头部出现「编辑」入口；onBack 提供时出现「返回」（嵌套场景用）
 */
import { useState } from 'react';
import { ArrowLeft, FileDown, Loader2, Pencil } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import ProcessPanel from './ProcessPanel';
import { CONDITION_LABELS, OUTCOME_META, pairLabel } from './meta';
import { exportRecordWord, type RecordItem } from './api';

export interface RecordDetailDialogProps {
  rec: RecordItem | null;
  onClose: () => void;
  /** ProcessPanel 保存后记录有更新 */
  onChanged?: (rec: RecordItem) => void;
  /** 提供时显示「编辑」按钮（点击后由父级关闭本对话框并打开编辑对话框） */
  onEdit?: (rec: RecordItem) => void;
  /** 提供时显示「返回」按钮（嵌套对话框场景：返回上一层，不关闭整个链路） */
  onBack?: () => void;
}

export default function RecordDetailDialog({
  rec,
  onClose,
  onChanged,
  onEdit,
  onBack,
}: RecordDetailDialogProps) {
  /** Word 导出下载中态（失败提示由 api 封装弹出） */
  const [exporting, setExporting] = useState(false);

  const handleExport = async (target: RecordItem) => {
    setExporting(true);
    try {
      await exportRecordWord(target);
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setExporting(false);
    }
  };

  return (
    <Dialog open={rec !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[90dvh] w-[calc(100vw-1.5rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
        {rec && (
          <>
            <DialogHeader className="border-b border-border px-5 py-4">
              <div className="flex items-center gap-2 pr-6">
                {onBack && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="-ml-2 h-8 shrink-0 px-2 text-muted-foreground"
                    onClick={onBack}
                  >
                    <ArrowLeft className="mr-1 h-4 w-4" /> 返回
                  </Button>
                )}
                <DialogTitle className="min-w-0 flex-1 text-left text-lg leading-snug">
                  实验记录 {rec.experiment_no || '（未填编号）'}
                  {rec.status === 'draft' && (
                    <Badge variant="outline" className="ml-2 border-gold/60 text-gold-foreground">
                      草稿
                    </Badge>
                  )}
                </DialogTitle>
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  disabled={exporting}
                  onClick={() => void handleExport(rec)}
                >
                  {exporting ? (
                    <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <FileDown className="mr-1 h-3.5 w-3.5" />
                  )}
                  {exporting ? '导出中…' : '导出 Word'}
                </Button>
                {onEdit && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0"
                    onClick={() => onEdit(rec)}
                  >
                    <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                  </Button>
                )}
              </div>
              <DialogDescription className="text-left">
                {rec.date}｜{rec.record_id}
              </DialogDescription>
            </DialogHeader>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
              <div>
                <p className="text-sm text-muted-foreground">单体对</p>
                <p className="text-lg font-medium">{pairLabel(rec)}</p>
                <p className="mt-1 break-all text-sm text-muted-foreground">
                  醛 SMILES：{rec.aldehyde?.smiles || '—'}
                  <br />
                  胺 SMILES：{rec.amine?.smiles || '—'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <p className="text-sm text-muted-foreground">结果</p>
                <Badge className={(OUTCOME_META[rec.outcome] ?? OUTCOME_META.failed).className}>
                  {(OUTCOME_META[rec.outcome] ?? OUTCOME_META.failed).label}
                </Badge>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">反应条件</p>
                <div className="mt-1 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
                  {Object.entries(CONDITION_LABELS).map(([key, label]) => {
                    const v = rec.conditions?.[key];
                    return (
                      <p key={key} className="text-sm">
                        <span className="text-muted-foreground">{label}：</span>
                        {v !== '' && v != null ? String(v) : '—'}
                      </p>
                    );
                  })}
                </div>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 sm:gap-4">
                <p className="text-sm">
                  <span className="text-muted-foreground">机械强度：</span>
                  {rec.strength || '—'}
                </p>
                <p className="text-sm">
                  <span className="text-muted-foreground">操作人：</span>
                  {rec.operator || '—'}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">备注</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-base">{rec.notes || '—'}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">自我总结</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-base">
                  {rec.self_summary || '—'}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">我认为的失误</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-base">
                  {rec.mistakes || '—'}
                </p>
              </div>
              {rec.prediction_snapshot && rec.prediction_snapshot.score != null && (
                <div className="rounded-lg border border-gold/50 bg-gold-muted px-3 py-2 text-sm">
                  <span className="font-medium">预测快照：</span>
                  评分 {Number(rec.prediction_snapshot.score).toFixed(3)}
                  {rec.prediction_snapshot.std != null &&
                    `（±${Number(rec.prediction_snapshot.std).toFixed(3)}）`}
                  {rec.prediction_snapshot.ood
                    ? `｜OOD：${rec.prediction_snapshot.ood}`
                    : ''}
                </div>
              )}
              {/* 实验过程时间线：完整流程 + 时间点记录（可编辑） */}
              <ProcessPanel rec={rec} onChanged={(updated) => onChanged?.(updated)} />
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
