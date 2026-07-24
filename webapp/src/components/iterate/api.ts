/**
 * 方案迭代页本地 API 辅助（共享 api.ts 缺失的端点在此补齐）
 * 约定与 @/lib/api 一致：失败弹中文 toast 并抛出 Error；网络层失败视为后端未连接。
 */
import { toast } from 'sonner';
import type { ExperimentRecord, Favorite, Plan, Suggestion } from '@/types';

const BASE_URL = '/api';

/** 后端未连接错误（页面据此显示降级提示而非白屏） */
export class BackendUnavailableError extends Error {
  constructor(message = '后端未连接，请确认 FastAPI 服务已启动（http://localhost:8000）') {
    super(message);
    this.name = 'BackendUnavailableError';
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  silent?: boolean;
  /** 慢请求传入 AbortSignal，路由切换时可中止 */
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, silent = false, signal } = options;
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    const err = new BackendUnavailableError();
    if (!silent) toast.error(err.message);
    throw err;
  }

  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
      else if (typeof data?.message === 'string') message = data.message;
    } catch {
      // 响应体非 JSON，保留默认提示
    }
    if (!silent) toast.error(message);
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------- 收藏 / 实验记录（列表为包裹结构 {items: [...]}） ----------
interface FavoritesResp { favorites?: Favorite[] }
interface RecordsResp { records?: ExperimentRecord[] }
interface SuggestionsResp { suggestions?: Suggestion[] }
interface PlansResp { plans?: Plan[] }

/** 收藏下拉（返回数组，容错后端包裹结构） */
export async function listFavorites(signal?: AbortSignal): Promise<Favorite[]> {
  const data = await request<FavoritesResp | Favorite[]>('/favorites', { signal });
  return Array.isArray(data) ? data : (data.favorites ?? []);
}

/** 指定收藏下的实验记录（锚定用） */
export async function listRecords(favoriteId: string, signal?: AbortSignal): Promise<ExperimentRecord[]> {
  const q = favoriteId ? `?favorite_id=${encodeURIComponent(favoriteId)}` : '';
  const data = await request<RecordsResp | ExperimentRecord[]>(`/records${q}`, { signal });
  return Array.isArray(data) ? data : (data.records ?? []);
}

// ---------- 方案迭代 ----------
/** 生成迭代建议（慢请求，最长 5 分钟，务必传 signal） */
export function suggestIterate(
  payload: { question: string; favorite_id?: string; record_id?: string },
  signal?: AbortSignal,
): Promise<{ written: string[]; count: number; batch: string | null }> {
  return request('/iterate/suggest', { method: 'POST', body: payload, signal });
}

/** 建议列表（可选按收藏单体组过滤：每组只显示本组的迭代建议） */
export async function listSuggestions(favoriteId?: string, signal?: AbortSignal): Promise<Suggestion[]> {
  const q = favoriteId ? `?favorite_id=${encodeURIComponent(favoriteId)}` : '';
  const data = await request<SuggestionsResp | Suggestion[]>(`/iterate/suggestions${q}`, { signal });
  return Array.isArray(data) ? data : (data.suggestions ?? []);
}

/** 采纳建议 → 生成方案 */
export function adoptSuggestion(suggestionId: string, signal?: AbortSignal): Promise<Plan> {
  return request<Plan>('/iterate/adopt', {
    method: 'POST',
    body: { suggestion_id: suggestionId },
    signal,
  });
}

/** 已生成方案列表 */
export async function listPlans(signal?: AbortSignal): Promise<Plan[]> {
  const data = await request<PlansResp | Plan[]>('/iterate/plans', { signal });
  return Array.isArray(data) ? data : (data.plans ?? []);
}
