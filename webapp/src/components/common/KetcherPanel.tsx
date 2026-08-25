/**
 * Ketcher 画板主体（懒加载模块，勿直接静态 import）。
 * 本文件由 StructureSketcher.tsx 通过 React.lazy 动态引入：
 * ketcher-react / ketcher-standalone（含内嵌 Indigo WASM）只在本模块首次
 * 加载时进入独立 chunk，不占用首屏体积。
 *
 * 静态资源说明（ketcher-react 3.17.2 调研结论）：
 * - 模板/图标已全部内联进 JS，staticResourcesUrl 传 '/' 即可，无需拷贝 assets；
 * - ketcher-standalone 的 WASM 与 Web Worker 以 base64 内嵌于 main.js，
 *   运行时经 Blob URL 启动，完全离线可用，无需额外文件。
 */
// 必须第一个 import：注入 raphael require 垫片后再加载 ketcher（否则白屏）
import './ketcher-require-shim';
import { useEffect } from 'react';
import { Editor } from 'ketcher-react';
import type { Ketcher } from 'ketcher-core';
import { StandaloneStructServiceProvider } from 'ketcher-standalone';
import 'ketcher-react/dist/index.css';

/** 单机模式结构服务：Indigo 以 WASM 本地运行（芳香化、clean、SMILES 互转等），无需后端 indigo 服务 */
const structServiceProvider = new StandaloneStructServiceProvider();

interface Props {
  /** 初始 SMILES（用于「继续编辑已有结构」），仅初始化时载入一次 */
  initialSmiles?: string;
  /** 回传 Ketcher 实例（getSmiles/setMolecule 等 API） */
  ketcherRef: { current: Ketcher | null };
  /** 初始 SMILES 解析失败时的提示回调 */
  onInitError?: (message: string) => void;
}

export default function KetcherPanel({ initialSmiles, ketcherRef, onInitError }: Props) {
  // 卸载时释放实例引用，避免「确定」拿到已销毁的画板
  useEffect(() => () => { ketcherRef.current = null; }, [ketcherRef]);

  return (
    <div className="ketcher-sketcher-host h-full w-full">
      <Editor
        staticResourcesUrl="/"
        structServiceProvider={structServiceProvider}
        disableMacromoleculesEditor
        errorHandler={(message: string) => console.error('[ketcher]', message)}
        onInit={(ketcher: Ketcher) => {
          ketcherRef.current = ketcher;
          const s = initialSmiles?.trim();
          if (s) {
            ketcher.setMolecule(s).catch((e: unknown) => {
              console.error('[ketcher] 初始 SMILES 载入失败', e);
              onInitError?.(`初始 SMILES 无法解析：${e instanceof Error ? e.message : '格式有误'}，已从空画板开始`);
            });
          }
        }}
      />
    </div>
  );
}
