import { releasePath, releaseUrl } from './release-paths.js';
// 小鱼 · 自我形象 MCP server（stdio）
// 让她能自己改昵称、个性签名、头像（对应 AstrBot 原来的 astrbot_plugin_xiaoyu_profile）。
//
// 工具：
//   xiaoyu_my_profile      看我现在叫什么、签名是什么、上次改的时间
//   xiaoyu_set_nickname    改昵称
//   xiaoyu_set_signature   改个性签名
//   xiaoyu_set_avatar      换头像（支持 http(s) 链接、本地绝对路径、base64）
//
// 保护：冷却时间（昵称 12h / 签名 3h / 头像 24h）+ 只允许白名单会话调用（桥接侧已限制）。

import fs from 'node:fs';
import path from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const CFG = releasePath("qq-bridge/config.json");
const STATE = releasePath("qq-bridge/state/xiaoyu-profile.json");
const COOLDOWN_MS = { nickname: 12 * 3600e3, signature: 3 * 3600e3, avatar: 24 * 3600e3 };

function readJson(p, fallback) {
  try {
    let t = fs.readFileSync(p, 'utf8');
    if (t.charCodeAt(0) === 0xfeff) t = t.slice(1);
    return JSON.parse(t);
  } catch {
    return fallback;
  }
}

function snowluma() {
  const c = readJson(CFG, {});
  const s = c.snowluma || {};
  return {
    url: String(s.httpUrl || releaseUrl("onebot", "")).replace(/\/+$/, ''),
    token: s.accessToken || '',
  };
}

async function onebot(action, payload) {
  const { url, token } = snowluma();
  const res = await fetch(`${url}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(payload || {}),
    signal: AbortSignal.timeout(30000),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`OneBot ${action} HTTP ${res.status}: ${text.slice(0, 160)}`);
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text.slice(0, 200) }; }
  if (data && data.status && data.status !== 'ok') throw new Error(`OneBot ${action} 失败：${text.slice(0, 160)}`);
  return data;
}

const cos = (() => {
  const st = readJson(STATE, {});
  return {
    last: st.last || {},
    check(kind) {
      const at = Number(this.last[kind] || 0);
      const left = COOLDOWN_MS[kind] - (Date.now() - at);
      if (left > 0) {
        const h = Math.ceil(left / 3600e3);
        return `刚改过，${h} 小时后再试（自己定的事别反复推翻）。`;
      }
      return null;
    },
    note(kind) {
      this.last[kind] = Date.now();
      try {
        fs.mkdirSync(path.dirname(STATE), { recursive: true });
        fs.writeFileSync(STATE, JSON.stringify({ last: this.last }, null, 2), 'utf8');
      } catch { /* 忽略 */ }
    },
  };
})();

const server = new McpServer({ name: 'xiaoyu-profile', version: '1.0.0' });

server.tool(
  'xiaoyu_my_profile',
  '查看自己现在的 QQ 昵称、个性签名，以及上次改昵称/签名/头像的时间（含冷却剩余）。',
  {},
  async () => {
    try {
      const info = await onebot('get_login_info');
      const st = readJson(STATE, { last: {} });
      const now = Date.now();
      const cooldown = {};
      for (const [k, ms] of Object.entries(COOLDOWN_MS)) {
        const left = ms - (now - Number(st.last?.[k] || 0));
        cooldown[k] = left > 0 ? `${Math.ceil(left / 3600e3)}h` : 'ok';
      }
      return {
        content: [{
          type: 'text',
          text: JSON.stringify({ login: info, last_changed: st.last || {}, cooldown }),
        }],
      };
    } catch (e) {
      return { content: [{ type: 'text', text: `查询失败：${e.message}` }], isError: true };
    }
  },
);

server.tool(
  'xiaoyu_set_nickname',
  '改自己在 QQ 上的昵称。想换名字的时候用（比如想换个更像自己的称呼）；别频繁改，12 小时只能一次。',
  { nickname: z.string().min(1).max(24).describe('新昵称，1-24 个字，别带符号') },
  async ({ nickname }) => {
    const wait = cos.check('nickname');
    if (wait) return { content: [{ type: 'text', text: wait }] };
    try {
      await onebot('set_qq_profile', { nickname: String(nickname).trim() });
      cos.note('nickname');
      return { content: [{ type: 'text', text: `昵称改成「${nickname}」了。` }] };
    } catch (e) {
      return { content: [{ type: 'text', text: `改名失败：${e.message}` }], isError: true };
    }
  },
);

server.tool(
  'xiaoyu_set_signature',
  '改自己的个性签名（QQ 资料里的那句个人说明）。可以用一句现在的心情或想说的话，别写太长。',
  { text: z.string().min(1).max(60).describe('签名内容，1-60 字') },
  async ({ text }) => {
    const wait = cos.check('signature');
    if (wait) return { content: [{ type: 'text', text: wait }] };
    try {
      await onebot('set_qq_profile', { personal_note: String(text).trim() });
      cos.note('signature');
      return { content: [{ type: 'text', text: `签名换成「${text}」了。` }] };
    } catch (e) {
      return { content: [{ type: 'text', text: `改签名失败：${e.message}` }], isError: true };
    }
  },
);

server.tool(
  'xiaoyu_set_avatar',
  '换自己的 QQ 头像。source 可以是 http(s) 图片链接、本机绝对路径（.png/.jpg/.webp），或 base64。图片会被 QQ 接受后生效。',
  { source: z.string().min(4).describe('图片地址 / 本地绝对路径 / base64 数据') },
  async ({ source }) => {
    const wait = cos.check('avatar');
    if (wait) return { content: [{ type: 'text', text: wait }] };
    let file = String(source).trim();
    try {
      if (/^https?:\/\//i.test(file)) {
        // 直接用链接
      } else if (/^[A-Za-z]:[\\/]/.test(file)) {
        if (!fs.existsSync(file)) throw new Error(`本机没有这个文件：${file}`);
        const buf = fs.readFileSync(file);
        if (buf.length > 8 * 1024 * 1024) throw new Error('图片太大（>8MB）');
        file = `base64://${buf.toString('base64')}`;
      } else if (/^base64:\/\//i.test(file)) {
        // 原样
      } else {
        throw new Error('source 需要是 http(s) 链接、本地绝对路径，或 base64 数据');
      }
      await onebot('set_qq_avatar', { file });
      cos.note('avatar');
      return { content: [{ type: 'text', text: '头像换了（QQ 端可能要几秒到一分钟才刷新）。' }] };
    } catch (e) {
      return { content: [{ type: 'text', text: `换头像失败：${e.message}` }], isError: true };
    }
  },
);

await server.connect(new StdioServerTransport());
