/**
 * 单体性质卡：GET /api/monomers/props 结果展示（facts 表格 + narrative 中文解读）
 * 加载骨架屏、错误态、空态齐全。
 */
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import type { MonomerProps } from './api';

/** facts 键 → 中文标签 */
const FACT_LABELS: Record<string, string> = {
  mw: '分子量 (MW)',
  xlogp: '脂水分配系数 (XLogP)',
  tpsa: '极性表面积 (TPSA)',
  hbd: '氢键供体数 (HBD)',
  hba: '氢键受体数 (HBA)',
  aromatic_rings: '芳香环数',
  f_count: '氟原子数',
  rotatable_bonds: '可旋转键数',
};

interface Props {
  title: string; // 如「醛单体性质」
  name?: string;
  loading: boolean;
  error: string | null;
  props: MonomerProps | null;
}

export default function MonomerPropsCard({ title, name, loading, error, props }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {title}
          {name && <span className="ml-2 text-sm font-normal text-muted-foreground">{name}</span>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* 加载骨架屏 */}
        {loading && (
          <div className="space-y-2">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-3/4" />
            <Skeleton className="h-16 w-full" />
          </div>
        )}

        {/* 错误态 */}
        {!loading && error && (
          <div className="rounded-lg border border-dashed border-red-300 p-4 text-sm text-red-600 dark:text-red-400">
            性质卡加载失败：{error}
          </div>
        )}

        {/* 空态 */}
        {!loading && !error && !props && (
          <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            打分后自动加载
          </div>
        )}

        {/* 正常内容 */}
        {!loading && !error && props && (
          <div className="space-y-3">
            {/* facts 表格 */}
            {Object.keys(props.facts).length > 0 ? (
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(props.facts).map(([k, v]) => (
                    <tr key={k} className="border-b last:border-0">
                      <td className="py-1.5 text-muted-foreground">{FACT_LABELS[k] ?? k}</td>
                      <td className="py-1.5 text-right font-medium text-foreground">{String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-sm text-muted-foreground">RDKit 未返回结构事实（SMILES 可能无法解析）</p>
            )}

            {/* narrative 中文解读 */}
            {props.narrative ? (
              <div className="rounded-lg bg-gold-muted p-3 text-sm leading-relaxed text-foreground">
                {props.narrative}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">暂无 LLM 解读（未配置或生成失败）</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
