/**
 * 内置单体库多选器：醛列 + 胺列复选，组合数为 N × M 笛卡尔积
 */
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import type { MonomerItem } from './api';

interface Props {
  aldehydes: MonomerItem[];
  amines: MonomerItem[];
  selectedAld: Set<string>;
  selectedAmine: Set<string>;
  onToggleAld: (smiles: string) => void;
  onToggleAmine: (smiles: string) => void;
}

/** 单列单体复选列表 */
function MonomerColumn({
  title,
  items,
  selected,
  onToggle,
}: {
  title: string;
  items: MonomerItem[];
  selected: Set<string>;
  onToggle: (smiles: string) => void;
}) {
  return (
    <div className="flex-1 min-w-0">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">{title}</span>
        <span className="text-xs text-muted-foreground">已选 {selected.size}</span>
      </div>
      <ScrollArea className="h-56 rounded-lg border border-border">
        <ul className="divide-y divide-border">
          {items.map((m) => {
            const id = `${title}-${m.smiles}`;
            const checked = selected.has(m.smiles);
            return (
              <li key={m.smiles}>
                <Label
                  htmlFor={id}
                  className="flex cursor-pointer items-start gap-2 px-3 py-2 hover:bg-muted/50"
                >
                  <Checkbox
                    id={id}
                    checked={checked}
                    onCheckedChange={() => onToggle(m.smiles)}
                    className="mt-0.5"
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm text-foreground">{m.name}</span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">
                      {m.smiles}
                    </span>
                  </span>
                </Label>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
    </div>
  );
}

export default function MonomerPicker({
  aldehydes,
  amines,
  selectedAld,
  selectedAmine,
  onToggleAld,
  onToggleAmine,
}: Props) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row">
      <MonomerColumn title="醛单体" items={aldehydes} selected={selectedAld} onToggle={onToggleAld} />
      <MonomerColumn title="胺单体" items={amines} selected={selectedAmine} onToggle={onToggleAmine} />
    </div>
  );
}
