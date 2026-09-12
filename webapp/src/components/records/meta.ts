/**
 * 实验记录共享展示常量（时间线 / 详情对话框 / 收藏详情内嵌列表共用）
 */
import type { RecordItem } from './api';

/** conditions 九键中文名 */
export const CONDITION_LABELS: Record<string, string> = {
  solvent_1: '溶剂一',
  solvent_2: '溶剂二',
  eluent: '洗脱剂',
  modulator: '调制剂',
  catalyst: '催化剂',
  temperature_c: '温度（℃）',
  time_days: '时间（天）',
  vessel: '容器',
  addition_order: '加料顺序',
};

/** 结果徽章配置：成膜紫 / 部分金 / 失败灰 / 未定（草稿留空） */
export const OUTCOME_META: Record<string, { label: string; className: string }> = {
  film: { label: '成膜', className: 'bg-primary text-primary-foreground' },
  partial: { label: '部分成膜', className: 'bg-gold text-gold-foreground' },
  failed: { label: '失败', className: 'bg-muted text-muted-foreground' },
  '': { label: '未定', className: 'bg-muted text-muted-foreground' },
};

/** 单体对显示名 */
export function pairLabel(rec: Pick<RecordItem, 'aldehyde' | 'amine'>): string {
  const ald = rec.aldehyde?.name || rec.aldehyde?.smiles?.slice(0, 16) || '未知醛';
  const amine = rec.amine?.name || rec.amine?.smiles?.slice(0, 16) || '未知胺';
  return `${ald} + ${amine}`;
}

/**
 * 实验时间（v1.9.3 问题 5）：统一取 `experiment_date`（实验过程时间线第一个
 * 时间点），接口未返回该派生字段时回退 `date`（录入日期）。
 *
 * 参数用宽松结构（unknown 取值 + String 归一），以便同时适配
 * `RecordItem`（components/records/api）与 `ExperimentRecord`（@/types）。
 */
export function experimentTime(rec: {
  experiment_date?: unknown;
  date?: unknown;
  date_source?: unknown;
}): { date: string; isFallback: boolean; hint: string } {
  const exp = String(rec.experiment_date ?? '').trim();
  const created = String(rec.date ?? '').trim();
  const date = exp || created || '—';
  const isFallback = !exp || rec.date_source === 'created';
  return {
    date,
    isFallback,
    hint: isFallback
      ? '时间线无可解析时间点，显示录入日期'
      : '实验过程时间线首个时间点',
  };
}
