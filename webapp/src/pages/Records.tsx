/**
 * 实验记录页（任务C，对照旧 Gradio 页④）
 * 左右分栏：左栏录入表单（2/5），右栏时间线（3/5）
 * 数据请求经 ./components/records/api 本地封装（后端 /api/records、/api/favorites）
 */
import { useCallback, useEffect, useState } from 'react';
import RecordForm from '@/components/records/RecordForm';
import RecordTimeline from '@/components/records/RecordTimeline';
import {
  BackendUnavailableError,
  listFavorites,
  listRecords,
  type FavoriteItem,
  type RecordItem,
} from '@/components/records/api';

export default function Records() {
  const [favorites, setFavorites] = useState<FavoriteItem[]>([]);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [favoriteId, setFavoriteId] = useState('');
  const [onlySelected, setOnlySelected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [backendDown, setBackendDown] = useState(false);

  /** 加载收藏列表（表单下拉用）；失败静默降级（时间线区统一提示） */
  const loadFavorites = useCallback(async () => {
    try {
      setFavorites(await listFavorites());
      return true;
    } catch {
      return false;
    }
  }, []);

  /** 加载记录列表（按「只看选中收藏」过滤） */
  const loadRecords = useCallback(
    async (only: boolean, favId: string) => {
      setLoading(true);
      try {
        setRecords(await listRecords(only && favId ? favId : undefined));
        setBackendDown(false);
      } catch (err) {
        setBackendDown(err instanceof BackendUnavailableError);
        setRecords([]);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  /** 初始加载 */
  useEffect(() => {
    void loadFavorites();
    void loadRecords(false, '');
  }, [loadFavorites, loadRecords]);

  /** 刷新：同时重拉收藏与记录 */
  const handleRefresh = useCallback(() => {
    void loadFavorites();
    void loadRecords(onlySelected, favoriteId);
  }, [loadFavorites, loadRecords, onlySelected, favoriteId]);

  /** 过滤开关切换后重新加载 */
  const handleOnlySelectedChange = (on: boolean) => {
    setOnlySelected(on);
    void loadRecords(on, favoriteId);
  };

  /** 切换收藏：若开启过滤则随之刷新列表 */
  const handleFavoriteChange = (id: string) => {
    setFavoriteId(id);
    if (onlySelected && id) void loadRecords(true, id);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">实验记录</h1>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* 左栏：录入表单 2/5 */}
        <div className="lg:col-span-2">
          <RecordForm
            favorites={favorites}
            favoriteId={favoriteId}
            onFavoriteChange={handleFavoriteChange}
            onSaved={handleRefresh}
          />
        </div>
        {/* 右栏：时间线 3/5 */}
        <div className="lg:col-span-3">
          <RecordTimeline
            records={records}
            loading={loading}
            backendDown={backendDown}
            onlySelected={onlySelected}
            onOnlySelectedChange={handleOnlySelectedChange}
            favoriteId={favoriteId}
            onRefresh={handleRefresh}
          />
        </div>
      </div>
    </div>
  );
}
