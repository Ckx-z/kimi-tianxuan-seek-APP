/**
 * 科研助手 API 层（严格按前后端契约实现，不得偏离）
 *
 * 契约：
 * - GET  /api/assistant/status           → { enabled, reason }
 * - POST /api/assistant/sessions         → { session_id, title }
 * - GET  /api/assistant/sessions         → { sessions: [...] }
 * - GET  /api/assistant/sessions/{id}    → { session_id, title, context, messages }
 * - POST /api/assistant/uploads          → 附件元信息（multipart，单文件 ≤10MB）
 * - POST /api/assistant/chat (stream)    → SSE，每行 data 为 JSON 事件
 *   （attachments 字段携带 upload_id 列表，≤3 个）
 * - GET  /api/assistant/daily-brief?date=YYYY-MM-DD → 今日科研日报（V2.2）
 * - GET  /api/assistant/nudges           → 连续失败提醒列表（V2.2）
 * - POST /api/assistant/nudges/dismiss   → 登记"知道了"（当日不再提醒）
 *
 * Mock 开关：VITE_ASSISTANT_MOCK=1（或 true）时走本地 mock（见 ./mock.ts），
 * 用于后端未就绪前的自测与日后联调，默认关闭。
 */
import { mockApi } from './mock';

const BASE_URL = '/api/assistant';

/** 是否启用本地 mock（VITE_ASSISTANT_MOCK=1/true，默认关） */
export const ASSISTANT_MOCK =
  import.meta.env.VITE_ASSISTANT_MOCK === '1' ||
  import.meta.env.VITE_ASSISTANT_MOCK === 'true';

// ---------- 契约类型 ----------
export interface AssistantStatus {
  enabled: boolean;
  reason?: string;
}

export interface AssistantSessionMeta {
  session_id: string;
  title: string;
  updated_at: string;
  message_count: number;
}

/** SSE / 历史消息中的工具事件 */
export interface ToolEvent {
  type: 'tool_call' | 'tool_result' | 'tool_confirm';
  name: string;
  args?: Record<string, unknown>;
  summary?: string;
  is_error?: boolean;
  /** tool_confirm：二次确认令牌（一次性、5 分钟过期） */
  confirm_token?: string;
  /** tool_confirm：影响说明 */
  impact?: string;
  /** tool_confirm：参数摘要 */
  args_summary?: string;
  /** tool_confirm：有效期秒数 */
  expires_in?: number;
  /** tool_result：用户取消的写操作标记 */
  cancelled?: boolean;
  /** 前端本地状态：确认卡已处理（confirmed/cancelled）或来自历史回放（history） */
  resolved?: 'confirmed' | 'cancelled' | 'history';
}

/** 附件元信息（与后端 src/assistant/attachments.py 契约一致） */
export interface AssistantAttachmentMeta {
  upload_id: string;
  filename: string;
  ext: string;
  kind: 'image' | 'document';
  size: number;
  created_at?: string;
}

export interface AssistantMessage {
  role: 'user' | 'assistant' | string;
  content: string;
  tool_events?: ToolEvent[];
  attachments?: AssistantAttachmentMeta[];
  created_at?: string;
}

export interface AssistantSession {
  session_id: string;
  title: string;
  context?: Record<string, unknown>;
  messages: AssistantMessage[];
}

/** 会话创建上下文（方案迭代页转入时携带） */
export interface AssistantContext {
  favorite_id?: string;
  ald_smiles?: string;
  amine_smiles?: string;
  suggestion_ids?: string[];
  [key: string]: unknown;
}

// ---------- V2.2 主动能力：日报 / 提醒 ----------
/** 日报中的实验记录条目 */
export interface DailyBriefRecordItem {
  record_id: string;
  experiment_no: string;
  monomers: string;
  outcome: string;
  outcome_zh: string;
  status: string;
  self_summary?: string;
}

/** GET /daily-brief 响应契约 */
export interface DailyBrief {
  date: string;
  llm_enabled: boolean;
  records_created_count: number;
  records_created: DailyBriefRecordItem[];
  records_updated_count: number;
  records_updated: DailyBriefRecordItem[];
  dft_count: number;
  dft_best_e_bind_kcal: number | null;
  favorites_count: number;
  favorites: { favorite_id: string; monomers: string }[];
  literature_count: number;
  literature: { paper_id: string | number | null; title: string }[];
  /** ming 人格点评；LLM 未配置或生成失败时为 null */
  commentary: string | null;
}

/** GET /nudges 的单条连续失败提醒 */
export interface AssistantNudge {
  favorite_id: string;
  monomers: string;
  consecutive_failures: number;
  latest_mistakes: string;
  suggestion: string;
}

// ---------- SSE 事件 ----------
export type AssistantSseEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_call'; name: string; args?: Record<string, unknown> }
  | { type: 'tool_result'; name: string; summary?: string; is_error?: boolean; cancelled?: boolean }
  | {
      type: 'tool_confirm';
      confirm_token: string;
      name: string;
      args?: Record<string, unknown>;
      args_summary?: string;
      impact?: string;
      expires_in?: number;
    }
  | { type: 'done'; session_id?: string }
  | { type: 'error'; message: string };

/** 后端未连接错误（网络层失败），页面据此显示重试 */
export class AssistantUnavailableError extends Error {
  constructor(message = '无法连接科研助手服务，请确认后端已启动') {
    super(message);
    this.name = 'AssistantUnavailableError';
  }
}

// ---------- 普通请求 ----------
async function request<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const { method = 'GET', body } = options;
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new AssistantUnavailableError();
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
      else if (typeof data?.message === 'string') message = data.message;
    } catch {
      // 保留默认提示
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

// ---------- SSE 解析（POST 流式不能用 EventSource，用 fetch + ReadableStream） ----------
/**
 * 从 ReadableStream 中解析 SSE：按行拆分，取 "data: " 行解析 JSON。
 * 兼容 data 行跨 chunk、同一 chunk 多行、CRLF。
 */
export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<AssistantSseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 事件以空行分隔；此处按行处理，data 行即完整 JSON（契约约定）
      let idx: number;
      while ((idx = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, idx).replace(/\r$/, '');
        buffer = buffer.slice(idx + 1);
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload || payload === '[DONE]') continue;
        try {
          yield JSON.parse(payload) as AssistantSseEvent;
        } catch {
          // 忽略非 JSON 的 data 行（如心跳注释）
        }
      }
    }
    // 冲刷尾部残留（无换行结尾的最后一行）
    const tail = buffer.trim();
    if (tail.startsWith('data:')) {
      const payload = tail.slice(5).trim();
      if (payload && payload !== '[DONE]') {
        try {
          yield JSON.parse(payload) as AssistantSseEvent;
        } catch {
          /* ignore */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ---------- 附件上传（multipart，不在共享 request 封装内） ----------

/** 附件大小上限（与后端一致：10MB） */
export const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
/** 单条消息附件数上限（与后端一致） */
export const ATTACHMENT_MAX_COUNT = 3;
/** 允许的文件类型（图片 + 文档，与后端白名单一致） */
export const ATTACHMENT_ACCEPT = '.png,.jpg,.jpeg,.webp,.txt,.md,.json,.csv,.docx,.pdf';

/** 客户端预校验附件（类型/大小）；返回错误文案，合法返回 null */
export function validateAttachmentFile(file: File): string | null {
  const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`;
  if (!ATTACHMENT_ACCEPT.split(',').includes(ext)) {
    return `不支持的附件类型「${ext}」：图片仅支持 png/jpg/jpeg/webp，文档仅支持 txt/md/json/csv/docx/pdf`;
  }
  if (file.size > ATTACHMENT_MAX_BYTES) {
    return `附件「${file.name}」超过大小限制（10MB）`;
  }
  if (file.size === 0) return `附件「${file.name}」内容为空`;
  return null;
}

/** 上传附件，返回元信息（含 upload_id） */
export async function uploadAttachment(file: File): Promise<AssistantAttachmentMeta> {
  if (ASSISTANT_MOCK) return mockApi.uploadAttachment(file);
  const form = new FormData();
  form.append('file', file);
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}/uploads`, { method: 'POST', body: form });
  } catch {
    throw new AssistantUnavailableError();
  }
  if (!res.ok) {
    let message = `上传失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
      else if (typeof data?.message === 'string') message = data.message;
    } catch {
      /* 保留默认提示 */
    }
    throw new Error(message);
  }
  return (await res.json()) as AssistantAttachmentMeta;
}

// ---------- 契约 API ----------
export const assistantApi = {
  /** 助手开关状态（静默，不弹 toast） */
  status(): Promise<AssistantStatus> {
    if (ASSISTANT_MOCK) return mockApi.status();
    return request<AssistantStatus>('/status');
  },

  /** 会话列表（按 updated_at 排序由页面负责） */
  async listSessions(): Promise<AssistantSessionMeta[]> {
    if (ASSISTANT_MOCK) return mockApi.listSessions();
    const data = await request<{ sessions: AssistantSessionMeta[] }>('/sessions');
    return data.sessions ?? [];
  },

  /** 新建会话 */
  createSession(body: { title?: string; context?: AssistantContext }): Promise<{ session_id: string; title: string }> {
    if (ASSISTANT_MOCK) return mockApi.createSession(body);
    return request('/sessions', { method: 'POST', body });
  },

  /** 会话详情（含历史消息） */
  getSession(sessionId: string): Promise<AssistantSession> {
    if (ASSISTANT_MOCK) return mockApi.getSession(sessionId);
    return request(`/sessions/${encodeURIComponent(sessionId)}`);
  },

  /** 重命名会话标题（v1.5.4）：只改 meta.title，消息内容不动 */
  renameSession(sessionId: string, title: string): Promise<{ session_id: string; title: string }> {
    if (ASSISTANT_MOCK) return mockApi.renameSession(sessionId, title);
    return request(`/sessions/${encodeURIComponent(sessionId)}/title`, {
      method: 'PATCH',
      body: { title },
    });
  },

  /**
   * 发送消息并以 SSE 流式接收回复。
   * 返回解析后的事件流；网络层失败抛 AssistantUnavailableError。
   */
  async chatStream(body: {
    session_id?: string;
    message: string;
    context?: AssistantContext;
    attachments?: string[];
    stream: true;
  }): Promise<ReadableStream<Uint8Array>> {
    if (ASSISTANT_MOCK) return mockApi.chatStream(body);
    let res: Response;
    try {
      res = await fetch(`${BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch {
      throw new AssistantUnavailableError();
    }
    if (!res.ok) {
      let message = `请求失败（${res.status}）`;
      try {
        const data = await res.json();
        if (typeof data?.detail === 'string') message = data.detail;
        else if (typeof data?.message === 'string') message = data.message;
      } catch {
        /* 保留默认 */
      }
      throw new Error(message);
    }
    if (!res.body) throw new AssistantUnavailableError('响应不含数据流');
    return res.body;
  },

  /**
   * 写操作二次确认：确认 / 取消后服务端执行（或注入拒绝）并 SSE 续跑对话。
   * 返回解析后的事件流；网络层失败抛 AssistantUnavailableError。
   */
  async confirmTool(body: {
    session_id: string;
    confirm_token: string;
    decision: 'confirm' | 'cancel';
    args?: Record<string, unknown>;
  }): Promise<ReadableStream<Uint8Array>> {
    if (ASSISTANT_MOCK) return mockApi.confirmTool(body);
    let res: Response;
    try {
      res = await fetch(`${BASE_URL}/chat/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch {
      throw new AssistantUnavailableError();
    }
    if (!res.ok) {
      let message = `请求失败（${res.status}）`;
      try {
        const data = await res.json();
        if (typeof data?.detail === 'string') message = data.detail;
        else if (typeof data?.message === 'string') message = data.message;
      } catch {
        /* 保留默认 */
      }
      throw new Error(message);
    }
    if (!res.body) throw new AssistantUnavailableError('响应不含数据流');
    return res.body;
  },

  /** 今日科研日报（V2.2）：date 可选（YYYY-MM-DD），缺省今天 */
  dailyBrief(date?: string): Promise<DailyBrief> {
    if (ASSISTANT_MOCK) return mockApi.dailyBrief();
    const qs = date ? `?date=${encodeURIComponent(date)}` : '';
    return request<DailyBrief>(`/daily-brief${qs}`);
  },

  /** 连续失败提醒列表（V2.2，已过滤当日 dismiss） */
  async nudges(): Promise<AssistantNudge[]> {
    if (ASSISTANT_MOCK) return mockApi.nudges();
    const data = await request<{ nudges: AssistantNudge[] }>('/nudges');
    return data.nudges ?? [];
  },

  /** 登记"知道了"：该收藏当日不再提醒；返回最新提醒列表 */
  async dismissNudge(favoriteId: string): Promise<AssistantNudge[]> {
    if (ASSISTANT_MOCK) return mockApi.dismissNudge(favoriteId);
    const data = await request<{ nudges: AssistantNudge[] }>('/nudges/dismiss', {
      method: 'POST',
      body: { favorite_id: favoriteId },
    });
    return data.nudges ?? [];
  },
};
