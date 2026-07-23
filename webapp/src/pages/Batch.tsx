/**
 * 批量排序页（任务B）
 * ① 批量输入：内置库多选（醛 N × 胺 M 笛卡尔积，上限 20 对）+ 粘贴文本解析预览
 * ② 批量打分：POST /api/predict/batch，分批评测显示「正在预测 X/Y」，结果可排序表格
 * ③ 导出 CSV（前端 Blob 下载）；④ 错误行警示区；⑤ 后端未连接降级提示
 */
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Download, FlaskConical, Loader2, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import MonomerPicker from '@/components/batch/monomer-picker';
import ResultTable from '@/components/batch/result-table';
import {
  BackendUnavailableError,
  dedupePairs,
  fetchMonomers,
  parsePastedPairs,
  predictBatch,
  type BatchErrorItem,
  type BatchResultItem,
  type MonomerLibrary,
  type PairInput,
} from '@/components/batch/api';

/** 单次批量预测上限（对） */
const MAX_PAIRS = 20;
/** 分批大小：每批 4 对调一次批量接口，兼顾进度反馈与稳健性 */
const CHUNK_SIZE = 4;

export default function Batch() {
  // ---------- 内置库 ----------
  const [library, setLibrary] = useState<MonomerLibrary | null>(null);
  const [libLoading, setLibLoading] = useState(true);
  const [backendDown, setBackendDown] = useState(false);
  const [selectedAld, setSelectedAld] = useState<Set<string>>(new Set());
  const [selectedAmine, setSelectedAmine] = useState<Set<string>>(new Set());

  // ---------- 粘贴文本 ----------
  const [pasteText, setPasteText] = useState('');

  // ---------- 预测状态 ----------
  const [predicting, setPredicting] = useState(false);
  const [doneCount, setDoneCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [results, setResults] = useState<BatchResultItem[]>([]);
  const [errors, setErrors] = useState<BatchErrorItem[]>([]);

  // 加载内置单体库（失败时降级：仍可使用粘贴模式）
  const loadLibrary = async () => {
    setLibLoading(true);
    try {
      const lib = await fetchMonomers();
      setLibrary(lib);
      setBackendDown(false);
    } catch (e) {
      if (e instanceof BackendUnavailableError) setBackendDown(true);
      setLibrary(null);
    } finally {
      setLibLoading(false);
    }
  };
  useEffect(() => {
    void loadLibrary();
  }, []);

  const toggleIn = (set: Set<string>, v: string): Set<string> => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    return next;
  };

  // ---------- 组合生成 ----------
  // 库模式：醛 N × 胺 M 笛卡尔积（附带名称便于预览）
  const libraryPairs = useMemo<PairInput[]>(() => {
    if (!library) return [];
    const alds = library.aldehydes.filter((m) => selectedAld.has(m.smiles));
    const amines = library.amines.filter((m) => selectedAmine.has(m.smiles));
    const out: PairInput[] = [];
    for (const a of alds)
      for (const b of amines)
        out.push({ ald_smiles: a.smiles, amine_smiles: b.smiles, ald_name: a.name, amine_name: b.name });
    return out;
  }, [library, selectedAld, selectedAmine]);

  // 粘贴模式：实时解析预览
  const parsed = useMemo(() => parsePastedPairs(pasteText), [pasteText]);

  // 合并两种来源并去重，截断到上限
  const allPairs = useMemo(() => dedupePairs([...libraryPairs, ...parsed.pairs]), [libraryPairs, parsed.pairs]);
  const overLimit = allPairs.length > MAX_PAIRS;
  const pairs = allPairs.slice(0, MAX_PAIRS);

  // ---------- 批量打分（分批评测，显示进度） ----------
  const runPredict = async () => {
    if (pairs.length === 0) {
      toast.warning('请先选择或粘贴至少一对醛-胺组合');
      return;
    }
    if (overLimit) toast.warning(`超过 ${MAX_PAIRS} 对上限，仅预测前 ${MAX_PAIRS} 对`);
    setPredicting(true);
    setResults([]);
    setErrors([]);
    setDoneCount(0);
    setTotalCount(pairs.length);
    const accResults: BatchResultItem[] = [];
    const accErrors: BatchErrorItem[] = [];
    try {
      for (let i = 0; i < pairs.length; i += CHUNK_SIZE) {
        const chunk = pairs.slice(i, i + CHUNK_SIZE);
        try {
          const resp = await predictBatch(chunk);
          accResults.push(...resp.results);
          // 错误行 index 是本批内的，换算回全局序号
          accErrors.push(...resp.errors.map((e) => ({ index: i + e.index, error: e.error })));
        } catch (e) {
          // 整批失败（如后端掉线）：该批全部记入错误区，继续下一批无意义则中止
          if (e instanceof BackendUnavailableError) {
            setBackendDown(true);
            break;
          }
          chunk.forEach((_, j) => accErrors.push({ index: i + j, error: (e as Error).message }));
        }
        setDoneCount(Math.min(i + CHUNK_SIZE, pairs.length));
      }
      // 排序：有分者降序在前，OOD=out（score 为 null）沉底
      accResults.sort((a, b) => (a.score === null ? 1 : 0) - (b.score === null ? 1 : 0) || (b.score ?? 0) - (a.score ?? 0));
      setResults(accResults);
      setErrors(accErrors);
      if (accResults.length > 0) toast.success(`预测完成：${accResults.length} 对成功${accErrors.length ? `，${accErrors.length} 对失败` : ''}`);
      else if (accErrors.length > 0) toast.error('全部预测失败，请查看错误详情');
    } finally {
      setPredicting(false);
    }
  };

  // ---------- 导出 CSV ----------
  const exportCsv = () => {
    if (results.length === 0) return;
    const header = ['排名', '醛SMILES', '胺SMILES', '主分', '树分', 'GNN分', 'OOD状态', 'OOD原因'];
    const esc = (s: string) => `"${s.replace(/"/g, '""')}"`;
    const lines = results.map((r, i) =>
      [
        r.score === null ? '⛔' : String(i + 1),
        esc(r.ald_smiles),
        esc(r.amine_smiles),
        r.score ?? '',
        r.tree_score ?? '',
        r.gnn_score ?? '',
        r.ood?.level ?? '',
        esc((r.ood?.reasons ?? []).join('; ')),
      ].join(','),
    );
    const csv = '﻿' + [header.join(','), ...lines].join('\n'); // BOM 保证 Excel 中文不乱码
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `批量排序结果_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success('CSV 已导出');
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">批量排序</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          从内置库多选或粘贴 SMILES 对，批量预测成膜评分并排序（上限 {MAX_PAIRS} 对）
        </p>
      </div>

      {/* 后端降级提示：不白屏，仍可用粘贴模式查看界面 */}
      {backendDown && (
        <Alert variant="destructive">
          <AlertTitle>后端未连接</AlertTitle>
          <AlertDescription className="flex items-center justify-between gap-4">
            <span>无法访问 http://localhost:8000，内置库与批量打分暂不可用。</span>
            <Button size="sm" variant="outline" onClick={() => void loadLibrary()}>
              <RefreshCw className="mr-1 h-3.5 w-3.5" /> 重试
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {/* 输入区 */}
      <Card>
        <CardHeader>
          <CardTitle>批量输入</CardTitle>
          <CardDescription>两种方式可叠加使用，自动去重</CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="library">
            <TabsList>
              <TabsTrigger value="library">内置库多选</TabsTrigger>
              <TabsTrigger value="paste">粘贴文本</TabsTrigger>
            </TabsList>

            <TabsContent value="library" className="mt-4">
              {libLoading ? (
                <div className="flex h-40 items-center justify-center text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 正在加载单体库…
                </div>
              ) : library ? (
                <MonomerPicker
                  aldehydes={library.aldehydes}
                  amines={library.amines}
                  selectedAld={selectedAld}
                  selectedAmine={selectedAmine}
                  onToggleAld={(s) => setSelectedAld((prev) => toggleIn(prev, s))}
                  onToggleAmine={(s) => setSelectedAmine((prev) => toggleIn(prev, s))}
                />
              ) : (
                <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                  单体库加载失败（后端未连接）
                </div>
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                醛 {selectedAld.size} × 胺 {selectedAmine.size} = {libraryPairs.length} 对（笛卡尔积）
              </p>
            </TabsContent>

            <TabsContent value="paste" className="mt-4 space-y-2">
              <Textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                rows={6}
                placeholder={'每行一对：醛SMILES, 胺SMILES（逗号或空格分隔）\n例如：\nO=Cc1ccccc1, Nc1ccccc1'}
                className="font-mono text-xs"
              />
              <p className="text-xs text-muted-foreground">
                解析出 {parsed.pairs.length} 对
                {parsed.badLines.length > 0 && (
                  <span className="text-destructive">；第 {parsed.badLines.join('、')} 行无法解析</span>
                )}
              </p>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* 组合预览 + 打分按钮 */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2">
              待预测组合
              <Badge variant={overLimit ? 'destructive' : 'secondary'}>
                {allPairs.length} 对{overLimit && `（超出上限，仅取前 ${MAX_PAIRS}）`}
              </Badge>
            </CardTitle>
            <CardDescription>库选择与粘贴文本合并去重后的最终列表</CardDescription>
          </div>
          <Button onClick={() => void runPredict()} disabled={predicting || pairs.length === 0}>
            {predicting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <FlaskConical className="mr-2 h-4 w-4" />
            )}
            {predicting ? '预测中…' : '批量打分'}
          </Button>
        </CardHeader>
        <CardContent>
          {pairs.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              暂无组合 —— 请在上方选择单体或粘贴 SMILES 对
            </div>
          ) : (
            <ScrollArea className="h-40 rounded-lg border border-border">
              <ol className="divide-y divide-border text-xs">
                {pairs.map((p, i) => (
                  <li key={`${p.ald_smiles}-${p.amine_smiles}`} className="flex items-center gap-2 px-3 py-1.5">
                    <span className="w-6 shrink-0 text-muted-foreground">{i + 1}.</span>
                    <span className="truncate font-mono" title={p.ald_smiles}>
                      {p.ald_name ? `${p.ald_name}（${p.ald_smiles}）` : p.ald_smiles}
                    </span>
                    <span className="shrink-0 text-gold">×</span>
                    <span className="truncate font-mono" title={p.amine_smiles}>
                      {p.amine_name ? `${p.amine_name}（${p.amine_smiles}）` : p.amine_smiles}
                    </span>
                  </li>
                ))}
              </ol>
            </ScrollArea>
          )}

          {/* 进度态 */}
          {predicting && (
            <div className="mt-4 space-y-1.5">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>正在预测 {Math.min(doneCount + 1, totalCount)}/{totalCount}（每批 {CHUNK_SIZE} 对，可能耗时 1–2 分钟）</span>
                <span>{Math.round((doneCount / totalCount) * 100)}%</span>
              </div>
              <Progress value={(doneCount / totalCount) * 100} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* 错误行警示区 */}
      {errors.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>⚠ {errors.length} 对预测失败</AlertTitle>
          <AlertDescription>
            <ul className="mt-1 max-h-32 space-y-1 overflow-y-auto text-xs">
              {errors.map((e, i) => (
                <li key={i}>
                  第 {e.index + 1} 对：{e.error}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {/* 结果区 */}
      {results.length > 0 ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">
              排序结果 <span className="text-sm font-normal text-muted-foreground">（{results.length} 对，点表头可排序）</span>
            </h2>
            <Button variant="outline" size="sm" onClick={exportCsv}>
              <Download className="mr-1.5 h-4 w-4" /> 导出 CSV
            </Button>
          </div>
          <ResultTable results={results} />
        </div>
      ) : (
        !predicting && (
          <div className="rounded-xl border border-dashed border-border bg-card p-10 text-center text-sm text-muted-foreground">
            尚无结果 —— 点击「批量打分」后在此查看排名
          </div>
        )
      )}
    </div>
  );
}
