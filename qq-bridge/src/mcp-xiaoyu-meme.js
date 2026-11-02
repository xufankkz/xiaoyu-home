import { releasePath, releaseUrl } from './release-paths.js';
// 小鱼 · 表情包语义检索 MCP（stdio）
// 让她按"意思"找表情包并发出去，而不是靠文件名。
//
// 工具：
//   meme_search   按意思找（"无语""开心""嘲讽他"…）
//   meme_send     把选中的表情发到 QQ（private:QQ / group:群号）
//   meme_note     给某张图补一句描述/标签（让以后更好搜）
//   meme_annotate 让视觉模型自动看图写描述（可选）
//   meme_list     看库存概况

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const API = (process.env.MEME_API || releaseUrl("memes", "")).replace(/\/+$/, '');

async function call(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(120000),
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { ok: false, error: text.slice(0, 200) }; }
  if (!data.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const server = new McpServer({ name: 'xiaoyu-meme', version: '1.0.0' });

server.tool(
  'meme_search',
  '按**意思**搜表情包（不是按文件名）。想说"无语/开心/嘲讽/委屈/摸鱼"这类情绪时用它找图；返回 id、描述、标签。',
  {
    query: z.string().describe('想表达的意思或情绪，例如 无语、开心、嘲讽、委屈、摸鱼'),
    limit: z.number().int().min(1).max(10).optional().describe('取几张，默认 5'),
  },
  async ({ query, limit = 5 }) => {
    const d = await call(`/meme/search?q=${encodeURIComponent(query)}&limit=${limit}`);
    return { content: [{ type: 'text', text: JSON.stringify(d.data) }] };
  },
);

server.tool(
  'meme_send',
  '把选中的表情发到 QQ。target 形如 private:联系人号码 或 group:群号；id 从 meme_search 里取。',
  {
    target: z.string().describe('发给谁：private:QQ号 或 group:群号'),
    id: z.string().describe('表情 id（meme_search 返回的那个）'),
  },
  async ({ target, id }) => {
    const d = await call('/meme/send', { method: 'POST', body: { target, id } });
    return { content: [{ type: 'text', text: JSON.stringify(d.data) }] };
  },
);

server.tool(
  'meme_note',
  '给某张表情补一句描述和几个标签（以后就能按这些词搜到它）。',
  {
    id: z.string().describe('表情 id'),
    desc: z.string().describe('一句话描述这张图表达什么'),
    tags: z.array(z.string()).optional().describe('标签，如 ["无语","翻白眼"]'),
  },
  async ({ id, desc, tags = [] }) => {
    await call('/meme/note', { method: 'POST', body: { id, desc, tags } });
    return { content: [{ type: 'text', text: `已记下这张图：${desc}` }] };
  },
);

server.tool(
  'meme_annotate',
  '让视觉模型自动看这张图并写描述/标签（不确定某张图是什么意思时用；会花一点点模型钱）。',
  { id: z.string().describe('表情 id') },
  async ({ id }) => {
    const d = await call('/meme/annotate', { method: 'POST', body: { id } });
    return { content: [{ type: 'text', text: JSON.stringify(d.data) }] };
  },
);

server.tool(
  'meme_list',
  '看表情库现在有多少张、分别来自哪里。',
  {},
  async () => {
    const d = await call('/meme/list');
    return { content: [{ type: 'text', text: JSON.stringify(d.data) }] };
  },
);

await server.connect(new StdioServerTransport());
