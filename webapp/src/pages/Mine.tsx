/**
 * 我的
 * - 顶部三张统计小卡：收藏数 / 方案数 / 实验记录数
 * - 「我的收藏」卡片墙（含详情 Dialog 与删除确认）
 * - 「我的方案库」方案列表（可展开查看完整方案）
 * - 「我的数据」一键导出 JSON 备份（favorites / records / plans / suggestions）
 * - 后端未连接时优雅降级提示，不白屏
 */
import { useCallback, useEffect, useState } from 'react';
import { Star, ClipboardList, FlaskConical, Download, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { healthApi, BackendUnavailableError } from '@/lib/api';
import {
  fetchFavorites,
  fetchAllRecords,
  fetchPlans,
  fetchSuggestions,
  type FavoriteItem,
  type PlanItem,
  type RecordItem,
} from '@/components/mine/api';
import { FavoritesSection } from '@/components/mine/FavoritesSection';
import { PlansSection } from '@/components/mine/PlansSection';

/** 导出备份：打包四类数据为 JSON Blob 下载 */
async function exportBackup() {
  try {
    const [favorites, records, plans, suggestions] = await Promise.all([
      fetchFavorites(),
      fetchAllRecords(),
      fetchPlans(),
      fetchSuggestions(),
    ]);
    const payload = {
      exported_at: new Date().toISOString(),
      favorites,
      records,
      plans,
      suggestions,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stamp = new Date().toISOString().slice(0, 10).replaceAll('-', '');
    a.href = url;
    a.download = `cof_backup_${stamp}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success('数据备份已导出');
  } catch {
    /* 错误已由 api 层 toast */
  }
}

export default function Mine() {
  const [favorites, setFavorites] = useState<FavoriteItem[]>([]);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [plans, setPlans] = useState<PlanItem[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  /** 加载全部数据（先静默探活，离线则降级） */
  const load = useCallback(async () => {
    setLoading(true);
    try {
      await healthApi.check();
      setOnline(true);
      const [fav, rec, pln] = await Promise.all([fetchFavorites(), fetchAllRecords(), fetchPlans()]);
      setFavorites(fav);
      setRecords(rec);
      setPlans(pln);
    } catch (e) {
      if (e instanceof BackendUnavailableError) setOnline(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const stats = [
    { label: '收藏数', value: favorites.length, icon: Star },
    { label: '方案数', value: plans.length, icon: ClipboardList },
    { label: '实验记录数', value: records.length, icon: FlaskConical },
  ];

  return (
    <div className="space-y-8">
      {/* 页头 */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gradient-royal">我的</h1>
          <p className="mt-1 text-sm text-muted-foreground">收藏、方案与实验数据的个人中心</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {/* 后端未连接降级提示 */}
      {online === false && (
        <div className="rounded-xl border border-dashed border-gold/50 bg-gold-muted/40 px-5 py-4 text-sm text-muted-foreground">
          后端未连接：请启动 FastAPI 服务（http://localhost:8000）后点击「刷新」。
        </div>
      )}

      {/* 三张统计小卡 */}
      <div className="grid grid-cols-3 gap-4">
        {stats.map(({ label, value, icon: Icon }) => (
          <Card key={label}>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
              <Icon className="h-4 w-4 text-gold" />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold text-primary">{loading ? '—' : value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* 我的收藏 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">我的收藏</h2>
        <FavoritesSection favorites={favorites} loading={loading} onChanged={() => void load()} />
      </section>

      {/* 我的方案库 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">我的方案库</h2>
        <PlansSection plans={plans} loading={loading} />
      </section>

      {/* 我的数据：导出备份 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">我的数据</h2>
        <Card>
          <CardContent className="flex items-center justify-between gap-4 p-4">
            <div className="text-sm text-muted-foreground">
              将收藏、实验记录、方案与迭代建议打包导出为 JSON 备份文件（cof_backup_YYYYMMDD.json）。
            </div>
            <Button
              onClick={async () => {
                setExporting(true);
                await exportBackup();
                setExporting(false);
              }}
              disabled={exporting || online === false}
            >
              <Download className="mr-1.5 h-4 w-4" />
              {exporting ? '导出中…' : '导出备份'}
            </Button>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
