/**
 * 通用收藏夹选择对话框
 * - 下拉选择目标收藏夹（默认上次使用的夹，记忆在 localStorage「cof_last_fav_folder」）
 * - 内置「新建收藏夹」输入项（重名 400 由 api 层弹中文提示）
 * - 用于：收藏创建（查询打分页 / DFT 页）、收藏卡片移动 / 复制
 * - 排除当前所在夹（excludeFolderId）用于移动 / 复制场景
 */
import { useCallback, useEffect, useState } from 'react';
import { FolderPlus } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { fetchFavoriteFolders, type FolderItem } from '@/components/query/api';
import { createFolder } from '@/components/mine/api';

const LAST_FOLDER_KEY = 'cof_last_fav_folder';

/** 读取上次使用的收藏夹 id */
export function getLastFolderId(): string {
  try {
    return localStorage.getItem(LAST_FOLDER_KEY) ?? '';
  } catch {
    return '';
  }
}

/** 记住本次使用的收藏夹 id */
export function setLastFolderId(id: string) {
  try {
    localStorage.setItem(LAST_FOLDER_KEY, id);
  } catch {
    /* 隐私模式等场景静默忽略 */
  }
}

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title?: string;
  description?: string;
  /** 排除的收藏夹（移动 / 复制时排除当前所在夹） */
  excludeFolderId?: string;
  /** 确认按钮文案 */
  confirmLabel?: string;
  /** 外部提交中状态（禁用按钮） */
  submitting?: boolean;
  /** 确认回调：传入目标收藏夹 id 与名称 */
  onConfirm: (folderId: string, folderName: string) => void | Promise<void>;
}

export default function FavoriteFolderDialog({
  open,
  onOpenChange,
  title = '选择收藏夹',
  description,
  excludeFolderId,
  confirmLabel = '确定',
  submitting = false,
  onConfirm,
}: Props) {
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);

  /** 可选夹列表（排除当前所在夹） */
  const options = excludeFolderId ? folders.filter((f) => f.id !== excludeFolderId) : folders;

  const load = useCallback(async () => {
    try {
      const list = await fetchFavoriteFolders();
      setFolders(list);
      // 默认选中：上次使用 > 收藏夹1 > 第一个
      setSelectedId((prev) => {
        if (prev && list.some((f) => f.id === prev && f.id !== excludeFolderId)) return prev;
        const last = getLastFolderId();
        const pick =
          list.find((f) => f.id === last && f.id !== excludeFolderId) ??
          list.find((f) => f.name === '收藏夹1' && f.id !== excludeFolderId) ??
          list.find((f) => f.id !== excludeFolderId);
        return pick?.id ?? '';
      });
    } catch {
      /* 静默：下拉显示空态 */
    }
  }, [excludeFolderId]);

  useEffect(() => {
    if (open) {
      setNewName('');
      void load();
    }
  }, [open, load]);

  /** 新建收藏夹并选中 */
  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) {
      toast.error('请输入收藏夹名称');
      return;
    }
    setCreating(true);
    try {
      const folder = await createFolder(name);
      toast.success(`已创建收藏夹「${folder.name}」`);
      setNewName('');
      setFolders((prev) => [...prev.filter((f) => f.id !== folder.id), folder]);
      setSelectedId(folder.id);
    } catch {
      /* 错误已由 api 层 toast（含重名 400 中文提示） */
    } finally {
      setCreating(false);
    }
  };

  const handleConfirm = () => {
    if (!selectedId) {
      toast.error('请选择目标收藏夹');
      return;
    }
    setLastFolderId(selectedId);
    const name = folders.find((f) => f.id === selectedId)?.name ?? '';
    void onConfirm(selectedId, name);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[calc(100vw-1.5rem)] sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>

        <div className="space-y-3">
          <Select value={selectedId} onValueChange={setSelectedId}>
            <SelectTrigger>
              <SelectValue placeholder={folders.length === 0 ? '收藏夹加载中…' : '选择目标收藏夹'} />
            </SelectTrigger>
            <SelectContent>
              {options.map((f) => (
                <SelectItem key={f.id} value={f.id}>
                  {f.name}
                  {typeof f.favorite_count === 'number' ? `（${f.favorite_count}）` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* 新建收藏夹 */}
          <div className="flex gap-2">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="新建收藏夹名称"
              maxLength={30}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleCreate();
              }}
            />
            <Button
              variant="outline"
              size="sm"
              className="shrink-0"
              disabled={creating || !newName.trim()}
              onClick={() => void handleCreate()}
            >
              <FolderPlus className="mr-1 h-3.5 w-3.5" />
              {creating ? '创建中…' : '新建'}
            </Button>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
              取消
            </Button>
            <Button onClick={handleConfirm} disabled={submitting || !selectedId}>
              {submitting ? '处理中…' : confirmLabel}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
