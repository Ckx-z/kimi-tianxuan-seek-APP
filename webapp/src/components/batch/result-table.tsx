/**
 * 结果表格：排名 / 醛 / 胺 / 主分（紫金渐变条）/ 树分 / GNN 分 / OOD 状态
 * - 点表头可排序（升/降切换），OOD=out 行始终沉底并灰显 ⛔
 */
import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { fmtScore, type BatchResultItem } from './api';

type SortKey = 'score' | 'tree_score' | 'gnn_score';
type SortDir = 'asc' | 'desc';

/** OOD 状态徽标 */
function OodBadge({ level }: { level: string }) {
  if (level === 'out')
    return <Badge variant="destructive">⛔ 分布外</Badge>;
  if (level === 'warn')
    return (
      <Badge variant="outline" className="border-gold/50 text-gold">
        ⚠ 边界
      </Badge>
    );
  return (
    <Badge variant="outline" className="border-primary/40 text-primary">
      ✓ 分布内
    </Badge>
  );
}

/** 可排序表头单元格 */
function SortableHead({
  label,
  sortKey,
  currentKey,
  dir,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  dir: SortDir;
  onSort: (k: SortKey) => void;
  className?: string;
}) {
  const active = currentKey === sortKey;
  return (
    <TableHead className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          'inline-flex items-center gap-1 hover:text-foreground',
          active ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        {label}
        {active ? (
          dir === 'desc' ? <ArrowDown className="h-3.5 w-3.5" /> : <ArrowUp className="h-3.5 w-3.5" />
        ) : (
          <ArrowUpDown className="h-3.5 w-3.5 opacity-50" />
        )}
      </button>
    </TableHead>
  );
}

export default function ResultTable({ results }: { results: BatchResultItem[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('score');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const onSort = (k: SortKey) => {
    if (k === sortKey) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(k);
      setSortDir('desc');
    }
  };

  // 排序：OOD=out（score 为 null）始终沉底；其余按所选列排序
  const sorted = useMemo(() => {
    const inRows = results.filter((r) => r.ood?.level !== 'out');
    const outRows = results.filter((r) => r.ood?.level === 'out');
    inRows.sort((a, b) => {
      const va = (a[sortKey] as number | null) ?? -Infinity;
      const vb = (b[sortKey] as number | null) ?? -Infinity;
      return sortDir === 'desc' ? vb - va : va - vb;
    });
    return [...inRows, ...outRows];
  }, [results, sortKey, sortDir]);

  const maxScore = useMemo(
    () => Math.max(...sorted.map((r) => r.score ?? 0), 0.0001),
    [sorted],
  );

  if (results.length === 0) return null;

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">排名</TableHead>
            <TableHead>醛 SMILES</TableHead>
            <TableHead>胺 SMILES</TableHead>
            <SortableHead
              label="主分"
              sortKey="score"
              currentKey={sortKey}
              dir={sortDir}
              onSort={onSort}
              className="min-w-40"
            />
            <SortableHead label="树分" sortKey="tree_score" currentKey={sortKey} dir={sortDir} onSort={onSort} />
            <SortableHead label="GNN 分" sortKey="gnn_score" currentKey={sortKey} dir={sortDir} onSort={onSort} />
            <TableHead>OOD</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((r, i) => {
            const isOut = r.ood?.level === 'out';
            return (
              <TableRow key={`${r.ald_smiles}-${r.amine_smiles}-${i}`} className={cn(isOut && 'opacity-45')}>
                <TableCell className="font-medium text-muted-foreground">
                  {isOut ? '⛔' : i + 1}
                </TableCell>
                <TableCell className="max-w-52">
                  <span className="block truncate font-mono text-xs" title={r.ald_smiles}>
                    {r.ald_smiles}
                  </span>
                </TableCell>
                <TableCell className="max-w-52">
                  <span className="block truncate font-mono text-xs" title={r.amine_smiles}>
                    {r.amine_smiles}
                  </span>
                </TableCell>
                <TableCell>
                  {r.score === null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <div className="flex items-center gap-2">
                      {/* 紫金渐变条形可视化 */}
                      <div className="h-2 w-24 overflow-hidden rounded-full bg-muted">
                        <div
                          className="gradient-royal h-full rounded-full"
                          style={{ width: `${Math.max(4, ((r.score ?? 0) / maxScore) * 100)}%` }}
                        />
                      </div>
                      <span className="text-sm font-semibold text-foreground">{fmtScore(r.score)}</span>
                    </div>
                  )}
                </TableCell>
                <TableCell className="text-sm">{fmtScore(r.tree_score)}</TableCell>
                <TableCell className="text-sm">{fmtScore(r.gnn_score)}</TableCell>
                <TableCell>
                  <OodBadge level={r.ood?.level ?? 'in'} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
