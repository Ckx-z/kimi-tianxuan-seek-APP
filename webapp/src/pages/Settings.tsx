/**
 * 设置
 * - LLM 配置卡：查看当前配置（掩码 key / 来源）+ 表单保存 + 测试连接
 * - 后端状态卡：tree / gnn / routing 可用性
 * - 关于卡：项目名 / 版本 / 主题说明
 * - 后端未连接时优雅降级，不白屏
 */
import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, XCircle, Loader2, PlugZap } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { BackendUnavailableError } from '@/lib/api';
import {
  fetchLlmSettings,
  saveLlmSettings,
  testLlmConnection,
  fetchHealth,
  type LlmSettings,
  type HealthInfo,
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
        <LlmSettingsCard offline={offline} />
        <div className="space-y-6">
          <BackendStatusCard health={health} offline={offline} loading={healthLoading} />

          {/* 关于 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">关于</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 text-sm text-muted-foreground">
              <div>
                项目：<span className="font-medium text-foreground">COF 成膜推荐系统</span>
              </div>
              <div>
                后端版本：<span className="font-medium text-foreground">0.1.0</span>
              </div>
              <p className="pt-1">
                界面采用紫金主题——紫色为主色调象征科研理性，金色点缀致敬学术荣光，支持明暗模式自动切换。
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
