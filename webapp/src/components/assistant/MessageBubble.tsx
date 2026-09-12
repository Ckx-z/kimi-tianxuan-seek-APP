/**
 * 对话气泡：用户右对齐紫金实底，助手左对齐卡片 + Markdown 渲染。
 * 助手消息中的工具事件以可折叠卡片穿插展示。
 * Markdown 渲染件（含外链安全打开）统一来自 `@/lib/markdown`（v1.9.3）。
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Bot, FileText, Image as ImageIcon, User } from 'lucide-react';
import { ToolEventCard, type ConfirmDecision } from './ToolEventCard';
import type { AssistantAttachmentMeta, ToolEvent } from './api';
import { mdComponents, normalizeMarkdownLinks } from '@/lib/markdown';

export interface ChatMessageView {
  role: 'user' | 'assistant';
  content: string;
  toolEvents?: ToolEvent[];
  /** 用户消息携带的附件（元信息，展示为 chip） */
  attachments?: Pick<AssistantAttachmentMeta, 'filename' | 'kind' | 'size'>[];
  /** 流式输出中（显示光标） */
  streaming?: boolean;
  /** 本条消息出错（温和提示；重试入口由父级渲染） */
  error?: string;
}

/** Markdown 元素映射与链接归一化统一由 `@/lib/markdown` 提供（v1.9.3） */

/** 判断 tool_call 是否尚无对应 tool_result（流式中显示“进行中”样式） */
function isPendingCall(events: ToolEvent[], index: number, streaming?: boolean): boolean {
  const e = events[index];
  if (e.type !== 'tool_call' || !streaming) return false;
  return !events.slice(index + 1).some((x) => x.type === 'tool_result' && x.name === e.name);
}

export function MessageBubble({
  message,
  onConfirmDecision,
  confirmBusy = false,
}: {
  message: ChatMessageView;
  /** 写操作确认卡按钮回调（仅实时流中的 tool_confirm 可用） */
  onConfirmDecision?: (event: ToolEvent, decision: ConfirmDecision) => void;
  confirmBusy?: boolean;
}) {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end gap-2.5">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground shadow-sm">
          {/* 附件 chip（图片/文档图标 + 文件名） */}
          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-1.5 flex flex-wrap gap-1.5">
              {message.attachments.map((a, i) => (
                <span
                  key={`${a.filename}-${i}`}
                  className="inline-flex max-w-56 items-center gap-1 rounded-md bg-white/15 px-2 py-0.5 text-xs"
                  title={a.filename}
                >
                  {a.kind === 'image' ? (
                    <ImageIcon className="h-3 w-3 shrink-0" />
                  ) : (
                    <FileText className="h-3 w-3 shrink-0" />
                  )}
                  <span className="truncate">{a.filename}</span>
                </span>
              ))}
            </div>
          )}
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gold text-gold-foreground">
          <User className="h-3.5 w-3.5" />
        </div>
      </div>
    );
  }

  const events = message.toolEvents ?? [];
  return (
    <div className="flex justify-start gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full gradient-royal text-white">
        <Bot className="h-3.5 w-3.5" />
      </div>
      <div className="max-w-[85%] space-y-2">
        {/* 工具事件卡片（默认折叠） */}
        {events.length > 0 && (
          <div className="space-y-1.5">
            {events.map((e, i) => (
              <ToolEventCard
                key={i}
                event={e}
                pending={isPendingCall(events, i, message.streaming)}
                onConfirmDecision={onConfirmDecision}
                confirmBusy={confirmBusy}
              />
            ))}
          </div>
        )}
        {/* 正文气泡 */}
        <div className="rounded-2xl rounded-tl-sm border border-border bg-card px-4 py-2.5 text-sm text-foreground shadow-sm">
          {message.content ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
              {normalizeMarkdownLinks(message.content)}
            </ReactMarkdown>
          ) : message.streaming ? (
            <span className="text-muted-foreground">思考中…</span>
          ) : null}
          {message.streaming && message.content && (
            <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-gold align-text-bottom" />
          )}
          {message.error && <p className="mt-1 text-xs text-destructive">{message.error}</p>}
        </div>
      </div>
    </div>
  );
}
