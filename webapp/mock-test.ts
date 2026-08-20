/**
 * Mock 端到端验证脚本（esbuild 打包后在 node 运行）：
 * 验证 mock SSE 流 → parseSseStream 解析出 token 逐字 / tool 事件 / done
 * 用法见 package 脚本外的手动命令（构建期不引用本文件）。
 */
import { parseSseStream } from './src/components/assistant/api';
import { mockApi } from './src/components/assistant/mock';

const assert = (cond: boolean, msg: string) => {
  if (!cond) {
    console.error('FAIL:', msg);
    process.exit(1);
  }
  console.log('ok -', msg);
};

// 1. status
const status = await mockApi.status();
assert(status.enabled === true, 'status enabled');

// 2. 会话生命周期
const created = await mockApi.createSession({ title: 'mock 验证', context: { favorite_id: 'f1' } });
assert(typeof created.session_id === 'string' && created.title === 'mock 验证', 'createSession');
const list = await mockApi.listSessions();
assert(list.some((s) => s.session_id === created.session_id), 'listSessions contains new session');

// 3. SSE 流
const stream = await mockApi.chatStream({
  session_id: created.session_id,
  message: '我想深入讨论这组单体的迭代建议',
  stream: true,
});
const events: Array<{ type: string; text?: string; name?: string; session_id?: string }> = [];
for await (const ev of parseSseStream(stream)) events.push(ev);

const tokens = events.filter((e) => e.type === 'token');
const toolCalls = events.filter((e) => e.type === 'tool_call');
const toolResults = events.filter((e) => e.type === 'tool_result');
const done = events.find((e) => e.type === 'done');
assert(tokens.length > 20, `token 逐字输出（${tokens.length} 个 token 事件）`);
assert(toolCalls.length === 2 && toolResults.length === 2, 'tool_call/tool_result 各 2 个');
assert(!!done && done.session_id === created.session_id, 'done 事件携带 session_id');
const full = tokens.map((t) => t.text).join('');
assert(full.includes('迭代建议分析') && full.includes('| 温度 |'), 'token 拼接成完整 Markdown 回复');
const doneIdx = events.indexOf(done!);
assert(doneIdx === events.length - 1, 'done 是最后一个事件');

// 4. 历史回放
const detail = await mockApi.getSession(created.session_id);
assert(detail.messages.length === 2, '历史消息含 user+assistant');
assert((detail.messages[1].tool_events ?? []).length === 4, '历史消息保留 4 个工具事件');

console.log('\nALL MOCK TESTS PASSED');
