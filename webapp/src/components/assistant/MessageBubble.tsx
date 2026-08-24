/**
 * 对话气泡：用户右对齐紫金实底，助手左对齐卡片 + Markdown 渲染。
 * 助手消息中的工具事件以可折叠卡片穿插展示。
 */
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Bot, FileText, Image as ImageIcon, User } from 'lucide-react';
import { ToolEventCard } from './ToolEventCard';
import type { AssistantAttachmentMeta, ToolEvent } from './api';

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

/** 剥离 react-markdown 注入的 node 属性，避免传到 DOM */
function withoutNode<T extends { node?: unknown }>(props: T): Omit<T, 'node'> {
  const { node, ...rest } = props;
  void node;
  return rest;
}

/** Markdown 元素的 Tailwind 映射（替代 typography 插件，贴合紫金主题） */
const mdComponents: Components = {
  h1: (props) => <h3 className="mb-2 mt-3 text-base font-semibold" {...withoutNode(props)} />,
  h2: (props) => <h3 className="mb-2 mt-3 text-base font-semibold" {...withoutNode(props)} />,
  h3: (props) => <h4 className="mb-1.5 mt-2.5 text-sm font-semibold" {...withoutNode(props)} />,
  p: (props) => <p className="mb-2 leading-relaxed last:mb-0" {...withoutNode(props)} />,
  ul: (props) => <ul className="mb-2 list-disc space-y-1 pl-5" {...withoutNode(props)} />,
  ol: (props) => <ol className="mb-2 list-decimal space-y-1 pl-5" {...withoutNode(props)} />,
  li: (props) => <li className="leading-relaxed" {...withoutNode(props)} />,
  blockquote: (props) => (
    <blockquote
      className="mb-2 border-l-2 border-gold pl-3 text-muted-foreground"
      {...withoutNode(props)}
    />
  ),
  code: (props) => {
    const { className, children, ...rest } = withoutNode(props);
    const isBlock = /language-/.test(className ?? '');
    return isBlock ? (
      <code className={className} {...rest}>
        {children}
      </code>
    ) : (
      <code
        className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-primary"
        {...rest}
      >
        {children}
      </code>
    );
  },
  pre: (props) => (
    <pre
      className="mb-2 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs"
      {...withoutNode(props)}
    />
  ),
  table: (props) => (
    <div className="mb-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs" {...withoutNode(props)} />
    </div>
  ),
  th: (props) => (
    <th
      className="border border-border bg-muted/60 px-2 py-1 text-left font-medium"
      {...withoutNode(props)}
    />
  ),
  td: (props) => <td className="border border-border px-2 py-1" {...withoutNode(props)} />,
  a: (props) => (
    <a className="text-primary underline" target="_blank" rel="noreferrer" {...withoutNode(props)} />
  ),
  strong: (props) => (
    <strong className="font-semibold text-foreground" {...withoutNode(props)} />
  ),
};

/** 判断 tool_call 是否尚无对应 tool_result（流式中显示“进行中”样式） */
function isPendingCall(events: ToolEvent[], index: number, streaming?: boolean): boolean {
  const e = events[index];
  if (e.type !== 'tool_call' || !streaming) return false;
  return !events.slice(index + 1).some((x) => x.type === 'tool_result' && x.name === e.name);
}

export function MessageBubble({ message }: { message: ChatMessageView }) {
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
              />
            ))}
          </div>
        )}
        {/* 正文气泡 */}
        <div className="rounded-2xl rounded-tl-sm border border-border bg-card px-4 py-2.5 text-sm text-foreground shadow-sm">
          {message.content ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
              {message.content}
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
