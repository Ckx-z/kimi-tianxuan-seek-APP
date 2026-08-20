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
