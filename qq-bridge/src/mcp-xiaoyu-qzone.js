import { releasePath, releaseUrl } from './release-paths.js';
// 小鱼 · QQ空间 MCP server（stdio）
// 把本地 Qzone 服务（127.0.0.1:18888）包装成她能调用的工具。
//
// 工具：
//   qzone_whoami          我是谁（QQ 号）
//   qzone_feed            看说说（自己或别人的），带评论
//   qzone_detail          看某条说说的详情与评论
//   qzone_post            发一条说说（自主发布）
//   qzone_comment         评论某条说说
//   qzone_like            点赞某条说说

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const API = (process.env.QZONE_API || releaseUrl("qzone", "")).replace(/\/+$/, '');

async function call(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(45000),
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text.slice(0, 400) }; }
  if (!res.ok) throw new Error(`Qzone 服务 HTTP ${res.status}: ${text.slice(0, 200)}`);
  return data;
}

const server = new McpServer({ name: 'xiaoyu-qzone', version: '1.0.0' });

server.tool(
  'qzone_whoami',
  '查询当前登录的 QQ 空间账号（QQ 号）。',
  {},
  async () => ({ content: [{ type: 'text', text: JSON.stringify(await call('/whoami')) }] }),
);

server.tool(
  'qzone_feed',
  '看她自己或好友的 QQ 空间说说列表（含评论，评论里带评论者的 QQ 与昵称，方便分清谁说了什么）。'
    + '想发说说前先看看最近发过什么，避免重复刷屏；想回复别人评论时先读这个。',
  {
    uin: z.string().optional().describe('看谁的空间：填 QQ 号；不填就是她自己'),
    count: z.number().int().min(1).max(20).optional().describe('取几条，默认 5'),
  },
  async ({ uin, count = 5 }) => {
    const q = new URLSearchParams({ count: String(count) });
    if (uin) q.set('uin', String(uin));
    return { content: [{ type: 'text', text: JSON.stringify(await call(`/feed?${q}`)) }] };
  },
);

server.tool(
  'qzone_detail',
  '看某一条说说（含完整评论列表）。需要 host_uin（说说主人的 QQ）和 tid（说说 id），先从 qzone_feed 里取。',
  {
    host_uin: z.string().describe('说说主人的 QQ 号'),
    tid: z.string().describe('说说 id（feed 返回里的 tid）'),
  },
  async ({ host_uin, tid }) => {
    const q = new URLSearchParams({ host_uin: String(host_uin), tid: String(tid) });
    return { content: [{ type: 'text', text: JSON.stringify(await call(`/detail?${q}`)) }] };
  },
);

server.tool(
  'qzone_post',
  '自己发一条 QQ 空间说说。想发的时候就能发——但要有真实想说的，别为了发而发；一天最多一两条。',
  { content: z.string().describe('说说正文，像平时发动态那样自然，别太长') },
  async ({ content }) => {
    const r = await call('/post', { method: 'POST', body: { content: String(content) } });
    return { content: [{ type: 'text', text: JSON.stringify(r) }] };
  },
);

server.tool(
  'qzone_comment',
  '给某条说说写评论。注意分清对象：评论给谁的说说，内容就对着那个人说。',
  {
    host_uin: z.string().describe('说说主人的 QQ 号'),
    tid: z.string().describe('说说 id'),
    content: z.string().describe('评论内容，短一点，像真人留言'),
  },
  async ({ host_uin, tid, content }) => {
    const r = await call('/comment', { method: 'POST', body: { host_uin: String(host_uin), tid: String(tid), content: String(content) } });
    return { content: [{ type: 'text', text: JSON.stringify(r) }] };
  },
);

server.tool(
  'qzone_like',
  '给某条说说点个赞（比评论更轻的表达）。',
  {
    host_uin: z.string().describe('说说主人的 QQ 号'),
    tid: z.string().describe('说说 id'),
  },
  async ({ host_uin, tid }) => {
    const r = await call('/like', { method: 'POST', body: { host_uin: String(host_uin), tid: String(tid) } });
    return { content: [{ type: 'text', text: JSON.stringify(r) }] };
  },
);

await server.connect(new StdioServerTransport());
