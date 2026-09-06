/**
 * 「文献解析 LLM」设置卡（v1.9.0）：科研知识库文献结构化提取的独立 LLM 配置
 * - 与助手 LLM / 联网搜索 key 完全隔离（独立 base_url/api_key/model）
 * - embedding 提供方三态：off（默认，图检索兜底）/ local（dphuanjing +
 *   bge-m3，安装见 scripts/install_lit_embedding.bat）/ online
 */
import { useCallback, useEffect, useState } from 'react';
import { Brain, CheckCircle2, Loader2, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

const BASE = '/api/literature';

interface Settings {
  enabled: boolean;
  base_url: string;
  api_key: string;
  model: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_api_key: string;
}

interface EmbedStatus {
  provider: string;
  available: boolean;
  reason: string;
  model?: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
    });
  } catch {
    throw new Error('无法连接后端服务');
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      /* keep */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

export function LiteratureLlmSettingsCard({ offline }: { offline: boolean }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [embed, setEmbed] = useState<EmbedStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [newKey, setNewKey] = useState('');       // 新 key 输入（保存后清空）
  const [newEmbedKey, setNewEmbedKey] = useState('');

  const load = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([
        req<Settings>('/llm-settings'),
        req<EmbedStatus>('/embedding-status'),
      ]);
      setSettings(s);
      setEmbed(e);
    } catch {
      /* 页面级 offline 提示兜底 */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!settings) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        enabled: settings.enabled,
        base_url: settings.base_url,
        model: settings.model,
        embedding_provider: settings.embedding_provider,
        embedding_model: settings.embedding_model,
      };
      if (newKey.trim()) body.api_key = newKey.trim();
      if (newEmbedKey.trim()) body.embedding_api_key = newEmbedKey.trim();
      await req('/llm-settings', { method: 'PUT', body: JSON.stringify(body) });
      toast.success('文献解析设置已保存');
      setNewKey('');
      setNewEmbedKey('');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await req<{ ok: boolean; message: string }>(
        '/llm-settings/test', { method: 'POST' });
      setTestResult(r);
      toast.success(r.message);
    } catch (e) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : '测试失败' });
    } finally {
      setTesting(false);
    }
  };

  if (!settings) {
    return (
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">
          加载文献解析设置中…
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Brain className="h-4 w-4 text-gold" />
          文献解析 LLM
        </CardTitle>
        <CardDescription>
          用于把文献全文提取为结构化知识（单体/成膜体系/条件/表征/DFT），
          独立于助手 LLM 与联网搜索；关闭时降级为 SMILES 正则扫描。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <Label htmlFor="lit-llm-enabled">启用解析</Label>
          <Switch
            id="lit-llm-enabled"
            checked={settings.enabled}
            onCheckedChange={(v) => setSettings((s) => (s ? { ...s, enabled: v } : s))}
            disabled={offline}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="lit-llm-url">API 地址（OpenAI 兼容）</Label>
          <Input
            id="lit-llm-url"
            value={settings.base_url}
            onChange={(e) => setSettings((s) => (s ? { ...s, base_url: e.target.value } : s))}
            placeholder="https://api.example.com/v1"
            disabled={offline}
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="lit-llm-key">API Key</Label>
            <Input
              id="lit-llm-key"
              type="password"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder={settings.api_key ? `已配置（${settings.api_key}），留空不改` : '未配置'}
              disabled={offline}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lit-llm-model">模型</Label>
            <Input
              id="lit-llm-model"
              value={settings.model}
              onChange={(e) => setSettings((s) => (s ? { ...s, model: e.target.value } : s))}
              placeholder="如 deepseek-chat"
              disabled={offline}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label>embedding 提供方</Label>
          <Select
            value={settings.embedding_provider}
            onValueChange={(v) => setSettings((s) => (s ? { ...s, embedding_provider: v } : s))}
            disabled={offline}
          >
            <SelectTrigger aria-label="embedding 提供方">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="off">关闭（图检索 + 文本匹配兜底）</SelectItem>
              <SelectItem value="local">本地离线（dphuanjing + bge-m3）</SelectItem>
              <SelectItem value="online">在线（DashScope text-embedding-v4）</SelectItem>
            </SelectContent>
          </Select>
          {embed && (
            <p className={`flex items-center gap-1 text-xs ${embed.available ? 'text-emerald-600' : 'text-muted-foreground'}`}>
              {embed.available ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
              {embed.available
                ? `本地 embedding 可用（${embed.model ?? ''}）`
                : embed.reason}
            </p>
          )}
        </div>
        {settings.embedding_provider === 'local' && (
          <div className="space-y-1.5">
            <Label htmlFor="lit-emb-model">本地模型（名称或路径）</Label>
            <Input
              id="lit-emb-model"
              value={settings.embedding_model}
              onChange={(e) => setSettings((s) => (s ? { ...s, embedding_model: e.target.value } : s))}
              placeholder="BAAI/bge-m3"
              disabled={offline}
            />
          </div>
        )}
        {settings.embedding_provider === 'online' && (
          <div className="space-y-1.5">
            <Label htmlFor="lit-emb-key">在线 embedding Key</Label>
            <Input
              id="lit-emb-key"
              type="password"
              value={newEmbedKey}
              onChange={(e) => setNewEmbedKey(e.target.value)}
              placeholder={settings.embedding_api_key
                ? `已配置（${settings.embedding_api_key}），留空不改` : '未配置'}
              disabled={offline}
            />
          </div>
        )}

        <div className="flex items-center gap-2">
          <Button onClick={() => void save()} disabled={busy || offline}>
            {busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
            保存设置
          </Button>
          <Button variant="outline" onClick={() => void test()} disabled={testing || offline}>
            {testing && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
            测试连接
          </Button>
          {testResult && (
            <span className={`text-xs ${testResult.ok ? 'text-emerald-600' : 'text-red-500'}`}>
              {testResult.message}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
