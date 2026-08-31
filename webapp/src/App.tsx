import { Routes, Route, Navigate } from 'react-router';
import { Toaster } from 'sonner';
import AppLayout from '@/components/layout/AppLayout';
import { DftTaskProvider } from '@/components/dft/DftTaskContext';
import Home from '@/pages/Home';
import Query from '@/pages/Query';
import Batch from '@/pages/Batch';
import Dft from '@/pages/Dft';
import Records from '@/pages/Records';
import Iterate from '@/pages/Iterate';
import Assistant from '@/pages/Assistant';
import Mine from '@/pages/Mine';
import Settings from '@/pages/Settings';

export default function App() {
  return (
    <DftTaskProvider>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Home />} />
          {/* 工具箱分组（v1.0.0 起迁入） */}
          <Route path="/toolbox/query" element={<Query />} />
          <Route path="/toolbox/batch" element={<Batch />} />
          <Route path="/toolbox/dft" element={<Dft />} />
          {/* 旧路径重定向：兼容历史记录 / 收藏 / 书签中保存的链接 */}
          <Route path="/query" element={<Navigate to="/toolbox/query" replace />} />
          <Route path="/batch" element={<Navigate to="/toolbox/batch" replace />} />
          <Route path="/records" element={<Records />} />
          <Route path="/iterate" element={<Iterate />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/mine" element={<Mine />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
      {/* 全局中文 toast（api.ts 统一错误提示使用） */}
      <Toaster richColors position="top-center" />
    </DftTaskProvider>
  );
}
