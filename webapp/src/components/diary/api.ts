/**
 * 科研日记页本地 API 辅助（不修改共享 @/lib/api）
 * 后端：/api/diary（列表 / 创建 / 更新 / 删除 / 按实验记录反查）。
 * 错误处理约定与 @/components/records/api 一致：失败弹中文 toast 并抛出 Error。
 */
import { toast } from 'sonner';
import { BackendUnavailableError } from '@/components/records/api';

/** 后端不可用时抛出此错误，调用方据此做降级展示 */
export { BackendUnavailableError };

/** @ 关联的实验记录摘要（由后端按 mentions 回查实验记录得到） */
export interface MentionDetail {
  record_id: string;
  /** 该实验记录当前是否仍存在（被删过则为 false，前端显示「已删除」） */
  exists: boolean;
  experiment_no?: string;
  /** 单体对标签（醛 + 胺），即「标题」列展示内容 */
  label?: string;
  outcome?: string;
  date?: string;
}

/** 日记条目（后端落盘结构） */
export interface DiaryEntry {
  entry_id: string;
  /** 日记日期 YYYY-MM-DD */
  date: string;
  title: string;
  /** Markdown 原文 */
  content: string;
  /** @ 的实验记录 id（结构化关联，不是正文里的文本） */
  mentions: string[];
  mention_details?: MentionDetail[];
  created_at?: string;
  updated_at?: string;
}

/** 列表查询参数（全部可选，可组合） */
export interface DiaryQuery {
  keyword?: string;
  date_from?: string;
  date_to?: string;
  record_id?: string;
  limit?: number;
}

/** 创建请求体 */
export interface DiaryCreateBody {
  date?: string;
  title?: string;
  content: string;
  mentions?: string[];
}

/** 更新请求体（只传要改的字段；mentions 传 [] 表示清空关联） */
export interface DiaryUpdateBody {
  date?: string;
  title?: string;
  content?: string;
  mentions?: string[];
}

/** 统一请求封装（相对路径 /api 走 vite 代理或 FastAPI 静态托管） */
async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; silent?: boolean } = {},
): Promise<T> {
  const { method = 'GET', body, silent = false } = options;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    const err = new BackendUnavailableError();
    if (!silent) toast.error(err.message);
    throw err;
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      // 非 JSON 响应，保留默认提示
    }
    if (!silent) toast.error(message);
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** 查询串（空值不参与，避免后端把空串当过滤条件） */
function toQuery(params: DiaryQuery | undefined): string {
  const search = new URLSearchParams();
  if (params?.keyword) search.set('keyword', params.keyword);
  if (params?.date_from) search.set('date_from', params.date_from);
  if (params?.date_to) search.set('date_to', params.date_to);
  if (params?.record_id) search.set('record_id', params.record_id);
  if (params?.limit) search.set('limit', String(params.limit));
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

/** 日记列表（按日期倒序） */
export async function listDiary(params?: DiaryQuery): Promise<DiaryEntry[]> {
  const data = await request<{ entries: DiaryEntry[] }>(`/diary${toQuery(params)}`);
  return data.entries ?? [];
}

/** 新建日记 */
export function createDiary(body: DiaryCreateBody): Promise<DiaryEntry> {
  return request<DiaryEntry>('/diary', { method: 'POST', body });
}

/** 更新日记（局部字段） */
export function updateDiary(
  entryId: string,
  body: DiaryUpdateBody,
): Promise<DiaryEntry> {
  return request<DiaryEntry>(`/diary/${encodeURIComponent(entryId)}`, {
    method: 'PUT',
    body,
  });
}

/** 删除日记 */
export function deleteDiary(entryId: string): Promise<void> {
  return request<void>(`/diary/${encodeURIComponent(entryId)}`, { method: 'DELETE' });
}

/** 按实验记录反查：所有 @ 了该记录的日记 */
export async function listDiaryByRecord(
  recordId: string,
  limit?: number,
): Promise<DiaryEntry[]> {
  const qs = limit ? `?limit=${limit}` : '';
  const data = await request<{ entries: DiaryEntry[] }>(
    `/diary/by-record/${encodeURIComponent(recordId)}${qs}`,
  );
  return data.entries ?? [];
}

/** 关联记录的展示标签：优先「实验编号」，其次 record_id */
export function mentionLabel(detail: MentionDetail | undefined, fallbackId: string): string {
  if (!detail) return fallbackId;
  const no = detail.experiment_no?.trim();
  return no ? no : detail.record_id || fallbackId;
}
