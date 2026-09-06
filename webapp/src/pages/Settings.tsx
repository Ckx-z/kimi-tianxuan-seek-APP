/**
 * 设置
 * - LLM 配置卡：查看当前配置（掩码 key / 来源）+ 表单保存 + 测试连接
 * - 后端状态卡：tree / gnn / routing 可用性
 * - 关于卡：项目名 / 版本 / 主题说明
 * - 后端未连接时优雅降级，不白屏
 */
import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, XCircle, Loader2, PlugZap, Brain, Eye, Globe, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { BackendUnavailableError } from '@/lib/api';
import { COLOR_SCHEMES, SCHEME_LABELS, useTheme } from '@/hooks/use-theme';
import { GnnEvolutionPanel } from '@/components/settings/GnnEvolutionPanel';
import {
  fetchLlmSettings,
  saveLlmSettings,
  testLlmConnection,
  fetchHealth,
  fetchAssistantMemory,
  updateAssistantMemory,
  clearAssistantMemory,
  fetchSearchSettings,
  saveSearchSettings,
  fetchPairMemories,
  deletePairMemory,
  fetchSkills,
  setSkillEnabled,
  type LlmSettings,
  type HealthInfo,
  type AssistantMemoryInfo,
  type WebSearchSettings,
  type PairMemoryMeta,
  type AssistantSkill,
} from '@/components/settings/api';

/** 配置来源中文标签 */
const SOURCE_LABELS: Record<string, string> = {
  local_settings: '本地设置文件',
  env: '环境变量',
  longcat_seed: '默认种子（longcat）',
};

/** 可用性指示点 */
function StatusDot({ ok }: { ok: boolean | undefined }) {
  return ok === undefined ? (
    <span className="h-2 w-2 rounded-full bg-muted-foreground" />
  ) : ok ? (
    <span className="h-2 w-2 rounded-full bg-emerald-500" />
  ) : (
    <span className="h-2 w-2 rounded-full bg-red-400" />
  );
}

/** LLM 配置卡 */
function LlmSettingsCard({ offline }: { offline: boolean }) {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const s = await fetchLlmSettings();
      setSettings(s);
      setBaseUrl(s.base_url || '');
      setModel(s.model || '');
      // api_key 掩码不回填，留空表示不修改
    } catch {
      /* 离线或失败：api 层已处理 */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!offline) void load();
    else setLoading(false);
  }, [offline, load]);

  async function handleSave() {
    if (!baseUrl.trim() || !apiKey.trim()) {
      toast.error('请填写 base_url 与 api_key');
      return;
    }
    setSaving(true);
    try {
      await saveLlmSettings({ base_url: baseUrl.trim(), api_key: apiKey.trim(), model: model.trim() });
      toast.success('LLM 配置已保存');
      setApiKey('');
      setTestResult(null);
      await load();
    } catch {
      /* 已 toast */
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testLlmConnection();
      setTestResult(r);
      if (r.ok) toast.success('连接测试成功');
      else toast.error(`连接测试失败：${r.message}`);
    } catch {
      /* 已 toast */
    } finally {
      setTesting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          LLM 配置
          {settings &&
            (settings.configured ? (
              <Badge variant="outline" className="border-gold/60 bg-gold-muted text-gold-foreground">
                已配置
              </Badge>
            ) : (
              <Badge variant="outline" className="border-border bg-muted text-muted-foreground">
                未配置
              </Badge>
            ))}
        </CardTitle>
        <CardDescription>配置大模型服务地址、密钥与模型名，供迭代建议等智能功能使用。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : offline ? (
          <p className="text-sm text-muted-foreground">后端未连接，暂无法读取或保存 LLM 配置。</p>
        ) : (
          <>
            {/* 当前生效配置 */}
            {settings?.configured && (
              <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
                <div className="mb-1 text-xs font-semibold text-muted-foreground">当前生效配置</div>
                <div className="space-y-1 text-muted-foreground">
                  <div>
                    base_url：<span className="text-foreground">{settings.base_url || '—'}</span>
                  </div>
                  <div>
                    model：<span className="text-foreground">{settings.model || '—'}</span>
                  </div>
                  <div>
                    api_key：
                    <span className="font-mono text-foreground">{settings.api_key_masked || '—'}</span>
                  </div>
                  <div>
                    来源：
                    <span className="text-foreground">
                      {SOURCE_LABELS[settings.source] ?? settings.source ?? '—'}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* 编辑表单 */}
            <div className="grid gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="llm-base-url">Base URL</Label>
                <Input
                  id="llm-base-url"
                  placeholder="https://api.example.com/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="llm-api-key">API Key（保存时必填，密钥仅写入本地设置文件）</Label>
                <Input
                  id="llm-api-key"
                  type="password"
                  placeholder={settings?.configured ? settings.api_key_masked : 'sk-...'}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="llm-model">模型名</Label>
                <Input
                  id="llm-model"
                  placeholder="如 gpt-4o-mini / deepseek-chat"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                />
              </div>
            </div>

            {/* 操作按钮 */}
            <div className="flex items-center gap-3">
              <Button onClick={handleSave} disabled={saving}>
                {saving && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                保存配置
              </Button>
              <Button variant="outline" onClick={handleTest} disabled={testing || !settings?.configured}>
                {testing ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <PlugZap className="mr-1.5 h-4 w-4 text-gold" />
                )}
                测试连接
              </Button>
            </div>

            {/* 测试结果 */}
            {testResult && (
              <div
                className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                  testResult.ok
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                    : 'border-red-400/40 bg-red-400/10 text-red-700 dark:text-red-300'
                }`}
              >
                {testResult.ok ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                ) : (
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
                )}
                <span>{testResult.message}</span>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** 联网搜索配置卡（v1.6.0 P0）：开关 + provider + key */
function WebSearchSettingsCard({ offline }: { offline: boolean }) {
  const [settings, setSettings] = useState<WebSearchSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [provider, setProvider] = useState('tavily');
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const s = await fetchSearchSettings();
      setSettings(s);
      setEnabled(s.enabled);
      setProvider(s.provider);
    } catch {
      /* api 层已 toast */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!offline) void load();
    else setLoading(false);
  }, [offline, load]);

  async function handleSave() {
    if (enabled && !apiKey.trim() && !settings?.configured) {
      toast.error('开启联网搜索需要填写 API key');
      return;
    }
    setSaving(true);
    try {
      await saveSearchSettings({
        enabled,
        provider,
        api_key: apiKey.trim(), // 空串=保留旧 key
      });
      toast.success('联网搜索配置已保存');
      setApiKey('');
      await load();
    } catch {
      /* 已 toast */
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Globe className="h-4 w-4 text-gold" />
          联网搜索（深度研究）
        </CardTitle>
        <CardDescription>
          为科研助手提供实时联网与学术检索能力（Tavily / Serper 任选；
          学术检索 arXiv / PubMed / Semantic Scholar / Crossref 免费直连无需配置）。
          默认关闭，未配置时助手自动隐藏联网工具。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <Label htmlFor="ws-enabled">启用联网搜索</Label>
                <p className="text-xs text-muted-foreground">
                  {settings?.configured
                    ? `已配置（key ${settings.api_key_masked}）`
                    : settings?.reason || '未配置'}
                </p>
              </div>
              <Switch
                id="ws-enabled"
                checked={enabled}
                onCheckedChange={setEnabled}
              />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="ws-provider">搜索供应商</Label>
              <select
                id="ws-provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              >
                <option value="tavily">Tavily（推荐，面向 AI Agent）</option>
                <option value="serper">Serper（Google 搜索）</option>
              </select>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="ws-key">API Key</Label>
              <Input
                id="ws-key"
                type="password"
                placeholder={settings?.configured ? '留空表示保留现有 key' : '粘贴搜索 API key'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>

            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              保存联网搜索配置
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** 助手记忆卡：编译/注入开关 + 查看编辑 + 清空 */
function AssistantMemoryCard({ offline }: { offline: boolean }) {
  const [info, setInfo] = useState<AssistantMemoryInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [savingContent, setSavingContent] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  // v1.6.0 P2：按单体组记忆 + 技能
  const [pairMems, setPairMems] = useState<PairMemoryMeta[]>([]);
  const [skills, setSkills] = useState<AssistantSkill[]>([]);
  const [skillBusy, setSkillBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setInfo(await fetchAssistantMemory());
      setPairMems((await fetchPairMemories()).memories ?? []);
      setSkills((await fetchSkills()).skills ?? []);
    } catch {
      /* 离线或失败：api 层已处理 */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!offline) void load();
    else setLoading(false);
  }, [offline, load]);

  async function handleToggle(enabled: boolean) {
    setToggling(true);
    try {
      setInfo(await updateAssistantMemory({ enabled }));
      toast.success(enabled ? '助手记忆已启用' : '助手记忆已停用');
    } catch {
      /* 已 toast */
    } finally {
      setToggling(false);
    }
  }

  async function handleSaveContent() {
    setSavingContent(true);
    try {
      setInfo(await updateAssistantMemory({ content: draft }));
      toast.success('记忆已保存');
      setViewOpen(false);
    } catch {
      /* 已 toast */
    } finally {
      setSavingContent(false);
    }
  }

  async function handleClear() {
    setClearing(true);
    try {
      setInfo(await clearAssistantMemory());
      toast.success('助手记忆已清空');
      setClearOpen(false);
    } catch {
      /* 已 toast */
    } finally {
      setClearing(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Brain className="h-4 w-4 text-gold" />
          助手记忆
          {info && (
            <Badge
              variant="outline"
              className={
                info.enabled
                  ? 'border-gold/60 bg-gold-muted text-gold-foreground'
                  : 'border-border bg-muted text-muted-foreground'
              }
            >
              {info.enabled ? '已启用' : '已停用'}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          会话结束时提炼「值得长期记住的事」存入本机 memory.md，新会话开局自动注入。
          数据仅存本机，可随时查看与清空。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : offline ? (
          <p className="text-sm text-muted-foreground">后端未连接，暂无法管理助手记忆。</p>
        ) : (
          <>
            <div className="flex items-center justify-between rounded-lg border border-border bg-muted/40 px-3 py-2.5">
              <div className="text-sm">
                <div className="font-medium text-foreground">记忆编译与注入</div>
                <div className="text-xs text-muted-foreground">
                  {info ? `当前共 ${info.entries} 条记忆` : '读取中…'}
                </div>
              </div>
              <Switch
                checked={info?.enabled ?? true}
                disabled={toggling || !info}
                onCheckedChange={handleToggle}
                aria-label="记忆编译与注入开关"
              />
            </div>
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                onClick={() => {
                  setDraft(info?.content ?? '');
                  setViewOpen(true);
                }}
              >
                <Eye className="mr-1.5 h-4 w-4" />
                查看记忆
              </Button>
              <Button
                variant="outline"
                className="text-destructive hover:text-destructive"
                onClick={() => setClearOpen(true)}
                disabled={!info || info.entries === 0}
              >
                <Trash2 className="mr-1.5 h-4 w-4" />
                清空记忆
              </Button>
            </div>

            {/* 按单体组记忆（v1.6.0 P2） */}
            <div className="rounded-lg border border-border bg-muted/40 px-3 py-2.5">
              <div className="text-sm font-medium text-foreground">
                按单体组记忆
                <span className="ml-1.5 text-xs text-muted-foreground">
                  （讨论该组时自动注入该组专属记忆）
                </span>
              </div>
              {pairMems.length === 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  暂无。在科研助手讨论具体单体组后自动生成。
                </p>
              ) : (
                <ul className="mt-1.5 space-y-1">
                  {pairMems.map((m) => (
                    <li key={m.key} className="flex items-center gap-2 text-xs">
                      <span className="min-w-0 flex-1 truncate text-foreground/90"
                            title={m.key}>
                        {m.label}
                        <span className="ml-1.5 text-muted-foreground">
                          {m.entries} 条 · {(m.updated_at || '').slice(0, 10)}
                        </span>
                      </span>
                      <button
                        type="button"
                        title="清除该组记忆"
                        className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive"
                        onClick={async () => {
                          try {
                            await deletePairMemory(m.key);
                            setPairMems((prev) =>
                              prev.filter((x) => x.key !== m.key));
                            toast.success('已清除该组记忆');
                          } catch {
                            /* 已 toast */
                          }
                        }}
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* 技能（v1.6.0 P2 SKILLS） */}
            <div className="rounded-lg border border-border bg-muted/40 px-3 py-2.5">
              <div className="text-sm font-medium text-foreground">
                助手技能
                <span className="ml-1.5 text-xs text-muted-foreground">
                  （方法论开关；可在用户数据目录 skills/ 放同名 md 覆盖）
                </span>
              </div>
              <ul className="mt-1.5 space-y-1.5">
                {skills.map((s) => (
                  <li key={s.name} className="flex items-center gap-2 text-xs">
                    <span className="min-w-0 flex-1">
                      <span className="text-foreground/90">{s.name}</span>
                      <span className="block truncate text-muted-foreground"
                            title={s.description}>
                        {s.description}
                      </span>
                    </span>
                    <Switch
                      checked={s.enabled}
                      disabled={skillBusy === s.name}
                      onCheckedChange={async (v) => {
                        setSkillBusy(s.name);
                        try {
                          const res = await setSkillEnabled(s.name, v);
                          setSkills(res.skills ?? skills);
                        } catch {
                          /* 已 toast */
                        } finally {
                          setSkillBusy(null);
                        }
                      }}
                      aria-label={`技能 ${s.name} 开关`}
                    />
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </CardContent>

      {/* 查看 / 编辑记忆 */}
      <Dialog open={viewOpen} onOpenChange={setViewOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>助手记忆（memory.md）</DialogTitle>
            <DialogDescription>
              每行一条，格式「- [日期] 内容」。可直接编辑后保存；清空请用设置页的「清空记忆」。
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={14}
            className="font-mono text-xs"
            placeholder="暂无记忆"
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setViewOpen(false)}>
              取消
            </Button>
            <Button onClick={handleSaveContent} disabled={savingContent}>
              {savingContent && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 清空确认 */}
      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>清空助手记忆？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除本机 memory.md 中的全部 {info?.entries ?? 0} 条记忆，且不可恢复。
              助手之后的对话将无法引用这些历史偏好与教训。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleClear} disabled={clearing}>
              {clearing && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              确认清空
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

/** 后端状态卡 */
function BackendStatusCard({
  health,
  offline,
  loading,
}: {
  health: HealthInfo | null;
  offline: boolean;
  loading: boolean;
}) {
  const items: { label: string; ok: boolean | undefined; desc: string }[] = [
    { label: '树模型（Tree）', ok: health?.tree_available, desc: '快速打分主模型' },
    { label: 'GNN 模型', ok: health?.gnn_available, desc: '图神经网络辅助打分' },
    { label: '路由（Routing）', ok: health?.routing, desc: '打分策略路由' },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">后端状态</CardTitle>
        <CardDescription>FastAPI 服务与各模型组件的可用性。</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : offline ? (
          <p className="text-sm text-muted-foreground">
            后端未连接：请启动 FastAPI 服务（http://localhost:8000）。
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {items.map(({ label, ok, desc }) => (
              <li key={label} className="flex items-center justify-between py-2.5 text-sm">
                <div className="flex items-center gap-2.5">
                  <StatusDot ok={ok} />
                  <span className="font-medium text-foreground">{label}</span>
                  <span className="text-xs text-muted-foreground">{desc}</span>
                </div>
                <span className="text-xs text-muted-foreground">
                  {ok === undefined ? '未知' : ok ? '可用' : '不可用'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** ── 软件更新（Electron 桌面版）──────────────────────────── */

/** preload 暴露的更新 API 类型（浏览器 dev 模式下不存在） */
interface UpdaterStatusPayload {
  state: 'checking' | 'latest' | 'available' | 'downloading' | 'downloaded' | 'error';
  version?: string;
  percent?: number;
  message?: string;
}
interface UpdaterCheckResult {
  ok: boolean;
  state?: string;
  message?: string;
  currentVersion?: string;
  latestVersion?: string;
}
interface UpdaterApi {
  getVersion(): Promise<string>;
  check(): Promise<UpdaterCheckResult>;
  download(): Promise<void>;
  install(): Promise<void>;
  onStatus(cb: (payload: UpdaterStatusPayload) => void): () => void;
}

type UpdateUiState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'latest'; version?: string }
  | { kind: 'available'; version?: string }
  | { kind: 'downloading'; percent: number }
  | { kind: 'downloaded'; version?: string }
  | { kind: 'error'; message: string };

function getUpdater(): UpdaterApi | undefined {
  return (window as unknown as { updater?: UpdaterApi }).updater;
}

/** 软件更新卡 */
function SoftwareUpdateCard() {
  const updater = getUpdater();
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [ui, setUi] = useState<UpdateUiState>({ kind: 'idle' });

  useEffect(() => {
    if (!updater) return;
    let cancelled = false;
    updater.getVersion().then((v) => {
      if (!cancelled) setAppVersion(v);
    }).catch(() => {});
    const unsubscribe = updater.onStatus((p) => {
      switch (p.state) {
        case 'checking':
          setUi({ kind: 'checking' });
          break;
        case 'latest':
          setUi({ kind: 'latest', version: p.version });
          break;
        case 'available':
          setUi({ kind: 'available', version: p.version });
          break;
        case 'downloading':
          setUi({ kind: 'downloading', percent: p.percent ?? 0 });
          break;
        case 'downloaded':
          setUi({ kind: 'downloaded', version: p.version });
          break;
        case 'error':
          setUi({ kind: 'error', message: p.message || '检查更新失败，请稍后重试' });
          break;
      }
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [updater]);

  async function handleCheck() {
    if (!updater) return;
    setUi({ kind: 'checking' });
    try {
      const r = await updater.check();
      // 检查结果的细节由状态事件流驱动；这里只处理即时失败
      if (!r.ok && r.state !== 'checking') {
        setUi({ kind: 'error', message: r.message || '检查更新失败，请稍后重试' });
      }
    } catch {
      setUi({ kind: 'error', message: '检查更新失败，请稍后重试' });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">软件更新</CardTitle>
        <CardDescription>检查并安装桌面端新版本。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!updater ? (
          <p className="text-sm text-muted-foreground">仅桌面版可用：浏览器模式下无法检查应用更新。</p>
        ) : (
          <>
            <div className="text-sm text-muted-foreground">
              当前版本：
              <span className="font-medium text-foreground">
                {appVersion ? `v${appVersion}` : '读取中…'}
              </span>
            </div>

            {/* 状态提示 */}
            {ui.kind === 'latest' && (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <span>已是最新版本 ✅</span>
              </div>
            )}
            {ui.kind === 'available' && (
              <div className="rounded-lg border border-gold/50 bg-gold-muted/40 px-3 py-2 text-sm text-muted-foreground">
                发现新版本
                <span className="font-medium text-foreground">{ui.version ? ` v${ui.version}` : ''}</span>
                ，点击下方按钮开始下载。
              </div>
            )}
            {ui.kind === 'downloading' && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>
                    正在下载更新… <span className="font-medium text-foreground">{ui.percent}%</span>
                  </span>
                </div>
                <Progress value={ui.percent} className="h-2" />
              </div>
            )}
            {ui.kind === 'downloaded' && (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <span>
                  新版本{ui.version ? ` v${ui.version}` : ''}已下载完成，重启后生效。
                </span>
              </div>
            )}
            {ui.kind === 'error' && (
              <div className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                {ui.message}
              </div>
            )}

            {/* 操作按钮 */}
            <div className="flex items-center gap-3">
              {(ui.kind === 'idle' || ui.kind === 'latest' || ui.kind === 'error') && (
                <Button variant="outline" onClick={handleCheck}>
                  检查更新
                </Button>
              )}
              {ui.kind === 'checking' && (
                <Button variant="outline" disabled>
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  正在检查…
                </Button>
              )}
              {ui.kind === 'available' && (
                <Button
                  onClick={() => {
                    setUi({ kind: 'downloading', percent: 0 });
                    void updater.download();
                  }}
                >
                  下载更新{ui.version ? ` v${ui.version}` : ''}
                </Button>
              )}
              {ui.kind === 'downloaded' && (
                <Button onClick={() => void updater.install()}>重启安装</Button>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** 关于卡：版本三显（界面 / 后端 / 前端构建），截图即可确认真身 */
function AboutCard({
  health,
  offline,
  loading,
}: {
  health: HealthInfo | null;
  offline: boolean;
  loading: boolean;
}) {
  const updater = getUpdater();
  const [appVersion, setAppVersion] = useState<string | null>(null);

  useEffect(() => {
    if (!updater) return;
    let cancelled = false;
    updater.getVersion().then((v) => {
      if (!cancelled) setAppVersion(v);
    }).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [updater]);

  const rows: { label: string; value: string }[] = [
    {
      label: '界面（Electron）版本',
      value: updater ? (appVersion ? `v${appVersion}` : '读取中…') : '浏览器模式（无桌面壳）',
    },
    {
      label: '后端版本',
      value: loading
        ? '读取中…'
        : offline
          ? '后端未连接'
          : health?.version
            ? `v${health.version}`
            : '未知（旧后端无 version 字段）',
    },
    { label: '前端构建', value: __BUILD_HASH__ },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">关于</CardTitle>
        <CardDescription>三项版本标识不一致时，说明更新未完全生效，请完全退出软件后重开。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1.5 text-sm text-muted-foreground">
        <div>
          项目：<span className="font-medium text-foreground">COF 科研系统</span>
        </div>
        {rows.map(({ label, value }) => (
          <div key={label}>
            {label}：<span className="font-mono font-medium text-foreground">{value}</span>
          </div>
        ))}
        <p className="pt-1">
          界面内置三套配色主题（暖纸松石 / 石墨仪器 / 学术紫金），默认每次启动自动轮换，可在「配色主题」卡手动固定；支持明暗模式切换。
        </p>
      </CardContent>
    </Card>
  );
}

/** 主题卡：三配色方案选择（手动选择固定主题并停止重启轮换） */
function ThemeCard() {
  const { scheme, setScheme } = useTheme();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">配色主题</CardTitle>
        <CardDescription>
          默认每次启动自动轮换；手动选择后固定使用该主题。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {COLOR_SCHEMES.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setScheme(s)}
            className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
              scheme === s
                ? 'border-primary bg-primary/10 font-medium text-primary'
                : 'border-border hover:bg-accent'
            }`}
          >
            <span>{SCHEME_LABELS[s]}</span>
            {scheme === s && <CheckCircle2 className="h-4 w-4" />}
          </button>
        ))}
      </CardContent>
    </Card>
  );
}

export default function Settings() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [offline, setOffline] = useState(false);
  const [healthLoading, setHealthLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await fetchHealth();
        if (!cancelled) setHealth(h);
      } catch (e) {
        if (!cancelled && e instanceof BackendUnavailableError) setOffline(true);
      } finally {
        if (!cancelled) setHealthLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gradient-royal">设置</h1>
        <p className="mt-1 text-sm text-muted-foreground">LLM 配置、后端状态与关于信息</p>
      </div>

      {offline && (
        <div className="rounded-xl border border-dashed border-gold/50 bg-gold-muted/40 px-5 py-4 text-sm text-muted-foreground">
          后端未连接：请启动 FastAPI 服务（http://localhost:8000）后刷新页面。
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <LlmSettingsCard offline={offline} />
          <WebSearchSettingsCard offline={offline} />
          <AssistantMemoryCard offline={offline} />
          <GnnEvolutionPanel />
        </div>
        <div className="space-y-6">
          <BackendStatusCard health={health} offline={offline} loading={healthLoading} />
          <SoftwareUpdateCard />
          <ThemeCard />
          <AboutCard health={health} offline={offline} loading={healthLoading} />
        </div>
      </div>
    </div>
  );
}
