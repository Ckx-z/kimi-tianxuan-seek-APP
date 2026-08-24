/**
 * DFT 计算（占位页，v1.0.0 工具箱新模块，正式功能后续批次上线）
 * 规划能力：输入两个 COF 单体，半经验方法（GFN2-xTB / GFN-FF）计算
 * 单体间结合能、偶极矩、HOMO/LUMO 能隙，并查看优化后复合物 3D 结构。
 */
import { Atom, Calculator, Boxes, FileDown } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

const PLANNED_FEATURES = [
  {
    icon: Calculator,
    title: '结合能计算',
    desc: 'E_bind = E_复合物 − E_单体A − E_单体B，辅助判断缩合反应倾向（kcal/mol 与 kJ/mol）。',
  },
  {
    icon: Boxes,
    title: '单体量化性质',
    desc: '偶极矩、HOMO/LUMO 能隙等基础量化指标，配合可交互的优化后复合物 3D 结构查看。',
  },
  {
    icon: FileDown,
    title: '高精度复算导出',
    desc: '一键导出 Gaussian / ORCA 输入文件，送超算做真 DFT 高精度复核。',
  },
];

export default function Dft() {
  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-gradient-royal">
            DFT 计算
            <Badge
              variant="outline"
              className="border-gold/60 bg-gold-muted text-gold-foreground"
            >
              即将上线
            </Badge>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            基于半经验量子化学方法（GFN2-xTB）的单体间结合能计算，正在开发中
          </p>
        </div>
      </div>

      {/* 主占位卡 */}
      <Card>
        <CardContent className="flex flex-col items-center px-6 py-12 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-xl gradient-royal text-white">
            <Atom className="h-7 w-7" />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-foreground">功能即将上线</h2>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
            输入两个 COF 单体（SMILES / CAS，或从历史与收藏中选取），自动完成 3D 构象生成、
            几何优化与能量计算，给出单体间结合能等量化指标，辅助判断缩合反应倾向。
            计算结果支持收藏与历史记录回溯。
          </p>
          <p className="mt-3 max-w-xl rounded-lg border border-dashed border-gold/50 bg-gold-muted/40 px-4 py-2 text-xs text-muted-foreground">
            学术诚信提示：半经验方法结果仅供相对比较，精确能量请导出输入文件用 DFT 复算。
          </p>
        </CardContent>
      </Card>

      {/* 规划能力 */}
      <div className="grid gap-4 md:grid-cols-3">
        {PLANNED_FEATURES.map(({ icon: Icon, title, desc }) => (
          <Card key={title}>
            <CardHeader className="flex flex-row items-center gap-2 pb-2">
              <Icon className="h-4 w-4 text-gold" />
              <CardTitle className="text-sm font-medium">{title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs leading-relaxed text-muted-foreground">{desc}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
