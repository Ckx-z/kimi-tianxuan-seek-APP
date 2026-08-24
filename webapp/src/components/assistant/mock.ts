/**
 * 科研助手本地 Mock（VITE_ASSISTANT_MOCK=1 时启用，默认关）
 *
 * 用途：后端未就绪前的自测，以及日后联调对照。
 * - 会话存于模块级内存（刷新即清空，纯演示用）
 * - chatStream 返回真实 ReadableStream，逐字吐出 token 事件，
 *   中间穿插 tool_call / tool_result，最后 done —— 完整演示 SSE 协议
 */
import type {
  AssistantContext,
  AssistantMessage,
  AssistantSession,
  AssistantSessionMeta,
  AssistantStatus,
} from './api';

// ---------- 内存会话库 ----------
interface MockSession extends AssistantSession {
  updated_at: string;
}

const sessions = new Map<string, MockSession>();
let seq = 0;

function nowIso(): string {
  return new Date().toISOString();
}

function toMeta(s: MockSession): AssistantSessionMeta {
  return {
    session_id: s.session_id,
    title: s.title,
    updated_at: s.updated_at,
    message_count: s.messages.length,
  };
}

/** SSE 编码：每个事件一行 "data: {json}\n\n" */
export function sseEncode(events: unknown[]): Uint8Array {
  const enc = new TextEncoder();
  const text = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
  return enc.encode(text);
}

/** 把文本切成逐字/逐词的小块，模拟 token 流 */
export function chunkText(text: string, size = 3): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += size) chunks.push(text.slice(i, i + size));
  return chunks;
}

const MOCK_REPLY_PRE = `好的，我先基于这组单体查一下相关数据。

`;
const MOCK_REPLY_POST = `

## 迭代建议分析

结合图谱与最近批次的建议，我的看法如下：

1. **溶剂比例微调**：当前建议偏向提高极性溶剂占比，有利于醛胺预聚，建议优先验证 70℃ / 12h 这组条件。
2. **催化剂用量**：建议从 6 mol% 降至 3 mol%，过往记录显示过高催化剂易导致无定形副产物。
3. **锚定记录**：上次失败主要嫌疑是陈化时间不足，可延长至 72h 再观察结晶性。

| 条件 | 当前 | 建议 |
| --- | --- | --- |
| 温度 | 80℃ | 70℃ |
| 陈化 | 24h | 72h |

> 小结：先小批量验证 1 号建议，失败再回退。需要我展开任何一条吗？`;

/** 构造演示用 SSE 事件序列（token 逐字 + 工具卡 + done） */
export function buildMockEvents(sessionId: string): unknown[] {
  const events: unknown[] = [];
  for (const t of chunkText(MOCK_REPLY_PRE)) events.push({ type: 'token', text: t });
  events.push({
    type: 'tool_call',
    name: 'query_graph',
    args: { ald_smiles: 'C=O...', amine_smiles: 'N...' , top_k: 5 },
  });
  events.push({
    type: 'tool_result',
    name: 'query_graph',
    summary: '命中 5 条相似文献报道：醛胺缩合类 COF，常见溶剂为均三甲苯/二氧六环，温度 70-120℃。',
    is_error: false,
  });
  events.push({
    type: 'tool_call',
    name: 'list_experiment_records',
    args: { favorite_id: 'fav-demo', limit: 3 },
  });
  events.push({
    type: 'tool_result',
    name: 'list_experiment_records',
    summary: '找到 3 条实验记录，最近一次（EXP-1024）结果为结晶性偏弱。',
    is_error: false,
  });
  for (const t of chunkText(MOCK_REPLY_POST)) events.push({ type: 'token', text: t });
  events.push({ type: 'done', session_id: sessionId });
  return events;
}

// ---------- Mock API（与 assistantApi 同签名） ----------
export const mockApi = {
  async uploadAttachment(file: File): Promise<import('./api').AssistantAttachmentMeta> {
    // mock：不落盘，只造一份元信息让 UI 流程可演示
    const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`;
    return {
      upload_id: `u_mock${Date.now().toString(16)}`,
      filename: file.name,
      ext,
      kind: ['.png', '.jpg', '.jpeg', '.webp'].includes(ext) ? 'image' : 'document',
      size: file.size,
      created_at: nowIso(),
    };
  },

  async status(): Promise<AssistantStatus> {
    return { enabled: true, reason: 'mock 模式' };
  },

  async listSessions(): Promise<AssistantSessionMeta[]> {
    return [...sessions.values()]
      .map(toMeta)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  },

  async createSession(body: { title?: string; context?: AssistantContext }): Promise<{ session_id: string; title: string }> {
    seq += 1;
    const session_id = `mock-sess-${Date.now()}-${seq}`;
    const title = body.title?.trim() || `新会话 ${seq}`;
    sessions.set(session_id, {
      session_id,
      title,
      context: body.context as Record<string, unknown> | undefined,
      messages: [],
      updated_at: nowIso(),
    });
    return { session_id, title };
  },

  async getSession(sessionId: string): Promise<AssistantSession> {
    const s = sessions.get(sessionId);
    if (!s) throw new Error('会话不存在');
    return {
      session_id: s.session_id,
      title: s.title,
      context: s.context,
      messages: s.messages,
    };
  },

  async chatStream(body: {
    session_id?: string;
    message: string;
    context?: AssistantContext;
    stream: true;
  }): Promise<ReadableStream<Uint8Array>> {
    // mock 下若无 session_id 则自动建一个，方便页面演示
    let sid = body.session_id;
    if (!sid || !sessions.has(sid)) {
      const created = await mockApi.createSession({
        title: body.message.slice(0, 16),
        context: body.context,
      });
      sid = created.session_id;
    }
    const session = sessions.get(sid)!;

    // 记录用户消息与（稍后的）助手消息，便于历史回放演示
    const userMsg: AssistantMessage = { role: 'user', content: body.message, created_at: nowIso() };
    const replyText = MOCK_REPLY_PRE + MOCK_REPLY_POST;
    const assistantMsg: AssistantMessage = {
      role: 'assistant',
      content: replyText,
      tool_events: [
        { type: 'tool_call', name: 'query_graph', args: { top_k: 5 } },
        {
          type: 'tool_result',
          name: 'query_graph',
          summary: '命中 5 条相似文献报道：醛胺缩合类 COF，常见溶剂为均三甲苯/二氧六环，温度 70-120℃。',
          is_error: false,
        },
        { type: 'tool_call', name: 'list_experiment_records', args: { favorite_id: 'fav-demo', limit: 3 } },
        {
          type: 'tool_result',
          name: 'list_experiment_records',
          summary: '找到 3 条实验记录，最近一次（EXP-1024）结果为结晶性偏弱。',
          is_error: false,
        },
      ],
      created_at: nowIso(),
    };
    session.messages.push(userMsg, assistantMsg);
    session.updated_at = nowIso();

    const events = buildMockEvents(sid);
    const enc = new TextEncoder();
    // 逐事件吐出，模拟真实网络流（每个事件间隔 ~30ms，token 流可见逐字效果）
    return new ReadableStream<Uint8Array>({
      async start(controller) {
        for (const e of events) {
          controller.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`));
          await new Promise((r) => setTimeout(r, 30));
        }
        controller.close();
      },
    });
  },
};
