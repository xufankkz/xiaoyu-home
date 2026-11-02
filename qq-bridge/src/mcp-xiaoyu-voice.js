import { releasePath, releaseUrl } from './release-paths.js';
// 小鱼语音 MCP server（stdio）
// 作用：让 DSH 里的她能用**我们克隆的音色**发 QQ 语音条。
//
// 链路：DSH agent → 本服务 → 共用桥接 → GPT-SoVITS → SnowLuma
// 发送、权限与实际语音文本的记忆写入由桥接统一处理。
//
// 工具：
//   xiaoyu_say_voice       给某人（称呼表里的称呼 或 QQ 号）发一条语音
//   xiaoyu_voice_state     查看语音后端是否就绪（GPT-SoVITS / SnowLuma）

import fs from 'node:fs';
import path from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const BRIDGE_CFG = releasePath("qq-bridge/config.json");
const VOICE_CFG = releasePath("astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json");
// 兜底称呼表：动态表读不到时仍可用。真正的称呼来源是 config.json 的 aliasesFile
// （aliases.json，与桥接 / Web「联系人」/ 桌面「联系人」共用同一份）。
const DEFAULT_ALIASES = {};

function readJson(p) {
  try {
    let t = fs.readFileSync(p, 'utf8');
    if (t.charCodeAt(0) === 0xfeff) t = t.slice(1);
    return JSON.parse(t);
  } catch { return {}; }
}

/**
 * 读取称呼表：aliasesFile（aliases.json，优先）> config.json 的 aliases 内联兜底 > DEFAULT_ALIASES。
 * 表是 QQ 号 -> 称呼，这里反转成 称呼 -> QQ 号；每次调用现读，改表后无需重启 MCP。
 */
function aliasMap() {
  const map = { ...DEFAULT_ALIASES };
  const cfg = readJson(BRIDGE_CFG);
  const add = (obj) => {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return;
    for (const [qq, name] of Object.entries(obj)) {
      const id = String(qq ?? '').trim();
      const label = String(name ?? '').trim();
      if (/^\d+$/.test(id) && label) map[label] = id;
    }
  };
  add(cfg.aliases);
  const file = String(cfg.aliasesFile || '').trim();
  if (file) add(readJson(file));
  return map;
}

function voiceCfg() {
  const c = readJson(VOICE_CFG);
  const tones = c.tones || {};
  return {
    api: String(c.gsv_api || releaseUrl("tts", "")).replace(/\/+$/, ''),
    defaultTone: c.default_tone || 'gentle',
    tones,
  };
}

function snowluma() {
  const c = readJson(BRIDGE_CFG);
  const s = c.snowluma || {};
  return {
    httpUrl: String(s.httpUrl || releaseUrl("onebot", "")).replace(/\/+$/, ''),
    token: s.accessToken || '',
  };
}

// Delivery goes through the shared bridge so successful speech is recorded once.
async function bridgeSayVoice(qq, text, tone, sourceToken) {
  const cfg = readJson(BRIDGE_CFG);
  const stateDir = path.join(path.dirname(BRIDGE_CFG), 'state');
  const consoleToken = String(cfg.consoleToken || fs.readFileSync(path.join(stateDir, 'console-token'), 'utf8').trim());
  if (!sourceToken && cfg.socialV2?.crossConversation?.enabled !== true) {
    throw new Error('请提供当前会话令牌 token');
  }
  const states = readJson(path.join(stateDir, 'social-v2.json'));
  const token = sourceToken || states.conversations?.[`private:${qq}`]?.agentToken;
  if (!token) throw new Error('目标对话尚未就绪，请稍后再试');
  const response = await fetch(`http://127.0.0.1:${cfg.consolePort || 3100}/api/socialV2/say-voice`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-console-token': consoleToken,
      'x-agent-token': token, 'x-agent-call': '1' },
    body: JSON.stringify({ key: `private:${qq}`, text, tone }), signal: AbortSignal.timeout(150000),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || '桥接语音发送失败');
  return result;
}

const server = new McpServer({ name: 'xiaoyu-voice', version: '1.0.0' });

server.tool(
  'xiaoyu_say_voice',
  '用你**自己的克隆音色**给别人发一条 QQ 语音条（真的发出去，不是只在当前对话里说）。'
    + '想撒娇、想让人听到你的声音、或对方要求"发个语音"时用。',
  {
    who: z.string().describe('发给谁：称呼（如 联系人设置中的称呼）或直接给 QQ 号；称呼表每次实时读取'),
    text: z.string().describe('要念的话，44 字以内的完整短句，不要带表情符号'),
    tone: z.enum(['gentle', 'tsundere', 'playful']).optional().describe('语气：温柔/傲娇/调皮，默认温柔'),
    token: z.string().optional().describe('当前唤醒提示里的会话令牌；跨对话时也使用当前会话令牌'),
  },
  async ({ who, text, tone = 'gentle', token }) => {
    const aliases = aliasMap();
    const whoTrim = who.trim();
    const qq = /^\d+$/.test(whoTrim) ? whoTrim : aliases[whoTrim];
    if (!qq) return { content: [{ type: 'text', text: `不认识「${who}」，可用：${Object.keys(aliases).join('/')} 或 QQ 号` }] };
    const clean = String(text || '').replace(/\s+/g, ' ').trim();
    if (!clean) return { content: [{ type: 'text', text: '内容空着' }] };
    if (clean.length > 44) return { isError: true, content: [{ type: 'text', text: '语音最多 44 字，请用完整短句，不会截断内容。' }] };
    try {
      const r = await bridgeSayVoice(qq, clean, tone, token);
      return { content: [{ type: 'text', text: JSON.stringify({ ok: true, to: who, qq, tone, result: r }) }] };
    } catch (e) {
      return { isError: true, content: [{ type: 'text', text: `语音发送失败：${e.message}` }] };
    }
  },
);

server.tool(
  'xiaoyu_voice_state',
  '检查语音后端是否就绪（GPT-SoVITS 合成服务 + SnowLuma 发送通道）。发语音前可选查。',
  {},
  async () => {
    const vc = voiceCfg();
    const out = { gsv_api: vc.api, tones: Object.keys(vc.tones), gsv_ok: false, snowluma_ok: false };
    try {
      const r = await fetch(`${vc.api}/`, { signal: AbortSignal.timeout(3000) });
      out.gsv_ok = r.status < 500;
    } catch { out.gsv_ok = false; }
    try {
      const { httpUrl, token } = snowluma();
      const r = await fetch(`${httpUrl}/get_login_info`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: AbortSignal.timeout(5000),
      });
      out.snowluma_ok = r.ok;
      if (r.ok) out.login = await r.json();
    } catch { out.snowluma_ok = false; }
    return { content: [{ type: 'text', text: JSON.stringify(out) }] };
  },
);

await server.connect(new StdioServerTransport());
