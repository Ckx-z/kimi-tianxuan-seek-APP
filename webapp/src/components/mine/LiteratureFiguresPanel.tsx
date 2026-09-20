/**
 * 「文献图谱」面板（v1.7.0，需求三）
 * - 选择文献 → 画廊展示三类图谱（structure / spectra / mechanism）
 * - 上传图片（PNG/JPG/SVG/WebP）+ 标注（类型/图注/标签）
 * - SMILES 输入 → RDKit 生成 2D 结构图入库
 * - 图谱操作：下载 / 编辑标注 / 删除（二次确认）
 * - 与成膜打分联动：structure 类「导入成膜打分」→ /toolbox/query?a=或b= 预填
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { Download, FlaskConical, ImagePlus, Loader2, Pencil, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const BASE = '/api/literature';

// ---------- 类型 ----------

interface PaperMeta {
  paper_id: string;
  title: string;
  doi: string;
  journal: string;
  year?: number | null;
}

interface FigureMeta {
  fig_id: string;
  paper_id: string;
  figure_type: 'structure' | 'spectra' | 'mechanism';
  caption: string;
  tags: string[];
  meta: Record<string, unknown>;
  file: string;
  mime: string;
  size: number;
  score_note?: string | null;
  created_at: string;
}

const TYPE_LABEL: Record<string, string> = {
  structure: '结构图',
  spectra: '光谱',
  mechanism: '机理图',
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: init?.body && !(init.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : undefined,
      ...init,
    });
  } catch {
    toast.error('无法连接后端服务，请确认服务已启动');
    throw new Error('backend-unavailable');
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      /* 非 JSON */
    }
    toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as T;
}

export function figureFileUrl(figId: string): string {
  return `${BASE}/figures/${encodeURIComponent(figId)}/file`;
}

// ---------- 组件 ----------

export function LiteratureFiguresPanel({
  initialPaperId,
  fixedPaperId,
}: {
  initialPaperId?: string;
  /** v1.9.0：嵌入文献卡片时固定 paper_id（隐藏自己的选择器） */
  fixedPaperId?: string;
}) {
  const navigate = useNavigate();

  const [papers, setPapers] = useState<PaperMeta[]>([]);
  const [paperId, setPaperId] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [figures, setFigures] = useState<FigureMeta[]>([]);
  const [typeFilter, setTypeFilter] = useState<'all' | 'structure' | 'spectra' | 'mechanism'>('all');

  // 上传/生成表单
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadType, setUploadType] = useState<'structure' | 'spectra' | 'mechanism'>('spectra');
  const [caption, setCaption] = useState('');
  const [tags, setTags] = useState('');
  const [smiles, setSmiles] = useState('');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // 编辑/删除
  const [editTarget, setEditTarget] = useState<FigureMeta | null>(null);
  const [editCaption, setEditCaption] = useState('');
  const [editTags, setEditTags] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<FigureMeta | null>(null);

  const refreshFigures = useCallback(async (pid: string) => {
    try {
      const data = await request<{ figures: FigureMeta[] }>(
        `/figures?paper_id=${encodeURIComponent(pid)}`);
      setFigures(data.figures ?? []);
    } catch {
      setFigures([]);
    }
  }, []);

  useEffect(() => {
    if (fixedPaperId) {
      // v1.9.0：固定文献模式（由文献卡片传入），不加载选择器
      setPaperId(fixedPaperId);
      void refreshFigures(fixedPaperId);
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const data = await request<{ papers: PaperMeta[] }>('/papers');
        const list = data.papers ?? [];
        setPapers(list);
        const target =
          (initialPaperId && list.some((p) => p.paper_id === initialPaperId)
            ? initialPaperId
            : list[0]?.paper_id) ?? '';
        setPaperId(target);
        if (target) await refreshFigures(target);
      } catch {
        /* 已 toast */
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchPaper = async (pid: string) => {
    setPaperId(pid);
    await refreshFigures(pid);
  };

  const doUpload = async (file: File) => {
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('figure_type', uploadType);
      form.append('caption', caption.trim());
      form.append('tags', tags.trim());
      if (uploadType === 'structure' && smiles.trim()) {
        form.append('meta_json', JSON.stringify({ smiles: smiles.trim() }));
      }
      await request(`/${encodeURIComponent(paperId)}/figures`, {
        method: 'POST',
        body: form,
      });
      toast.success('图谱已上传');
      setUploadOpen(false);
      await refreshFigures(paperId);
    } catch {
      /* 已 toast */
    } finally {
      setBusy(false);
    }
  };

  const doFromSmiles = async () => {
    if (!smiles.trim()) {
      toast.error('请输入 SMILES');
      return;
    }
    setBusy(true);
    try {
      await request('/figures/from-smiles', {
        method: 'POST',
        body: JSON.stringify({ paper_id: paperId, smiles: smiles.trim(), caption: caption.trim() || undefined }),
      });
      toast.success('结构图已生成');
      setUploadOpen(false);
      await refreshFigures(paperId);
    } catch {
      /* 已 toast */
    } finally {
      setBusy(false);
    }
  };

  const saveEdit = async () => {
    if (!editTarget) return;
    try {
      await request(`/figures/${encodeURIComponent(editTarget.fig_id)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          caption: editCaption.trim(),
          tags: editTags.split(',').map((t) => t.trim()).filter(Boolean),
        }),
      });
      toast.success('标注已更新');
      setEditTarget(null);
      await refreshFigures(paperId);
    } catch {
      /* 已 toast */
    }
  };

  const doDelete = async () => {
    if (!deleteTarget) return;
    try {
      await request(`/figures/${encodeURIComponent(deleteTarget.fig_id)}`, {
        method: 'DELETE',
      });
      toast.success('图谱已删除');
      setDeleteTarget(null);
      await refreshFigures(paperId);
    } catch {
      /* 已 toast */
    }
  };

  const shown = figures.filter((f) => typeFilter === 'all' || f.figure_type === typeFilter);
  /** v1.9.7：点击缩略图放大（Lightbox）——此前只有小图与"下载原图"，用户看不到大图 */
  const [zoomIndex, setZoomIndex] = useState<number | null>(null);
  const zoomFigure = zoomIndex !== null ? shown[zoomIndex] : null;

  const stepZoom = useCallback((delta: number) => {
    setZoomIndex((i) => {
      if (i === null || shown.length === 0) return i;
      return (i + delta + shown.length) % shown.length;
    });
  }, [shown.length]);

  // Lightbox 键盘：← / → 切换，Esc 关闭
  useEffect(() => {
    if (zoomIndex === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') stepZoom(-1);
      else if (e.key === 'ArrowRight') stepZoom(1);
      else if (e.key === 'Escape') setZoomIndex(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [zoomIndex, stepZoom]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">文献图谱</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-4 pt-2">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : !fixedPaperId && papers.length === 0 ? (
          // v1.9.7 修复：该分支此前未区分「固定文献模式」，而本面板在文献卡片里
          // **只以固定模式使用**（不加载 papers）→ papers 恒为空 → 画廊永远不可达，
          // 于是有 22 张图的文献也显示「文献库为空」。现在只有非固定模式才提示库空。
          <p className="text-sm text-muted-foreground">
            文献库为空：先在上方「文献录入」入库文献，再为其上传图谱。
          </p>
        ) : (
          <>
            {/* 选择文献 + 类型筛选 + 上传入口 */}
            <div className="flex flex-wrap items-center gap-2">
              {!fixedPaperId && (
                <Select value={paperId} onValueChange={(v) => void switchPaper(v)}>
                  <SelectTrigger className="max-w-[420px]" aria-label="选择文献">
                    <SelectValue placeholder="选择文献" />
                  </SelectTrigger>
                  <SelectContent>
                    {papers.map((p) => (
                      <SelectItem key={p.paper_id} value={p.paper_id} title={p.title}>
                        #{p.paper_id} {p.title.slice(0, 40)}{p.title.length > 40 ? '…' : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {(['all', 'structure', 'spectra', 'mechanism'] as const).map((t) => (
                <Button
                  key={t}
                  size="sm"
                  variant={typeFilter === t ? 'default' : 'outline'}
                  onClick={() => setTypeFilter(t)}
                >
                  {t === 'all' ? '全部' : TYPE_LABEL[t]}
                </Button>
              ))}
              <Button
                size="sm"
                variant="outline"
                className="ml-auto"
                onClick={() => {
                  setUploadType('spectra');
                  setCaption('');
                  setTags('');
                  setSmiles('');
                  setUploadOpen(true);
                }}
              >
                <ImagePlus className="mr-1.5 h-3.5 w-3.5" />
                上传图谱
              </Button>
            </div>

            {/* 画廊 */}
            {shown.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                该文献暂无{typeFilter === 'all' ? '' : TYPE_LABEL[typeFilter]}文献图片。
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                {shown.map((f, idx) => (
                  <div
                    key={f.fig_id}
                    className="group overflow-hidden rounded-lg border border-border bg-muted/20"
                  >
                    {/* v1.9.7：点击缩略图放大（此前只能看 112px 小图或下载原图） */}
                    <button
                      type="button"
                      title="点击查看大图"
                      onClick={() => setZoomIndex(idx)}
                      className="relative flex h-32 w-full cursor-zoom-in items-center justify-center bg-white/60 p-1 dark:bg-black/20"
                    >
                      <img
                        src={figureFileUrl(f.fig_id)}
                        alt={f.caption || '文献图片'}
                        className="max-h-28 max-w-full object-contain transition-transform group-hover:scale-[1.03]"
                        loading="lazy"
                      />
                      <Badge
                        variant="outline"
                        className="absolute left-1.5 top-1.5 text-[10px]"
                      >
                        {TYPE_LABEL[f.figure_type]}
                      </Badge>
                    </button>
                    <div className="space-y-1 p-2">
                      <p className="line-clamp-2 min-h-8 text-xs text-foreground" title={f.caption}>
                        {f.caption || '（无图注）'}
                      </p>
                      {f.tags.length > 0 && (
                        <p className="truncate text-[11px] text-muted-foreground">
                          {f.tags.join(' · ')}
                        </p>
                      )}
                      {f.score_note && (
                        <p className="truncate text-[11px] text-gold" title={f.score_note}>
                          打分回写：{f.score_note}
                        </p>
                      )}
                      <div className="flex items-center gap-1 pt-1">
                        {f.figure_type === 'structure' && typeof f.meta.smiles === 'string' && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-6 px-1.5 text-[11px]"
                            title="将该单体导入成膜打分页验证"
                            onClick={() => {
                              const s = encodeURIComponent(f.meta.smiles as string);
                              const role = f.meta.role === 'amine' ? 'b' : 'a';
                              navigate(`/toolbox/query?${role}=${s}`);
                            }}
                          >
                            <FlaskConical className="mr-1 h-3 w-3" />
                            打分验证
                          </Button>
                        )}
                        <a
                          href={figureFileUrl(f.fig_id)}
                          download
                          className="inline-flex h-6 items-center rounded border border-border px-1.5 text-[11px] text-muted-foreground hover:text-foreground"
                          title="下载原图"
                        >
                          <Download className="h-3 w-3" />
                        </a>
                        <button
                          type="button"
                          title="编辑标注"
                          className="ml-auto inline-flex h-6 items-center rounded border border-border px-1.5 text-[11px] text-muted-foreground hover:text-foreground"
                          onClick={() => {
                            setEditTarget(f);
                            setEditCaption(f.caption);
                            setEditTags(f.tags.join(', '));
                          }}
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          title="删除图谱"
                          className="inline-flex h-6 items-center rounded border border-border px-1.5 text-[11px] text-muted-foreground hover:text-destructive"
                          onClick={() => setDeleteTarget(f)}
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* 上传/生成弹窗 */}
        <Dialog open={uploadOpen} onOpenChange={(open) => !open && !busy && setUploadOpen(false)}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>上传图谱</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>图谱类型</Label>
                <Select
                  value={uploadType}
                  onValueChange={(v) => setUploadType(v as typeof uploadType)}
                >
                  <SelectTrigger aria-label="图谱类型">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="structure">结构图（分子结构式）</SelectItem>
                    <SelectItem value="spectra">光谱（PXRD/FTIR/荧光/NMR）</SelectItem>
                    <SelectItem value="mechanism">机理图（反应路径）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {uploadType === 'structure' && (
                <div className="space-y-1.5">
                  <Label htmlFor="fig-smiles">SMILES（输入则自动生成 2D 结构图，无需选文件）</Label>
                  <Input
                    id="fig-smiles"
                    value={smiles}
                    onChange={(e) => setSmiles(e.target.value)}
                    placeholder="O=Cc1ccc(-c2nc(...)n2)cc1"
                  />
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="fig-caption">图注</Label>
                <Input
                  id="fig-caption"
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                  placeholder="图 2a：TFPT 的荧光发射光谱"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="fig-tags">标签（逗号分隔）</Label>
                <Input
                  id="fig-tags"
                  value={tags}
                  onChange={(e) => setTags(e.target.value)}
                  placeholder="荧光, TFPT"
                />
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".png,.jpg,.jpeg,.svg,.webp"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = '';
                  if (f) void doUpload(f);
                }}
              />
            </div>
            <DialogFooter className="gap-2">
              {uploadType === 'structure' && smiles.trim() && (
                <Button
                  variant="outline"
                  onClick={() => void doFromSmiles()}
                  disabled={busy}
                >
                  {busy ? '生成中…' : '由 SMILES 生成'}
                </Button>
              )}
              <Button
                variant="outline"
                onClick={() => fileRef.current?.click()}
                disabled={busy || (uploadType === 'structure' && !smiles.trim())}
                title={uploadType === 'structure' && !smiles.trim()
                  ? '结构图请先填 SMILES（或选择「光谱/机理图」类型直接传图）'
                  : '选择图片文件（PNG/JPG/SVG/WebP，≤20MB）'}
              >
                {busy ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <ImagePlus className="mr-1.5 h-4 w-4" />
                )}
                选择图片上传
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 编辑标注弹窗 */}
        <Dialog open={editTarget !== null} onOpenChange={(open) => !open && setEditTarget(null)}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>编辑图谱标注</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-caption">图注</Label>
                <Input
                  id="edit-caption"
                  value={editCaption}
                  onChange={(e) => setEditCaption(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-tags">标签（逗号分隔）</Label>
                <Input
                  id="edit-tags"
                  value={editTags}
                  onChange={(e) => setEditTags(e.target.value)}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setEditTarget(null)}>
                取消
              </Button>
              <Button onClick={() => void saveEdit()}>保存</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 删除确认弹窗 */}
        <Dialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>确认删除该图谱？</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              将删除「{deleteTarget?.caption || '未命名图谱'}」。删除后不可恢复。
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteTarget(null)}>
                取消
              </Button>
              <Button
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={() => void doDelete()}
              >
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {/* v1.9.7：大图查看（Lightbox）——←/→ 切换、Esc 关闭、可直接改标注或下载 */}
        <Dialog open={zoomIndex !== null} onOpenChange={(open) => !open && setZoomIndex(null)}>
          <DialogContent className="max-w-4xl">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-base">
                <Badge variant="outline" className="text-[10px]">
                  {zoomFigure ? TYPE_LABEL[zoomFigure.figure_type] : ''}
                </Badge>
                <span className="line-clamp-1">
                  {zoomFigure?.caption || '（无图注）'}
                </span>
              </DialogTitle>
            </DialogHeader>
            {zoomFigure && (
              <div className="space-y-2">
                <div className="flex max-h-[70vh] items-center justify-center overflow-auto rounded-lg border border-border bg-white/70 p-2 dark:bg-black/30">
                  <img
                    src={figureFileUrl(zoomFigure.fig_id)}
                    alt={zoomFigure.caption || '文献图片'}
                    className="max-h-[66vh] max-w-full object-contain"
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>
                    {(zoomIndex ?? 0) + 1} / {shown.length}
                  </span>
                  {typeof zoomFigure.meta?.page === 'number' && (
                    <span>· 第 {String(zoomFigure.meta.page)} 页</span>
                  )}
                  {zoomFigure.tags.length > 0 && (
                    <span className="truncate">· {zoomFigure.tags.join(' · ')}</span>
                  )}
                </div>
              </div>
            )}
            <DialogFooter className="sm:justify-between">
              <span className="flex gap-2">
                <Button variant="outline" size="sm"
                        disabled={shown.length < 2}
                        onClick={() => stepZoom(-1)}>
                  上一张
                </Button>
                <Button variant="outline" size="sm"
                        disabled={shown.length < 2}
                        onClick={() => stepZoom(1)}>
                  下一张
                </Button>
              </span>
              <span className="flex gap-2">
                {zoomFigure && (
                  <>
                    <Button variant="outline" size="sm"
                            onClick={() => {
                              setEditTarget(zoomFigure);
                              setEditCaption(zoomFigure.caption);
                              setEditTags(zoomFigure.tags.join(', '));
                              setZoomIndex(null);
                            }}>
                      <Pencil className="mr-1 h-3 w-3" />改标注
                    </Button>
                    <a
                      href={figureFileUrl(zoomFigure.fig_id)}
                      download
                      className="inline-flex h-8 items-center rounded-md border border-input px-3 text-xs hover:bg-accent"
                    >
                      <Download className="mr-1 h-3 w-3" />下载原图
                    </a>
                  </>
                )}
              </span>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
