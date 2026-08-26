/**
 * 科研助手页（一级页面）
 * - 左侧会话列表（新建/切换/按 updated_at 倒序），右侧对话区
 * - SSE 流式逐字输出（fetch + ReadableStream，POST 流式不能用 EventSource）
 * - 工具调用事件在消息流中显示为默认折叠的小卡片
 * - 方案迭代页可通过 location.state 传入 { assistantContext, openingMessage } 自动建会话并开场
 * - 后端未启用 LLM 时显示优雅禁用态，引导去设置页配置
 * - Mock 开关：VITE_ASSISTANT_MOCK=1（见 components/assistant/api.ts）
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { Bot, FileText, Image as ImageIcon, MessageSquarePlus, Paperclip, RefreshCw, Send, Settings as SettingsIcon, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import {
  assistantApi,
  parseSseStream,
  uploadAttachment,
  validateAttachmentFile,
  AssistantUnavailableError,
  ASSISTANT_MOCK,
  ATTACHMENT_ACCEPT,
  ATTACHMENT_MAX_COUNT,
  type AssistantAttachmentMeta,
  type AssistantContext,
  type AssistantSessionMeta,
  type AssistantStatus,
  type ToolEvent,
} from '@/components/assistant/api';
import { MessageBubble, type ChatMessageView } from '@/components/assistant/MessageBubble';
import { DailyBriefCard } from '@/components/assistant/DailyBriefCard';
import { NudgeBar } from '@/components/assistant/NudgeBar';

interface LocalMessage extends ChatMessageView {
  id: string;
}

/** 方案迭代页转入时携带的 location.state 结构 */
interface TransferState {
  assistantContext?: AssistantContext;
  openingMessage?: string;
}

let msgSeq = 0;
const nextId = () => `m${++msgSeq}`;

export default function Assistant() {
  const location = useLocation();
  const navigate = useNavigate();

  // 助手状态：null=检测中
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<AssistantSessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeContext, setActiveContext] = useState<AssistantContext | undefined>(undefined);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  /** 写操作确认请求进行中（确认卡按钮防重复点击） */
  const [confirmBusy, setConfirmBusy] = useState(false);
  /** 待发送附件（未上传；发送时先上传再带 upload_id 发消息） */
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  /** 最近一次发送（供网络中断后重试；attachments 为已上传的元信息） */
  const lastSentRef = useRef<{ message: string; attachments?: AssistantAttachmentMeta[] } | null>(null);
  const [canRetry, setCanRetry] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const transferConsumedRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  // ---------- 数据加载 ----------
  const refreshSessions = useCallback(async () => {
    try {
      const list = await assistantApi.listSessions();
      setSessions(
        [...list].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))),
      );
    } catch {
      // 列表失败不阻塞对话
    }
  }, []);

  const checkStatus = useCallback(async () => {
    setStatusError(null);
    try {
      const s = await assistantApi.status();
      setStatus(s);
      if (s.enabled) refreshSessions();
    } catch (e) {
      setStatus(null);
      setStatusError(
        e instanceof AssistantUnavailableError
          ? e.message
          : e instanceof Error
            ? e.message
            : '状态检测失败',
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    checkStatus();
    const abort = abortRef.current;
    return () => abort?.abort();
  }, [checkStatus]);

  // 切换会话：拉取历史消息
  const openSession = useCallback(async (sessionId: string) => {
    setActiveId(sessionId);
    setCanRetry(false);
    setLoadingSession(true);
    try {
      const detail = await assistantApi.getSession(sessionId);
      setActiveContext(detail.context as AssistantContext | undefined);
      setMessages(
        (detail.messages ?? [])
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m) => ({
            id: nextId(),
            role: m.role as 'user' | 'assistant',
            content: m.content,
            // 历史回放中的确认卡只读（令牌早已过期/已用），标记为 history
            toolEvents: m.tool_events?.map((e) =>
              e.type === 'tool_confirm' && !e.resolved ? { ...e, resolved: 'history' as const } : e,
            ),
            attachments: m.attachments,
          })),
      );
    } catch {
      setMessages([]);
    } finally {
      setLoadingSession(false);
    }
  }, []);

  // ---------- 流式发送 ----------
  const runStream = useCallback(
    async (
      sessionId: string,
      message: string,
      context?: AssistantContext,
      attachments?: AssistantAttachmentMeta[],
    ) => {
      const asstId = nextId();
      setMessages((ms) => [
        ...ms,
        { id: asstId, role: 'assistant', content: '', toolEvents: [], streaming: true },
      ]);
      setStreaming(true);
      setCanRetry(false);
      const patch = (p: Partial<LocalMessage>) =>
        setMessages((ms) => ms.map((m) => (m.id === asstId ? { ...m, ...p } : m)));
      try {
        const stream = await assistantApi.chatStream({
          session_id: sessionId,
          message,
          context,
          attachments: attachments?.length ? attachments.map((a) => a.upload_id) : undefined,
          stream: true,
        });
        for await (const ev of parseSseStream(stream)) {
          if (ev.type === 'token') {
            setMessages((ms) =>
              ms.map((m) => (m.id === asstId ? { ...m, content: m.content + ev.text } : m)),
            );
          } else if (ev.type === 'tool_call' || ev.type === 'tool_result' || ev.type === 'tool_confirm') {
            setMessages((ms) =>
              ms.map((m) =>
                m.id === asstId ? { ...m, toolEvents: [...(m.toolEvents ?? []), ev] } : m,
              ),
            );
          } else if (ev.type === 'done') {
            if (ev.session_id && ev.session_id !== sessionId) setActiveId(ev.session_id);
          } else if (ev.type === 'error') {
            patch({ error: `助手回复出错：${ev.message}`, streaming: false });
          }
        }
        patch({ streaming: false });
        refreshSessions();
      } catch (e) {
        patch({
          streaming: false,
          error:
            e instanceof AssistantUnavailableError
              ? '连接中断，请检查网络或后端服务后重试。'
              : e instanceof Error
                ? e.message
                : '发送失败，请重试。',
        });
        setCanRetry(true);
      } finally {
        setStreaming(false);
      }
    },
    [refreshSessions],
  );

  // ---------- 写操作二次确认 ----------
  /** 确认卡按钮：确认/取消 → POST /chat/confirm → SSE 续跑（新助手消息接续） */
  const handleConfirmDecision = useCallback(
    async (ev: ToolEvent, decision: 'confirm' | 'cancel') => {
      const token = ev.confirm_token;
      const sid = activeId;
      if (!token || !sid || confirmBusy || ev.resolved) return;
      setConfirmBusy(true);
      // 乐观标记确认卡状态（失败时回滚，允许重试）
      const markResolved = (value?: 'confirmed' | 'cancelled') =>
        setMessages((ms) =>
          ms.map((m) => ({
            ...m,
            toolEvents: m.toolEvents?.map((e) =>
              e.type === 'tool_confirm' && e.confirm_token === token
                ? { ...e, resolved: value }
                : e,
            ),
          })),
        );
      markResolved(decision === 'confirm' ? 'confirmed' : 'cancelled');

      const asstId = nextId();
      setMessages((ms) => [
        ...ms,
        { id: asstId, role: 'assistant', content: '', toolEvents: [], streaming: true },
      ]);
      setStreaming(true);
      const patch = (p: Partial<LocalMessage>) =>
        setMessages((ms) => ms.map((m) => (m.id === asstId ? { ...m, ...p } : m)));
      try {
        const stream = await assistantApi.confirmTool({
          session_id: sid,
          confirm_token: token,
          decision,
        });
        for await (const sev of parseSseStream(stream)) {
          if (sev.type === 'token') {
            setMessages((ms) =>
              ms.map((m) => (m.id === asstId ? { ...m, content: m.content + sev.text } : m)),
            );
          } else if (sev.type === 'tool_call' || sev.type === 'tool_result' || sev.type === 'tool_confirm') {
            setMessages((ms) =>
              ms.map((m) =>
                m.id === asstId ? { ...m, toolEvents: [...(m.toolEvents ?? []), sev] } : m,
              ),
            );
          } else if (sev.type === 'error') {
            patch({ error: `操作处理失败：${sev.message}`, streaming: false });
          }
        }
        patch({ streaming: false });
        refreshSessions();
      } catch (e) {
        // 网络层失败：确认可能未送达，回滚确认卡允许重试
        markResolved(undefined);
        setMessages((ms) => ms.filter((m) => m.id !== asstId));
        toast.error(
          e instanceof AssistantUnavailableError
            ? '连接中断，确认未送达，请重试。'
            : `确认请求失败：${e instanceof Error ? e.message : '未知错误'}`,
        );
      } finally {
        setConfirmBusy(false);
        setStreaming(false);
      }
    },
    [activeId, confirmBusy, refreshSessions],
  );

  // ---------- 附件 ----------
  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const incoming = [...files];
      if (incoming.length === 0) return;
      setPendingFiles((prev) => {
        const room = ATTACHMENT_MAX_COUNT - prev.length;
        if (room <= 0) {
          toast.warning(`一次最多附带 ${ATTACHMENT_MAX_COUNT} 个附件`);
          return prev;
        }
        const accepted: File[] = [];
        for (const f of incoming.slice(0, room)) {
          const err = validateAttachmentFile(f);
          if (err) toast.error(err);
          else accepted.push(f);
        }
        if (incoming.length > room) {
          toast.warning(`一次最多附带 ${ATTACHMENT_MAX_COUNT} 个附件，超出的已忽略`);
        }
        return [...prev, ...accepted];
      });
    },
    [],
  );

  const removePendingFile = useCallback((idx: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const sendMessage = useCallback(
    async (raw: string) => {
      const message = raw.trim();
      const files = pendingFiles;
      if ((!message && files.length === 0) || streaming || uploading) return;
      setInput('');

      // 先上传附件（失败则中止本次发送，输入与附件保留在输入区）
      let metas: AssistantAttachmentMeta[] = [];
      if (files.length > 0) {
        setUploading(true);
        try {
          for (const f of files) {
            metas.push(await uploadAttachment(f));
          }
          setPendingFiles([]);
        } catch (e) {
          toast.error(
            `附件上传失败：${e instanceof AssistantUnavailableError
              ? '无法连接后端服务，请确认服务已启动'
              : e instanceof Error
                ? e.message
                : '未知错误'}，消息未发送`,
          );
          setInput(raw); // 恢复输入，避免丢失
          return;
        } finally {
          setUploading(false);
        }
      }

      const effective = message || '请查看我上传的附件并给出分析。';
      lastSentRef.current = { message: effective, attachments: metas };
      setMessages((ms) => [
        ...ms,
        {
          id: nextId(),
          role: 'user',
          content: effective,
          attachments: metas.length ? metas : undefined,
        },
      ]);
      let sid = activeId;
      if (!sid) {
        try {
          const created = await assistantApi.createSession({
            title: effective.slice(0, 16),
            context: activeContext,
          });
          sid = created.session_id;
          setActiveId(sid);
        } catch (e) {
          setMessages((ms) => [
            ...ms,
            {
              id: nextId(),
              role: 'assistant',
              content: '',
              error:
                e instanceof AssistantUnavailableError
                  ? '连接中断，请检查网络或后端服务后重试。'
                  : '创建会话失败，请重试。',
            },
          ]);
          setCanRetry(true);
          return;
        }
      }
      await runStream(sid, effective, activeContext, metas);
    },
    [activeId, activeContext, pendingFiles, runStream, streaming, uploading],
  );

  // 网络中断重试：复用上一条用户消息（含已上传附件的 upload_id），直接重发流
  const retryLast = useCallback(async () => {
    const last = lastSentRef.current;
    if (!last || streaming) return;
    // 移除上一条失败的助手占位消息
    setMessages((ms) => ms.filter((m, i) => !(i === ms.length - 1 && m.role === 'assistant' && m.error)));
    const sid = activeId;
    if (!sid) {
      setCanRetry(false);
      await sendMessage(last.message);
      return;
    }
    await runStream(sid, last.message, activeContext, last.attachments);
  }, [activeId, activeContext, runStream, sendMessage, streaming]);

  // ---------- 方案迭代转入：自动建会话 + 开场消息 ----------
  useEffect(() => {
    if (transferConsumedRef.current) return;
    const state = location.state as TransferState | null;
    if (!state?.assistantContext || !status?.enabled) return;
    transferConsumedRef.current = true;
    // 清空 state，避免刷新/返回重复触发
    navigate(location.pathname, { replace: true, state: null });
    const ctx = state.assistantContext;
    const opening = state.openingMessage?.trim() || '我想深入讨论这组单体的迭代建议';
    (async () => {
      try {
        const created = await assistantApi.createSession({
          title: '方案迭代深入讨论',
          context: ctx,
        });
        setActiveId(created.session_id);
        setActiveContext(ctx);
        setMessages([]);
        lastSentRef.current = { message: opening };
        setMessages([{ id: nextId(), role: 'user', content: opening }]);
        await runStream(created.session_id, opening, ctx);
        refreshSessions();
      } catch {
        setMessages([
          {
            id: nextId(),
            role: 'assistant',
            content: '',
            error: '创建讨论会话失败，请检查后端后重试。',
          },
        ]);
        setCanRetry(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  // ---------- 自动滚动到底部 ----------
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const handleNewSession = () => {
    if (streaming) return;
    setActiveId(null);
    setActiveContext(undefined);
    setMessages([]);
    setCanRetry(false);
  };

  /** 提醒条点击 → 话术填入输入框并聚焦（不自动发送，用户确认后发） */
  const handleNudgePrefill = useCallback((text: string) => {
    setInput(text);
    inputRef.current?.focus();
  }, []);

  // ---------- 状态页：检测中 / 禁用 / 离线 ----------
  if (statusError) {
    return (
      <CenteredState
        icon={<Bot className="h-8 w-8 text-gold" />}
        title="无法连接科研助手服务"
        desc={statusError}
        action={
          <Button onClick={checkStatus} variant="outline">
            <RefreshCw className="mr-2 h-4 w-4" />
            重新检测
          </Button>
        }
      />
    );
  }
  if (status === null) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-[60dvh] w-full" />
      </div>
    );
  }
  if (!status.enabled) {
    return (
      <CenteredState
        icon={<Bot className="h-8 w-8 text-gold" />}
        title="科研助手未启用"
        desc={status.reason || '需要先在设置页配置 LLM（API Key / 模型）后才能使用科研助手。'}
        action={
          <Link to="/settings">
            <Button className="bg-primary text-primary-foreground">
              <SettingsIcon className="mr-2 h-4 w-4" />
              去设置页配置 LLM
            </Button>
          </Link>
        }
      />
    );
  }

  // ---------- 主界面 ----------
  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col gap-4">
      {/* 页头 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <h1 className="text-2xl font-semibold text-foreground">科研助手</h1>
          {ASSISTANT_MOCK && (
            <span className="rounded-full border border-gold/50 bg-gold-muted px-2 py-0.5 text-[11px] text-gold-foreground">
              Mock 模式
            </span>
          )}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={handleNewSession}
          disabled={streaming}
          className="shrink-0"
        >
          <MessageSquarePlus className="mr-1.5 h-4 w-4" />
          新建会话
        </Button>
      </div>

      {/* V2.2 主动能力：今日科研日报卡 + 连续失败提醒条 */}
      <DailyBriefCard />
      <NudgeBar onPrefill={handleNudgePrefill} disabled={streaming} />

      <div className="flex min-h-0 flex-1 flex-col gap-4 md:flex-row">
        {/* 左侧会话列表：窄屏时变为顶部横向滚动条 */}
        <aside
          className={cn(
            'flex shrink-0 gap-1.5 overflow-x-auto pb-1',
            'md:w-56 md:flex-col md:overflow-y-auto md:overflow-x-hidden md:rounded-xl md:border md:border-border md:bg-card md:p-2 md:pb-2',
          )}
        >
          {sessions.length === 0 && (
            <p className="hidden px-2 py-3 text-xs text-muted-foreground md:block">
              暂无历史会话
            </p>
          )}
          {sessions.map((s) => (
            <button
              key={s.session_id}
              type="button"
              onClick={() => s.session_id !== activeId && !streaming && openSession(s.session_id)}
              className={cn(
                'shrink-0 rounded-lg px-3 py-2 text-left text-sm transition-colors',
                'max-w-40 truncate md:max-w-none',
                s.session_id === activeId
                  ? 'bg-accent font-medium text-accent-foreground shadow-[inset_2px_0_0_0_hsl(var(--gold))]'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
              )}
              title={s.title}
            >
              <span className="block truncate">{s.title || '未命名会话'}</span>
              <span className="mt-0.5 hidden text-[11px] text-muted-foreground/70 md:block">
                {s.message_count} 条消息 · {(s.updated_at || '').slice(0, 10)}
              </span>
            </button>
          ))}
        </aside>

        {/* 右侧对话区：限高 + 内部滚动（同 dialog 90dvh 模式思路） */}
        <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card">
          {/* 上下文提示条（从方案迭代转入时显示） */}
          {activeContext && (
            <div className="border-b border-gold/30 bg-gold-muted/40 px-4 py-2 text-xs text-muted-foreground">
              已关联上下文
              {activeContext.favorite_id ? `：收藏 ${activeContext.favorite_id}` : ''}
              {Array.isArray(activeContext.suggestion_ids) &&
                ` · ${activeContext.suggestion_ids.length} 条迭代建议`}
            </div>
          )}

          {/* 消息滚动区 */}
          <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {loadingSession ? (
              <div className="space-y-3 pt-2">
                <Skeleton className="h-12 w-2/3" />
                <Skeleton className="ml-auto h-10 w-1/2" />
                <Skeleton className="h-16 w-3/4" />
              </div>
            ) : messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-full gradient-royal">
                  <Bot className="h-6 w-6 text-white" />
                </div>
                <p className="text-sm text-muted-foreground">
                  我是你的 COF 成膜科研助手，可以结合收藏单体、实验记录与迭代建议一起分析。
                  <br />
                  在下方输入问题开始对话，或从「方案迭代」页一键转入。
                </p>
              </div>
            ) : (
              messages.map((m) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  onConfirmDecision={handleConfirmDecision}
                  confirmBusy={confirmBusy}
                />
              ))
            )}

            {/* 网络中断重试入口 */}
            {canRetry && !streaming && (
              <div className="flex justify-start pl-9">
                <Button size="sm" variant="outline" onClick={retryLast}>
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  重试上一条
                </Button>
              </div>
            )}
          </div>

          {/* 输入区 */}
          <div className="border-t border-border p-3">
            {/* 待发送附件 chips（可删除重传） */}
            {pendingFiles.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {pendingFiles.map((f, i) => {
                  const isImage = /\.(png|jpe?g|webp)$/i.test(f.name);
                  return (
                    <span
                      key={`${f.name}-${i}`}
                      className="inline-flex max-w-56 items-center gap-1.5 rounded-md border border-border bg-muted/60 px-2 py-1 text-xs text-foreground"
                      title={`${f.name}（${(f.size / 1024).toFixed(0)}KB）`}
                    >
                      {isImage ? (
                        <ImageIcon className="h-3 w-3 shrink-0 text-muted-foreground" />
                      ) : (
                        <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                      )}
                      <span className="truncate">{f.name}</span>
                      <button
                        type="button"
                        title="移除附件"
                        disabled={streaming || uploading}
                        onClick={() => removePendingFile(i)}
                        className="shrink-0 text-muted-foreground hover:text-destructive"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  );
                })}
              </div>
            )}
            <div className="flex items-end gap-2">
              {/* 附件选择（隐藏 input） */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ATTACHMENT_ACCEPT}
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) addFiles(e.target.files);
                  e.target.value = '';
                }}
              />
              <Button
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
                disabled={streaming || uploading || pendingFiles.length >= ATTACHMENT_MAX_COUNT}
                className="h-9 w-9 shrink-0 p-0"
                title={`添加附件（图片/文档，单个 ≤10MB，最多 ${ATTACHMENT_MAX_COUNT} 个）`}
              >
                <Paperclip className="h-4 w-4" />
              </Button>
              <Textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    sendMessage(input);
                  }
                }}
                placeholder="输入问题，Enter 发送，Shift+Enter 换行；可点击左侧回形针附带图片/文档"
                rows={2}
                disabled={streaming || uploading}
                className="max-h-32 resize-none"
              />
              <Button
                onClick={() => sendMessage(input)}
                disabled={streaming || uploading || (!input.trim() && pendingFiles.length === 0)}
                className="h-9 w-9 shrink-0 bg-primary p-0 text-primary-foreground"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
            {uploading && (
              <p className="mt-1.5 text-xs text-muted-foreground">附件上传中…</p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

/** 居中状态页（禁用 / 离线） */
function CenteredState({
  icon,
  title,
  desc,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  action: React.ReactNode;
}) {
  return (
    <div className="flex h-[calc(100dvh-4rem)] items-center justify-center">
      <div className="flex max-w-md flex-col items-center gap-4 rounded-2xl border border-dashed border-gold/50 bg-gold-muted/30 p-10 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-card shadow-sm">
          {icon}
        </div>
        <h1 className="text-xl font-semibold text-foreground">{title}</h1>
        <p className="text-sm leading-relaxed text-muted-foreground">{desc}</p>
        {action}
      </div>
    </div>
  );
}
