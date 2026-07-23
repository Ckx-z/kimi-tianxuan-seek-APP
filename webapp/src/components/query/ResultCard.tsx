/**
 * 打分结果大卡：主分数 + 树/GNN 分量小卡 + OOD 横幅 + tree_route 路由说明
 */
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import type { PredictResult } from './api';

interface Props {
  result: PredictResult | null;
  loading: boolean;
}

/** OOD 横幅：out 红色 / 其他非 in 黄色 */
function OodBanner({ ood }: { ood: PredictResult['ood'] }) {
  // 后端正常级别为 "in" 或 "none"，其余按警告/不适用处理
  if (!ood || ood.level === 'in' || ood.level === 'none') return null;
  const isOut = ood.level === 'out';
  return (
    <div
      className={
        isOut
          ? 'rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300'
          : 'rounded-lg border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-800 dark:border-yellow-900 dark:bg-yellow-950/40 dark:text-yellow-300'
      }
    >
      <div className="font-semibold">
        {isOut ? '⛔ 分布外（OOD=out）：模型不适用' : '⚠️ 分布外警告（OOD=warning）：结果可信度较低'}
      </div>
      {ood.reasons?.length > 0 && (
        <ul className="mt-1 list-inside list-disc space-y-0.5">
          {ood.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 分量小卡（树 / GNN） */
function SubScoreCard({ label, score, std }: { label: string; score: number | null; std: number | null }) {
  return (
    <div className="rounded-lg border bg-card p-3 text-center">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold text-foreground">
        {score == null ? '—' : score.toFixed(3)}
        {score != null && std != null && (
          <span className="ml-1 text-sm font-normal text-muted-foreground">±{std.toFixed(3)}</span>
        )}
      </div>
    </div>
  );
}

export default function ResultCard({ result, loading }: Props) {
  // 加载态
  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>打分结果</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <div className="grid grid-cols-2 gap-3">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        </CardContent>
      </Card>
    );
  }

  // 空态
  if (!result) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>打分结果</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border border-dashed p-10 text-center text-muted-foreground">
            输入醛 / 胺单体后点击「开始打分」，结果将显示在这里
          </div>
        </CardContent>
      </Card>
    );
  }

  const noScore = result.score == null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>打分结果</span>
          {result.score_policy === 'max_tree_gnn' && (
            <span className="text-xs font-normal text-muted-foreground">
              取分策略 score_policy=max_tree_gnn（两模型较高值）
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <OodBanner ood={result.ood} />

        {/* 主分数 */}
        <div className="rounded-xl border border-gold/50 bg-gold-muted p-6 text-center">
          {noScore ? (
            <>
              <div className="text-3xl font-bold text-foreground">⛔ 模型不适用</div>
              {result.ood?.reasons?.length > 0 && (
                <p className="mt-2 text-sm text-muted-foreground">{result.ood.reasons.join('；')}</p>
              )}
            </>
          ) : (
            <>
              <div className="text-sm text-muted-foreground">成膜评分（越高越好）</div>
              <div className="mt-1 text-5xl font-bold text-primary">{result.score!.toFixed(3)}</div>
            </>
          )}
        </div>

        {/* 树 / GNN 分量 */}
        <div className="grid grid-cols-2 gap-3">
          <SubScoreCard label="树模型分量" score={result.tree_score} std={result.tree_std} />
          <SubScoreCard label="GNN 分量" score={result.gnn_score} std={result.gnn_std} />
        </div>

        {/* tree_route 路由说明 */}
        {result.tree_route && (
          <p className="text-xs text-muted-foreground">
            树模型路由：{result.tree_route}
            {result.tree_model_name ? `（${result.tree_model_name}）` : ''}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
