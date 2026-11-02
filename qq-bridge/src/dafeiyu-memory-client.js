import { releasePath, releaseUrl } from './release-paths.js';
// Durable local core access shared by MCP and bridge. No model API client.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export function createMemoryClient({ baseURL = process.env.DAFEIYU_API || releaseUrl("core", ""), home = process.env.DAFEIYU_HOME || releasePath("dafeiyu/data"), request = fetch } = {}) {
  async function call(route, body, timeoutMs = 20000) {
    const response = await request(baseURL.replace(/\/+$/, '') + route, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || `Local core HTTP ${response.status}`);
      error.rejected = response.status >= 400 && response.status < 500;
      throw error;
    }
    return data;
  }
  return {
    call,
    async recordChat(payload) {
      try { return await call('/api/chat/record', payload, 8000); }
      catch (error) {
        const inbox = path.join(home, 'state', 'chat-inbox');
        fs.mkdirSync(inbox, { recursive: true });
        const target = path.join(inbox, crypto.createHash('sha256').update(payload.event_id).digest('hex') + '.json');
        fs.writeFileSync(target + '.tmp', JSON.stringify(payload));
        fs.renameSync(target + '.tmp', target);
        return { ok: true, queued: true, stored: false, error: error.message };
      }
    },
    async flushChatInbox(limit = 20) {
      const inbox = path.join(home, 'state', 'chat-inbox');
      if (!fs.existsSync(inbox)) return { flushed: 0 };
      let flushed = 0;
      for (const name of fs.readdirSync(inbox).filter(s => s.endsWith('.json')).slice(0, limit)) {
        const file = path.join(inbox, name);
        try {
          await call('/api/chat/record', JSON.parse(fs.readFileSync(file, 'utf8')), 8000);
          fs.unlinkSync(file); flushed++;
        } catch { break; }
      }
      return { flushed };
    },
    recall: (query, limit = 5) => call('/api/memory/recall', { query, limit }),
    lookup: (term) => call('/api/slang/lookup', { term, allow_network: true }, 55000),
    async remember(content, category = '平静', extra = {}) {
      const payload = { ...extra, content, category, event_id: extra.event_id || crypto.randomUUID() };
      try { return await call('/api/memory/remember', payload); }
      catch (error) {
        if (error.rejected) throw error;
        const inbox = path.join(home, 'state', 'memory-inbox');
        fs.mkdirSync(inbox, { recursive: true });
        const name = crypto.createHash('sha256').update(payload.event_id).digest('hex') + '.json';
        const target = path.join(inbox, name);
        const temporary = target + '.tmp';
        fs.writeFileSync(temporary, JSON.stringify(payload, null, 2), 'utf8');
        fs.renameSync(temporary, target);
        return { ok: true, stored: false, queued: true, event_id: payload.event_id,
          note: '核心暂不可用，已保存在本机待写入队列；尚未计入长期记忆。' };
      }
    },
  };
}
