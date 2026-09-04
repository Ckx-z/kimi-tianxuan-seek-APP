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
  /** 后端版本号（api/__init__.py 的 __version__，旧后端无此字段） */
  version?: string;
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

/** 联网搜索配置（v1.6.0 P0；GET /api/llm/search-settings 响应） */
export interface WebSearchSettings {
  enabled: boolean;
  provider: string;
  api_key_masked: string;
  configured: boolean;
  available: boolean;
  reason: string;
}

export const fetchSearchSettings = () =>
  request<WebSearchSettings>('/llm/search-settings');

export const saveSearchSettings = (body: {
  enabled: boolean;
  provider: string;
  api_key: string;
}) =>
  request<WebSearchSettings>('/llm/search-settings', {
    method: 'PUT',
    body: JSON.stringify(body),
  });

export const testLlmConnection = () =>
  request<{ ok: boolean; message: string }>('/llm/test', { method: 'POST' });

/** 静默健康检查（不弹 toast，离线时抛 BackendUnavailableError） */
export const fetchHealth = () => request<HealthInfo>('/health', undefined, true);

/** 助手记忆（GET /api/assistant/memory 响应） */
export interface AssistantMemoryInfo {
  enabled: boolean;
  content: string;
  entries: number;
}

export const fetchAssistantMemory = () => request<AssistantMemoryInfo>('/assistant/memory');

export const updateAssistantMemory = (body: { enabled?: boolean; content?: string }) =>
  request<AssistantMemoryInfo>('/assistant/memory', {
    method: 'PUT',
    body: JSON.stringify(body),
  });

export const clearAssistantMemory = () =>
  request<{ cleared: boolean } & AssistantMemoryInfo>('/assistant/memory', { method: 'DELETE' });

/** 按单体组记忆（v1.6.0 P2）清单条目 */
export interface PairMemoryMeta {
  key: string;
  label: string;
  updated_at: string;
  entries: number;
}

export const fetchPairMemories = () =>
  request<{ memories: PairMemoryMeta[]; count: number }>('/assistant/pair-memories');

export const deletePairMemory = (key: string) =>
  request<{ deleted: boolean; key: string }>(
    `/assistant/pair-memories/${encodeURIComponent(key)}`,
    { method: 'DELETE' },
  );

/** 技能（v1.6.0 P2）条目 */
export interface AssistantSkill {
  name: string;
  description: string;
  enabled: boolean;
  source: 'user' | 'builtin';
}

export const fetchSkills = () =>
  request<{ skills: AssistantSkill[]; count: number }>('/assistant/skills');

export const setSkillEnabled = (name: string, enabled: boolean) =>
  request<{ saved: boolean; name: string; skills: AssistantSkill[] }>(
    `/assistant/skills/${encodeURIComponent(name)}`,
    { method: 'PUT', body: JSON.stringify({ enabled }) },
  );
