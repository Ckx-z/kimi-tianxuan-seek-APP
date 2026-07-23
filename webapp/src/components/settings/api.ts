/**
 * 「设置」页本地 API 辅助（不修改共享 @/lib/api）
 * 错误处理约定与 @/lib/api 一致：失败弹中文 toast 并抛出 Error。
 */
import { toast } from 'sonner';
import { BackendUnavailableError } from '@/lib/api';

const BASE = '/api';

async function request<T>(path: string, init?: RequestInit, silent = false): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
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
      /* 非 JSON 响应 */
    }
    if (!silent) toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as T;
}

/** LLM 设置（GET /api/llm/settings 响应，key 已掩码） */
export interface LlmSettings {
  configured: boolean;
  base_url: string;
  model: string;
  api_key_masked: string;
  source: string; // local_settings | env | longcat_seed | ''
}

/** 后端健康（GET /api/health 响应） */
export interface HealthInfo {
  status: string;
  tree_available?: boolean;
  gnn_available?: boolean;
  routing?: boolean;
}

export const fetchLlmSettings = () => request<LlmSettings>('/llm/settings');

export const saveLlmSettings = (body: { base_url: string; api_key: string; model: string }) =>
  request<{ saved: boolean; configured: boolean }>('/llm/settings', {
    method: 'PUT',
    body: JSON.stringify(body),
  });

export const testLlmConnection = () =>
  request<{ ok: boolean; message: string }>('/llm/test', { method: 'POST' });

/** 静默健康检查（不弹 toast，离线时抛 BackendUnavailableError） */
export const fetchHealth = () => request<HealthInfo>('/health', undefined, true);
