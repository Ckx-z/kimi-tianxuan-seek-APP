/**
 * 实验记录页（任务C，对照旧 Gradio 页④）
 * 左右分栏：左栏录入表单（2/5），右栏时间线（3/5）
 * 数据请求经 ./components/records/api 本地封装（后端 /api/records、/api/favorites）
 */
import { useCallback, useEffect, useState } from 'react';
import RecordForm from '@/components/records/RecordForm';
import RecordTimeline from '@/components/records/RecordTimeline';
import { PageHeader } from '@/components/layout/PageHeader';
import {
  BackendUnavailableError,
  listFavorites,
  listFolders,
  listRecords,
  type FavoriteItem,
  type FolderItem,
  type RecordItem,
} from '@/components/records/api';
import { PROGRESS_FILTER_ALL } from '@/components/records/meta';

export default function Records() {
  const [favorites, setFavorites] = useState<FavoriteItem[]>([]);
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [favoriteId, setFavoriteId] = useState('');
  const [onlySelected, setOnlySelected] = useState(false);
  /** 实验进展筛选：PROGRESS_FILTER_ALL 表示全部（其余为 running/finished/xrd_done） */
  const [progressFilter, setProgressFilter] = useState<string>(PROGRESS_FILTER_ALL);
  const [loading, setLoading] = useState(true);
  const [backendDown, setBackendDown] = useState(false);

  /** 加载收藏列表（表单下拉用）与收藏夹（下拉分组用）；失败静默降级 */
  const loadFavorites = useCallback(async () => {
    try {
      const [favs, folds] = await Promise.all([
        listFavorites(),
        listFolders().catch(() => [] as FolderItem[]),
      ]);
      setFavorites(favs);
      setFolders(folds);
      return true;
    } catch {
      return false;
    }
  }, []);

  /** 加载记录列表（按「只看选中收藏」+ 实验进展筛选，均为服务端筛选） */
  const loadRecords = useCallback(
    async (only: boolean, favId: string, progress: string) => {
      setLoading(true);
      try {
        setRecords(
          await listRecords(
            only && favId ? favId : undefined,
            progress === PROGRESS_FILTER_ALL ? undefined : progress,
          ),
        );
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
    void loadRecords(false, '', PROGRESS_FILTER_ALL);
  }, [loadFavorites, loadRecords]);

  /** 刷新：同时重拉收藏与记录 */
  const handleRefresh = useCallback(() => {
    void loadFavorites();
    void loadRecords(onlySelected, favoriteId, progressFilter);
  }, [loadFavorites, loadRecords, onlySelected, favoriteId, progressFilter]);

  /** 过滤开关切换后重新加载 */
  const handleOnlySelectedChange = (on: boolean) => {
    setOnlySelected(on);
    void loadRecords(on, favoriteId, progressFilter);
  };

  /** 切换收藏：若开启过滤则随之刷新列表 */
  const handleFavoriteChange = (id: string) => {
    setFavoriteId(id);
    if (onlySelected && id) void loadRecords(true, id, progressFilter);
  };

  /** 切换实验进展筛选：立即重新加载 */
  const handleProgressFilterChange = (value: string) => {
    setProgressFilter(value);
    void loadRecords(onlySelected, favoriteId, value);
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="实验记录"
        subtitle="录入实验过程与结果，时间线自动归纳实验时间；支持附件与复盘"
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* 左栏：录入表单 2/5 */}
        <div className="lg:col-span-2">
          <RecordForm
            favorites={favorites}
            folders={folders}
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
            progressFilter={progressFilter}
            onProgressFilterChange={handleProgressFilterChange}
            onRefresh={handleRefresh}
          />
        </div>
      </div>
    </div>
  );
}
