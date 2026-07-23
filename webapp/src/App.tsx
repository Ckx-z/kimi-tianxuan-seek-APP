import { Routes, Route } from 'react-router';
import { Toaster } from 'sonner';
import AppLayout from '@/components/layout/AppLayout';
import Home from '@/pages/Home';
import Query from '@/pages/Query';
import Batch from '@/pages/Batch';
import Records from '@/pages/Records';
import Iterate from '@/pages/Iterate';
import Mine from '@/pages/Mine';
import Settings from '@/pages/Settings';

export default function App() {
  return (
    <>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Home />} />
          <Route path="/query" element={<Query />} />
          <Route path="/batch" element={<Batch />} />
          <Route path="/records" element={<Records />} />
          <Route path="/iterate" element={<Iterate />} />
          <Route path="/mine" element={<Mine />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
      {/* 全局中文 toast（api.ts 统一错误提示使用） */}
      <Toaster richColors position="top-center" />
    </>
  );
}
