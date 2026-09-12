/**
 * 助手 / 报告 Markdown 渲染公共件（v1.9.3 问题 1）。
 *
 * 1. `normalizeMarkdownLinks`：把报告里被全角括号、中文标点污染的 URL
 *    归一化为「纯净 href」——历史报告的写法是 `DOI: 10.xxxx（https://doi.org/...）`，
 *    全角右括号会被 remark-gfm 的 autolink 吞进 href（它只修剪 ASCII 尾标点），
 *    点击即 404。后端已在校验/落盘/读取三处归一化，这里对「流式增量文本 +
 *    尚未刷新页面的旧数据」兜底，规则与 `src/assistant/research.py` 保持一致。
 * 2. `mdComponents`：对话气泡与报告弹窗共用的 Tailwind 映射；所有 http(s)
 *    链接一律走 `openExternal`（Electron 下交给系统浏览器，绝不在应用窗口内导航）。
 */
import type { Components } from 'react-markdown';
import { openExternal } from '@/lib/external';

/** 剥离 URL 尾部误吞的括号/标点；URL 内部成对括号保留（如 ..._(Inorganic_Chemistry)/...） */
function cleanUrlTail(url: string): string {
  const junk = '）)】]｝>，,。.；;、：:*_…—';
  let out = url;
  while (out) {
    const ch = out[out.length - 1];
    if (ch === ')') {
      const opens = (out.match(/\(/g) ?? []).length;
      const closes = (out.match(/\)/g) ?? []).length;
      if (opens >= closes) break;
    } else if (ch === ']') {
      const opens = (out.match(/\[/g) ?? []).length;
      const closes = (out.match(/\]/g) ?? []).length;
      if (opens >= closes) break;
    } else if (!junk.includes(ch)) {
      break;
    }
    out = out.slice(0, -1);
  }
  return out;
}

const FULLWIDTH_WRAPPED_URL = /(?<!\])[（(]\s*(https?:\/\/[^\s（）()]+?)\s*[）)]/g;
const MD_LINK_TARGET = /\]\(\s*(https?:\/\/[^\s)]+?)\s*\)/g;
const BARE_URL = /(?<![([<"'])https?:\/\/[^\s<>"'（）【】「」，。；：、]+[）】]*/g;

/** 报告 markdown → 纯净链接（幂等；与后端 normalize_markdown_links 同规则） */
export function normalizeMarkdownLinks(markdown: string): string {
  if (!markdown) return markdown ?? '';
  return markdown
    .replace(MD_LINK_TARGET, (_m, url: string) => `](${cleanUrlTail(url)})`)
    .replace(FULLWIDTH_WRAPPED_URL, '$1')
    .replace(BARE_URL, (m) => cleanUrlTail(m));
}

/** 剥离 react-markdown 注入的 node 属性，避免传到 DOM */
function withoutNode<T extends { node?: unknown }>(props: T): Omit<T, 'node'> {
  const { node, ...rest } = props;
  void node;
  return rest;
}

/** Markdown 元素的 Tailwind 映射（替代 typography 插件，贴合紫金主题） */
export const mdComponents: Components = {
  h1: (props) => <h3 className="mb-2 mt-3 text-base font-semibold" {...withoutNode(props)} />,
  h2: (props) => <h3 className="mb-2 mt-3 text-base font-semibold" {...withoutNode(props)} />,
  h3: (props) => <h4 className="mb-1.5 mt-2.5 text-sm font-semibold" {...withoutNode(props)} />,
  p: (props) => <p className="mb-2 leading-relaxed last:mb-0" {...withoutNode(props)} />,
  ul: (props) => <ul className="mb-2 list-disc space-y-1 pl-5" {...withoutNode(props)} />,
  ol: (props) => <ol className="mb-2 list-decimal space-y-1 pl-5" {...withoutNode(props)} />,
  li: (props) => <li className="leading-relaxed" {...withoutNode(props)} />,
  blockquote: (props) => (
    <blockquote
      className="mb-2 border-l-2 border-gold pl-3 text-muted-foreground"
      {...withoutNode(props)}
    />
  ),
  code: (props) => {
    const { className, children, ...rest } = withoutNode(props);
    const isBlock = /language-/.test(className ?? '');
    return isBlock ? (
      <code className={className} {...rest}>
        {children}
      </code>
    ) : (
      <code
        className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-primary"
        {...rest}
      >
        {children}
      </code>
    );
  },
  pre: (props) => (
    <pre
      className="mb-2 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs"
      {...withoutNode(props)}
    />
  ),
  table: (props) => (
    <div className="mb-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs" {...withoutNode(props)} />
    </div>
  ),
  th: (props) => (
    <th
      className="border border-border bg-muted/60 px-2 py-1 text-left font-medium"
      {...withoutNode(props)}
    />
  ),
  td: (props) => <td className="border border-border px-2 py-1" {...withoutNode(props)} />,
  /** 所有 http(s) 链接统一外链（Electron 下交给系统浏览器，应用内不导航） */
  a: (props) => {
    const { href, ...rest } = withoutNode(props);
    return (
      <a
        className="text-primary underline decoration-dotted underline-offset-2 hover:text-primary/80"
        href={href}
        target="_blank"
        rel="noreferrer"
        title={href}
        onClick={(e) => {
          if (href && /^https?:\/\//i.test(href)) {
            e.preventDefault();
            openExternal(href);
          }
        }}
        {...rest}
      />
    );
  },
  strong: (props) => (
    <strong className="font-semibold text-foreground" {...withoutNode(props)} />
  ),
  hr: () => <hr className="my-3 border-border" />,
};
