/**
 * 科研日记页（v2.2.0）
 * - 列表按日期倒序，支持关键词 / 关联实验记录筛选
 * - 正文用 react-markdown + remark-gfm，元素映射统一取 @/lib/markdown 的 mdComponents
 * - @ 的实验记录：正文里 `@编号(record:rec_xxx)` 渲染成可点击引用，条目下方另有
 *   结构化关联 chip；点击复用实验记录的详情弹窗（RecordDetailDialog）查看
 * - 新建 / 编辑 / 删除（二次确认）
 * 实验记录模块只读引用：本页只调用 GET /api/records 与 GET /api/diary。
 */
import { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import {
  AtSign,
  ListFilter,
  Loader2,
  NotebookPen,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import DiaryEditorDialog from '@/components/diary/DiaryEditorDialog';
import {
  BackendUnavailableError,
  deleteDiary,
  listDiary,
  mentionLabel,
  type DiaryEntry,
  type MentionDetail,
} from '@/components/diary/api';
import { PageHeader } from '@/components/layout/PageHeader';
import RecordDetailDialog from '@/components/records/RecordDetailDialog';
import { getRecord, type RecordItem } from '@/components/records/api';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { openExternal } from '@/lib/external';
import { mdComponents } from '@/lib/markdown';
import { cn } from '@/lib/utils';

/** 关联记录 chip 样式（存在 / 已删除两态） */
const CHIP_BASE =
  'inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-0.5 text-xs transition-colors';

export default function Diary() {
  const [entries, setEntries] = useState<DiaryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [backendDown, setBackendDown] = useState(false);
  /** 输入框里的关键词（回车/点搜索后写入 appliedKeyword） */
  const [keywordInput, setKeywordInput] = useState('');
  const [appliedKeyword, setAppliedKeyword] = useState('');
  /** 按实验记录反查（点 chip 上的「筛选」图标设置） */
  const [recordFilter, setRecordFilter] = useState<{ id: string; label: string } | null>(null);

  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<DiaryEntry | null>(null);
  const [toDelete, setToDelete] = useState<DiaryEntry | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [detailRec, setDetailRec] = useState<RecordItem | null>(null);
  const [loadingRecordId, setLoadingRecordId] = useState('');

  const load = useCallback(async (keyword: string, recordId: string) => {
    setLoading(true);
    try {
      setEntries(
        await listDiary({
          keyword: keyword || undefined,
          record_id: recordId || undefined,
        }),
      );
      setBackendDown(false);
    } catch (err) {
      setBackendDown(err instanceof BackendUnavailableError);
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const reload = useCallback(
    () => load(appliedKeyword, recordFilter?.id ?? ''),
    [load, appliedKeyword, recordFilter],
  );

  useEffect(() => {
    void load(appliedKeyword, recordFilter?.id ?? '');
  }, [load, appliedKeyword, recordFilter]);

  /** 点击关联 chip：取实验记录并复用详情弹窗（已删除的给中文提示） */
  const openRecord = useCallback(async (recordId: string, detail?: MentionDetail) => {
    if (detail && detail.exists === false) {
      toast.error(`实验记录 ${recordId} 已不存在（可能已被删除）`);
      return;
    }
    setLoadingRecordId(recordId);
    try {
      const rec = await getRecord(recordId);
      setDetailRec(rec);
    } catch {
      // 错误提示已由 records/api 弹出（如 404 记录不存在）
    } finally {
      setLoadingRecordId('');
    }
  }, []);

  /** 按实验记录反查日记 */
  const applyRecordFilter = (recordId: string, detail?: MentionDetail) => {
    setRecordFilter({
      id: recordId,
      label: detail ? `${mentionLabel(detail, recordId)}` : recordId,
    });
  };

  /**
   * Markdown 元素映射：在公共 mdComponents 基础上，把 `record:` 协议的链接
   * 渲染成可点击引用（其余（含 http(s) 外链）沿用公共映射的行为）。
   */
  const componentsFor = (entryId: string): Components => ({
    ...mdComponents,
    a: ({ node, href, children, ...rest }) => {
      void node;
      if (href && href.startsWith('record:')) {
        const rid = href.slice('record:'.length);
        const detail = entries
          .find((x) => x.entry_id === entryId)
          ?.mention_details?.find((d) => d.record_id === rid);
        return (
          <button
            type="button"
            onClick={() => void openRecord(rid, detail)}
            title={`查看实验记录 ${rid}`}
            className="mx-0.5 inline-flex items-center gap-0.5 rounded bg-primary/10 px-1.5 py-0.5 align-baseline text-[0.85em] font-medium text-primary hover:bg-primary/20"
          >
            <AtSign className="h-3 w-3 shrink-0" />
            {children}
          </button>
        );
      }
      return (
        <a
          className="text-primary underline decoration-dotted underline-offset-2 hover:text-primary/80"
          href={href}
          target="_blank"
          rel="noreferrer"
          title={href}
          onClick={(e) => {
            if (href && /^https?:\/\//i.test(href)) {
              e.preventDefault();
              openExternal(href);
            }
          }}
          {...rest}
        >
          {children}
        </a>
      );
    },
  });

  const handleDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deleteDiary(toDelete.entry_id);
      toast.success('日记已删除');
      setToDelete(null);
      await reload();
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setDeleting(false);
    }
  };

  const clearFilters = () => {
    setKeywordInput('');
    setAppliedKeyword('');
    setRecordFilter(null);
  };

  const filtering = Boolean(appliedKeyword || recordFilter);

  return (
    <div className="space-y-5">
      <PageHeader
        title="科研日记"
        subtitle="按天记录实验思路与复盘，支持 Markdown 与 @ 实验记录；科研助手也能读到这些日记"
        actions={
          <Button
            onClick={() => {
              setEditing(null);
              setEditorOpen(true);
            }}
          >
            <Plus className="mr-1 h-4 w-4" /> 写日记
          </Button>
        }
      />

      {/* 筛选条：关键词（回车/点搜索） + 关联记录反查 chip */}
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setAppliedKeyword(keywordInput.trim());
        }}
      >
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            placeholder="搜索标题 / 正文 / 关联记录（回车）"
            className="w-72 pl-8"
          />
        </div>
        <Button type="submit" variant="outline" size="sm">
          搜索
        </Button>
        {recordFilter && (
          <Badge variant="outline" className="gap-1 border-primary/40 py-1 text-primary">
            <ListFilter className="h-3 w-3" /> 关联记录：{recordFilter.label}
            <button
              type="button"
              onClick={() => setRecordFilter(null)}
              aria-label="取消关联记录筛选"
              className="rounded p-0.5 hover:bg-primary/10"
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        )}
        {filtering && (
          <Button type="button" variant="ghost" size="sm" onClick={clearFilters}>
            清除筛选
          </Button>
        )}
      </form>

      {loading ? (
        <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 加载日记…
        </div>
      ) : backendDown ? (
        <div className="rounded-xl border border-dashed border-border py-16 text-center text-sm text-muted-foreground">
          后端未连接，无法读取日记；请确认 FastAPI 服务已启动
        </div>
      ) : entries.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border py-16 text-center text-sm text-muted-foreground">
          <NotebookPen className="mx-auto mb-2 h-6 w-6 opacity-60" />
          {filtering ? '没有符合条件的日记' : '还没有日记，点右上角「写日记」开始记录'}
        </div>
      ) : (
        <div className="mx-auto w-full max-w-4xl space-y-4">
          {entries.map((entry) => (
            <article
              key={entry.entry_id}
              className="rounded-xl border border-border bg-card p-4 shadow-sm"
            >
              <header className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-ui text-sm font-semibold text-primary">
                      {entry.date}
                    </span>
                    {entry.title && (
                      <h2 className="text-base font-semibold leading-snug">{entry.title}</h2>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2 text-muted-foreground"
                    onClick={() => {
                      setEditing(entry);
                      setEditorOpen(true);
                    }}
                  >
                    <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2 text-muted-foreground hover:text-destructive"
                    onClick={() => setToDelete(entry)}
                  >
                    <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                  </Button>
                </div>
              </header>

              {/* @ 关联的实验记录（结构化 mentions → 可点击引用） */}
              {(entry.mentions ?? []).length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {(entry.mentions ?? []).map((rid) => {
                    const detail = entry.mention_details?.find((d) => d.record_id === rid);
                    const missing = detail?.exists === false;
                    return (
                      <span key={rid} className="inline-flex items-center">
                        <button
                          type="button"
                          onClick={() => void openRecord(rid, detail)}
                          title={`查看实验记录 ${rid}`}
                          className={cn(
                            CHIP_BASE,
                            'rounded-r-none',
                            missing
                              ? 'border-destructive/40 bg-destructive/5 text-destructive'
                              : 'border-primary/30 bg-primary/5 text-primary hover:bg-primary/10',
                          )}
                        >
                          {loadingRecordId === rid ? (
                            <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
                          ) : (
                            <AtSign className="h-3 w-3 shrink-0" />
                          )}
                          <span className="truncate">
                            {mentionLabel(detail, rid)}
                          </span>
                          {detail?.label && (
                            <span className="truncate opacity-70">{detail.label}</span>
                          )}
                          {missing && <span className="shrink-0">（已删除）</span>}
                        </button>
                        <button
                          type="button"
                          onClick={() => applyRecordFilter(rid, detail)}
                          title="只看关联该实验记录的日记"
                          className={cn(
                            CHIP_BASE,
                            'rounded-l-none border-l-0 px-1',
                            missing
                              ? 'border-destructive/40 bg-destructive/5 text-destructive'
                              : 'border-primary/30 bg-primary/5 text-primary hover:bg-primary/10',
                          )}
                        >
                          <ListFilter className="h-3 w-3" />
                        </button>
                      </span>
                    );
                  })}
                </div>
              )}

              {/* Markdown 正文（mdComponents + record: 引用拦截） */}
              <div className="mt-2 text-sm text-foreground">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={componentsFor(entry.entry_id)}
                >
                  {entry.content}
                </ReactMarkdown>
              </div>

              {entry.updated_at && entry.created_at && entry.updated_at !== entry.created_at && (
                <p className="mt-2 text-xs text-muted-foreground">
                  更新于 {entry.updated_at.replace('T', ' ').slice(0, 16)}
                </p>
              )}
            </article>
          ))}
        </div>
      )}

      <DiaryEditorDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        entry={editing}
        onSaved={() => void reload()}
      />

      {/* 实验记录详情（复用实验记录模块的只读弹窗） */}
      <RecordDetailDialog rec={detailRec} onClose={() => setDetailRec(null)} />

      {/* 删除二次确认 */}
      <AlertDialog
        open={toDelete !== null}
        onOpenChange={(open) => !open && setToDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除这篇日记？</AlertDialogTitle>
            <AlertDialogDescription className="text-left">
              {toDelete
                ? `${toDelete.date}${toDelete.title ? `｜${toDelete.title}` : ''} 将被永久删除，`
                : ''}
              此操作不可撤销（关联的实验记录不受影响）。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={(e) => {
                e.preventDefault();
                void handleDelete();
              }}
            >
              {deleting ? '删除中…' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
