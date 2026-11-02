import { releasePath, releaseUrl } from './release-paths.js';
// 大肥鱼记忆 MCP server（stdio）
// 由 DSH 的 MCP 客户端 spawn（web profile 的 cordis.patch.yml 里 mcp-dafeiyu 行）。
//
// 目的：让 DSH 里跑的那个"她"能读到**大肥鱼的记忆与情绪**，从而做到"一条鱼"。
//
// 工具：
//   dafeiyu_recall   共用核心的语义/关键词检索（含日期、有效期与来源）
//   dafeiyu_emotion  读当前情绪值（本地 API 不在时回退到投影文件）
//   dafeiyu_profile  读人设与 1 号长期记忆（性格设定 / 朋友档案 / 老大）
//   dafeiyu_remember 写入核心数据库；离线时进入本机待写入队列
//   dafeiyu_lookup   完整词条查词与网页回答缓存
//
// 数据位置：默认 <workspace>/dafeiyu\data，可用环境变量 DAFEIYU_HOME 覆盖。

import fs from 'node:fs';
import { createMemoryClient } from './dafeiyu-memory-client.js';
import path from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const HOME = process.env.DAFEIYU_HOME || releasePath("dafeiyu/data");
const MEM = path.join(HOME, '记忆系统');
const LT1 = path.join(MEM, '1号长期记忆');
const STATE = path.join(MEM, '情绪阈值', '状态.json');
const PERSONA = path.join(HOME, 'config', 'personality.md');
const EMOTION_API = process.env.DAFEIYU_API || releaseUrl("core", "");

const memoryClient = createMemoryClient();

const CATS = ['开心', '难过', '生气', '创伤', '平静', '害羞'];

function readText(p) {
  try {
    let t = fs.readFileSync(p, 'utf8');
    if (t.charCodeAt(0) === 0xfeff) t = t.slice(1);
    return t;
  } catch {
    return '';
  }
}

/** 把 .md 切成 { heading, body, cat } 块 */
function parseBlocks(dir) {
  const out = [];
  for (const cat of CATS) {
    const text = readText(path.join(dir, `${cat}.md`));
    if (!text) continue;
    const parts = text.split(/^###\s+/m).slice(1);
    for (const part of parts) {
      const nl = part.indexOf('\n');
      const heading = (nl >= 0 ? part.slice(0, nl) : part).trim();
      const body = (nl >= 0 ? part.slice(nl + 1) : '').trim();
      if (body) out.push({ cat, heading, body });
    }
  }
  // 1 号长期记忆也带上（性格/朋友/老大）
  for (const f of ['01-性格设定.md', '02-朋友档案.md', '03-老大.md']) {
    const text = readText(path.join(LT1, f));
    if (!text) continue;
    const parts = text.split(/^###\s+/m).slice(1);
    for (const part of parts) {
      const nl = part.indexOf('\n');
      const heading = (nl >= 0 ? part.slice(0, nl) : part).trim();
      const body = (nl >= 0 ? part.slice(nl + 1) : '').trim();
      if (body) out.push({ cat: '身份', heading, body });
    }
  }
  return out;
}

const server = new McpServer({ name: 'dafeiyu-memory', version: '1.1.0' });

server.tool(
  'dafeiyu_recall',
  '查询「大肥鱼」的长期记忆（她跟所有人相处累积下来的记忆条目，带日期与归属）。'
    + '在回复 QQ 消息前，如果对方提到过去的事、习惯、约定、或你想确认"我记得对不对"，先调用一次。'
    + '返回按相关度排序的记忆片段；没有命中就返回空数组。',
  {
    query: z.string().describe('要查的内容，用对方原话里的关键词最好'),
    limit: z.number().int().min(1).max(10).optional().describe('返回条数，默认 5'),
  },
  async ({ query, limit = 5 }) => {
    try {
      const data = await memoryClient.recall(query, limit);
      return { content: [{ type: 'text', text: JSON.stringify(data) }] };
    } catch (error) {
      return { isError: true, content: [{ type: 'text', text: '记忆核心暂不可用，不能确认过去的事；请不要凭空补写。' }] };
    }
  },
);

server.tool(
  'dafeiyu_emotion',
  '读小鱼与网页/软件共用的当前角色状态，以自然语言描述心情与互动余波。'
    + '想知道自己此刻心情、或对方问"你怎么了"时可以调用。',
  {},
  async () => {
    let payload = null;
    let source = 'api';
    try {
      const character = await fetch(`${EMOTION_API}/api/affective/state`, { signal: AbortSignal.timeout(3000) });
      if (character.ok) {
        const state = await character.json();
        if (state.block) return { content: [{ type: 'text', text: state.block }] };
      }
      const res = await fetch(`${EMOTION_API}/api/emotion/state`, { signal: AbortSignal.timeout(3000) });
      if (res.ok) payload = await res.json();
    } catch { /* 回退到投影文件 */ }
    if (!payload) {
      source = 'file';
      const raw = readText(STATE);
      payload = raw ? JSON.parse(raw) : { note: '没有读到情绪状态（大肥鱼可能没在运行）' };
    }
    return { content: [{ type: 'text', text: JSON.stringify({ source, state: payload }) }] };
  },
);

server.tool(
  'dafeiyu_profile',
  '读「大肥鱼」的人设与身份记忆（性格设定 / 朋友档案 / 老大）。'
    + '第一次对话、或需要确认自己是谁、对方是谁时调用。',
  {},
  async () => {
    const persona = readText(PERSONA);
    const identity = parseBlocks(LT1).filter((b) => b.cat === '身份');
    return {
      content: [{
        type: 'text',
        text: JSON.stringify({
          persona_excerpt: persona.slice(0, 2000),
          identity: identity.map((b) => ({ heading: b.heading, memory: b.body })),
        }),
      }],
    };
  },
);

server.tool(
  'dafeiyu_remember',
  '把一件值得长期记住的事写回「大肥鱼」的记忆（追加一条，不会改动旧条目）。'
    + '只在对方明确说了值得记的事、或发生了有意义的事时调用；不要记录寒暄。',
  {
    content: z.string().describe('要记住的事，写成一句完整的话，带上是谁说的/做的'),
    category: z.enum(CATS).optional().describe('情绪分类，默认 平静'),
  },
  async ({ content, category = '平静' }) => {
    const data = await memoryClient.remember(content, category);
    return { content: [{ type: 'text', text: JSON.stringify(data) }] };
  },
);

server.tool(
  'dafeiyu_lookup',
  '遇到不懂的新梗或网络词，先查此工具再回答。先查完整本地词条，未知才用正常登录的免费网页查询，成功后缓存。相近词不是定义；失败时应坦诚不知道。',
  { term: z.string().min(2).max(20).describe('完整词，例如“奶蛙”') },
  async ({ term }) => ({ content: [{ type: 'text', text: JSON.stringify(await memoryClient.lookup(term)) }] }),
);

await server.connect(new StdioServerTransport());
