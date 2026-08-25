/**
 * 化学结构画板封装（D2.0b · docs/DFT2.0设计方案.md §二，选型 epam/ketcher，Apache-2.0）
 *
 * 「画结构」按钮 → Dialog 内嵌 Ketcher（React.lazy 懒加载，不进首屏 chunk）
 * →「确定」通过 ketcher.getSmiles() 把 SMILES 回填到目标输入框。
 * 支持传入当前 SMILES 作为初始结构继续编辑。
 *
 * 用法：
 *   <StructureSketcher value={smiles} onChange={setSmiles} />
 */
import { lazy, Suspense, useRef, useState } from 'react';
import { PencilLine } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
// 仅类型导入：编译后擦除，不会把 ketcher 拉进首屏 chunk
import type { Ketcher } from 'ketcher-core';

const KetcherPanel = lazy(() => import('./KetcherPanel'));

interface Props {
  /** 当前 SMILES（打开画板时作为初始结构） */
  value?: string;
  /** 「确定」后回填 SMILES */
  onChange: (smiles: string) => void;
  disabled?: boolean;
  /** 对话框标题，默认「绘制化学结构」 */
  title?: string;
}

export default function StructureSketcher({
  value,
  onChange,
  disabled,
  title = '绘制化学结构',
}: Props) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const ketcherRef = useRef<Ketcher | null>(null);

  /** 「确定」：取画板当前结构的 SMILES 回填；空画板则提示不回填 */
  const handleConfirm = async () => {
    const ketcher = ketcherRef.current;
    if (!ketcher) {
      toast.warning('画板尚未加载完成，请稍候再试');
      return;
    }
    setConfirming(true);
    try {
      const smiles = (await ketcher.getSmiles()).trim();
      if (!smiles) {
        toast.warning('画板为空，请先绘制结构（或直接关闭对话框）');
        return;
      }
      onChange(smiles);
      setOpen(false);
      toast.success('已回填 SMILES');
    } catch (e) {
      toast.error(`获取 SMILES 失败：${e instanceof Error ? e.message : '未知错误'}`);
    } finally {
      setConfirming(false);
    }
  };

  return (
    <>
      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        onClick={() => setOpen(true)}
        title="打开结构画板，绘制后回填 SMILES"
      >
        <PencilLine className="mr-1 h-4 w-4" />
        画结构
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex h-[85vh] w-[calc(100vw-1.5rem)] max-w-5xl flex-col">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>
              在画板中绘制或编辑结构（支持键型、环、官能团模板；画板界面为英文）。
              点击「确定」将结构的 SMILES 回填到输入框。
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-hidden rounded-md border bg-white">
            {open && (
              <Suspense
                fallback={
                  <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                    画板首次加载中（需载入化学内核，约数秒）…
                  </div>
                }
              >
                <KetcherPanel
                  initialSmiles={value}
                  ketcherRef={ketcherRef}
                  onInitError={(msg) => toast.warning(msg)}
                />
              </Suspense>
            )}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button onClick={() => void handleConfirm()} disabled={confirming}>
              {confirming ? '获取中…' : '确定'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
