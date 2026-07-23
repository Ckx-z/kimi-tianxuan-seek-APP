/**
 * 单体输入通道（醛 / 胺 共用）
 * 三种输入方式：①SMILES 直输 ②CAS 号经 PubChem PUG REST 解析 ③内置单体库下拉
 */
import { useState } from 'react';
import { toast } from 'sonner';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { BuiltinMonomer } from './api';

export interface MonomerValue {
  smiles: string;
  name: string;
}

interface Props {
  title: string; // 如「醛单体」
  role: 'aldehyde' | 'amine';
  value: MonomerValue;
  onChange: (v: MonomerValue) => void;
  library: BuiltinMonomer[]; // 该角色对应的内置库分组
  libraryLoading: boolean;
  disabled?: boolean;
}

/** 调 PubChem PUG REST 将 CAS/名称解析为 Canonical SMILES（CORS 开放，浏览器直连） */
async function resolveCasToSmiles(cas: string): Promise<string> {
  const url = `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/${encodeURIComponent(cas)}/property/CanonicalSMILES/JSON`;
  let res: Response;
  try {
    res = await fetch(url);
  } catch {
    throw new Error('无法访问 PubChem，请检查网络');
  }
  if (!res.ok) throw new Error(`未在 PubChem 找到「${cas}」对应的化合物`);
  const data = await res.json();
  const smiles = data?.PropertyTable?.Properties?.[0]?.CanonicalSMILES;
  if (!smiles) throw new Error(`「${cas}」解析结果中没有 SMILES`);
  return smiles as string;
}

export default function MonomerInput({ title, value, onChange, library, libraryLoading, disabled }: Props) {
  const [cas, setCas] = useState('');
  const [resolving, setResolving] = useState(false);

  /** CAS 解析 */
  const handleResolve = async () => {
    const q = cas.trim();
    if (!q) {
      toast.warning('请先输入 CAS 号');
      return;
    }
    setResolving(true);
    try {
      const smiles = await resolveCasToSmiles(q);
      onChange({ smiles, name: q });
      toast.success(`解析成功：${q} → ${smiles}`);
    } catch (e) {
      toast.error(`CAS 解析失败：${e instanceof Error ? e.message : '未知错误'}`);
    } finally {
      setResolving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-foreground">{title}</h3>
        {value.smiles && (
          <span className="max-w-[60%] truncate text-xs text-muted-foreground" title={value.smiles}>
            当前：{value.smiles}
          </span>
        )}
      </div>
      <Tabs defaultValue="smiles">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="smiles">SMILES 直输</TabsTrigger>
          <TabsTrigger value="cas">CAS 号解析</TabsTrigger>
          <TabsTrigger value="library">内置单体库</TabsTrigger>
        </TabsList>

        {/* 通道①：SMILES 直输 */}
        <TabsContent value="smiles" className="space-y-2 pt-2">
          <Label>SMILES</Label>
          <Input
            placeholder="如 O=Cc1ccccc1"
            value={value.smiles}
            disabled={disabled}
            onChange={(e) => onChange({ smiles: e.target.value.trim(), name: value.name })}
          />
          <Label>名称（选填，用于性质卡与方案卡展示）</Label>
          <Input
            placeholder="如 苯甲醛"
            value={value.name}
            disabled={disabled}
            onChange={(e) => onChange({ smiles: value.smiles, name: e.target.value })}
          />
        </TabsContent>

        {/* 通道②：CAS 号 → PubChem 解析 */}
        <TabsContent value="cas" className="space-y-2 pt-2">
          <Label>CAS 号 / 英文名</Label>
          <div className="flex gap-2">
            <Input
              placeholder="如 100-52-7"
              value={cas}
              disabled={disabled || resolving}
              onChange={(e) => setCas(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleResolve()}
            />
            <Button variant="outline" onClick={handleResolve} disabled={disabled || resolving}>
              {resolving ? '解析中…' : '解析'}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            通过 PubChem PUG REST API 解析为 SMILES 后自动填入。
          </p>
        </TabsContent>

        {/* 通道③：内置单体库下拉 */}
        <TabsContent value="library" className="space-y-2 pt-2">
          <Label>从内置库选择</Label>
          <Select
            disabled={disabled || libraryLoading || library.length === 0}
            onValueChange={(smiles) => {
              const m = library.find((x) => x.smiles === smiles);
              if (m) onChange({ smiles: m.smiles, name: m.name });
            }}
          >
            <SelectTrigger>
              <SelectValue
                placeholder={libraryLoading ? '单体库加载中…' : library.length === 0 ? '库为空（后端未连接？）' : '选择单体'}
              />
            </SelectTrigger>
            <SelectContent>
              {library.map((m) => (
                <SelectItem key={m.smiles} value={m.smiles}>
                  {m.name}{m.cas ? `（CAS ${m.cas}）` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {value.name && <p className="text-xs text-muted-foreground">已选：{value.name}</p>}
        </TabsContent>
      </Tabs>
    </div>
  );
}
