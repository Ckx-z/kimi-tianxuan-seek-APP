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

const MOCK_CONFIRM_TOKEN = 'mock-cfm-0001';

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

const MOCK_CONFIRM_REPLY = '好的，已确认执行：该组合已收藏到「收藏夹1」（条目 fav-demo-001），并附上了当前打分快照（0.650）。还要我做别的吗？';
const MOCK_CANCEL_REPLY = '好的，该操作已取消，没有写入任何数据。需要我换个方式处理吗？';

/** 写操作演示：消息含「收藏」时发 tool_confirm 并挂起（无 tool_result） */
export function buildMockConfirmEvents(sessionId: string): unknown[] {
  const events: unknown[] = [];
  for (const t of chunkText('我准备把这组单体收藏到「收藏夹1」，会写入收藏数据并附当前打分快照，请你确认。\n\n'))
    events.push({ type: 'token', text: t });
  events.push({
    type: 'tool_call',
    name: 'manage_favorite',
    args: { action: 'add', ald_smiles: 'O=Cc1ccccc1', amine_smiles: 'Nc1ccccc1', folder_name: '收藏夹1' },
  });
  events.push({
    type: 'tool_confirm',
    confirm_token: MOCK_CONFIRM_TOKEN,
    name: 'manage_favorite',
    args: { action: 'add', ald_smiles: 'O=Cc1ccccc1', amine_smiles: 'Nc1ccccc1', folder_name: '收藏夹1' },
    args_summary: '{"action":"add","ald_smiles":"O=Cc1ccccc1","amine_smiles":"Nc1ccccc1","folder_name":"收藏夹1"}',
    impact: '将把该醛/胺组合收藏到「收藏夹1」，并尝试附带当前打分快照；同组合已收藏时不会重复添加。',
    expires_in: 300,
  });
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

    // 写操作演示分支：消息含「收藏」→ 发确认卡并挂起（不入库 tool_result）
    if (body.message.includes('收藏')) {
      const confirmEvents = buildMockConfirmEvents(sid);
      const toolEvts = confirmEvents.filter(
        (e) => (e as { type?: string }).type === 'tool_call' || (e as { type?: string }).type === 'tool_confirm',
      ) as AssistantMessage['tool_events'];
      session.messages.push(
        { role: 'user', content: body.message, created_at: nowIso() },
        {
          role: 'assistant',
          content: '我准备把这组单体收藏到「收藏夹1」，会写入收藏数据并附当前打分快照，请你确认。\n\n',
          tool_events: toolEvts,
          created_at: nowIso(),
        },
      );
      session.updated_at = nowIso();
      return streamEvents(confirmEvents);
    }

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
    return streamEvents(events);
  },

  /** 写操作确认（mock）：确认 → tool_result + 完成回复；取消 → 取消说明 */
  async confirmTool(body: {
    session_id: string;
    confirm_token: string;
    decision: 'confirm' | 'cancel';
  }): Promise<ReadableStream<Uint8Array>> {
    const session = sessions.get(body.session_id);
    if (body.confirm_token !== MOCK_CONFIRM_TOKEN || !session) {
      return streamEvents([{ type: 'error', message: '确认令牌不存在或已被使用（每次确认仅生效一次）' }]);
    }
    const confirmed = body.decision === 'confirm';
    const events: unknown[] = [
      confirmed
        ? { type: 'tool_result', name: 'manage_favorite', summary: '已收藏到「收藏夹1」（条目 fav-demo-001），已附当前打分快照（分数 0.650）。', is_error: false }
        : { type: 'tool_result', name: 'manage_favorite', summary: '用户取消了该操作，未执行。', is_error: false, cancelled: true },
    ];
    const reply = confirmed ? MOCK_CONFIRM_REPLY : MOCK_CANCEL_REPLY;
    for (const t of chunkText(reply)) events.push({ type: 'token', text: t });
    events.push({ type: 'done', session_id: body.session_id });
    session.messages.push({
      role: 'assistant',
      content: reply,
      tool_events: [events[0] as NonNullable<AssistantMessage['tool_events']>[number]],
      created_at: nowIso(),
    });
    session.updated_at = nowIso();
    return streamEvents(events);
  },
};

/** 逐事件吐出为 SSE 流（每个事件间隔 ~30ms，token 流可见逐字效果） */
function streamEvents(events: unknown[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const e of events) {
        controller.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`));
        await new Promise((r) => setTimeout(r, 30));
      }
      controller.close();
    },
  });
}
