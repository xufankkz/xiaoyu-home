// Per-conversation accounting survives model-session rotation and process restarts.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export const dayKey = (now) => {
  const d = new Date(now);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const minute = (hm, fallback) => /^\d{2}:\d{2}$/.test(String(hm))
  ? Math.min(1439, Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3))) : fallback;
export function isQuiet(now, span = ['23:30', '08:30']) {
  if (!Array.isArray(span) || span.length !== 2) return false;
  const d = new Date(now), m = d.getHours() * 60 + d.getMinutes();
  const a = minute(span[0], 1410), b = minute(span[1], 510);
  return a > b ? m >= a || m < b : a < b && m >= a && m < b;
}
const hash = (s) => crypto.createHash('sha256').update(s).digest().readUInt32BE();

export class ConversationCare {
  constructor(file, { now = Date.now(), startupGraceMs = 30 * 60000 } = {}) {
    this.file = file; this.startedAt = now; this.startupGraceMs = startupGraceMs;
    this.writable = true;
    try { this.data = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')); }
    catch (error) { this.data = {}; if (error.code !== 'ENOENT') { this.writable = false; this.error = error.message; } }
    this.data.chats ||= {};
  }
  save() {
    if (!this.writable) return false;
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      fs.writeFileSync(this.file + '.tmp', JSON.stringify(this.data, null, 2));
      fs.renameSync(this.file + '.tmp', this.file); return true;
    } catch (error) { this.writable = false; this.error = error.message; return false; }
  }
  chat(key) { return this.data.chats[key] ||= { sent: 0, voice: 0, credit: 0 }; }
  daily(key, now, cfg = {}) {
    const c = this.chat(key), today = dayKey(now);
    const windowKey = cfg.start !== undefined || cfg.end !== undefined ? `${cfg.start || '10:00'}-${cfg.end || '21:30'}` : null;
    if (c.day !== today) {
      c.day = today; c.dailySent = 0; c.attemptAt = 0; c.pendingAt = 0;
      delete c.windowKey;
      const start = minute(cfg.start || '10:00', 600), end = Math.max(start, minute(cfg.end || '21:30', 1290));
      const d = new Date(now); d.setHours(0, 0, 0, 0);
      c.dueAt = d.getTime() + (start + hash(`${key}:${today}`) % Math.max(1, end - start)) * 60000;
      this.save();
    }
    if (windowKey && c.windowKey !== windowKey) {
      const start = minute(cfg.start || '10:00', 600), end = Math.max(start, minute(cfg.end || '21:30', 1290));
      const d = new Date(now); d.setHours(0, 0, 0, 0);
      c.dueAt = d.getTime() + (start + hash(`${key}:${today}`) % Math.max(1, end - start)) * 60000;
      c.windowKey = windowKey;
      this.save();
    }
    return c;
  }
  due(key, state, now, cfg = {}) {
    const c = this.daily(key, now, cfg);
    if (!this.writable) return false;
    const grace = this.startupGraceMs + hash(key) % (30 * 60000);
    if (now < this.startedAt + grace || c.dailySent >= (cfg.maxPerDay ?? 1)) return false;
    if (isQuiet(now, cfg.quietHours) || now < c.dueAt) return false;
    const d = new Date(now), m = d.getHours() * 60 + d.getMinutes();
    if (m < minute(cfg.start || '10:00', 600) || m >= minute(cfg.end || '21:30', 1290)) return false;
    if (now - (c.attemptAt || 0) < (cfg.retryMs ?? 45 * 60000)) return false;
    if (now - Math.max(state.lastIncomingAt || 0, state.lastAiReplyAt || 0) < (cfg.idleMs ?? 25 * 60000)) return false;
    return true;
  }
  attempted(key, now) {
    const c = this.daily(key, now); c.attemptAt = now; c.pendingAt = now; this.save();
  }
  outgoing(key, { now = Date.now(), voice = false, proactive = false, probability = 0.3 } = {}) {
    const c = this.daily(key, now); c.sent++; if (voice) c.voice++;
    c.credit = Math.max(-1, Math.min(1, (c.credit || 0) + probability - (voice ? 1 : 0)));
    if (proactive) { c.dailySent++; c.lastProactiveAt = now; c.pendingAt = 0; }
    this.save(); return c;
  }
  wantsVoice(key, text, cfg = {}, now = Date.now()) {
    if (!this.writable || cfg.enabled === false || now < (this.chat(key).voiceRetryAt || 0)) return false;
    if (!text || text.length > (cfg.maxChars || 44) || /https?:\/\/|```|[{}<>]|\b\w+\.(?:js|py|json)\b|[\u{1F000}-\u{1FAFF}]/u.test(text)) return false;
    const p = Math.max(0, Math.min(1, cfg.probability ?? 0.3));
    return (this.chat(key).credit || 0) + p >= 0.999999;
  }
  voiceFailed(key, now = Date.now()) { this.chat(key).voiceRetryAt = now + 5 * 60000; this.save(); }
  status(now, cfg = {}) {
    return Object.entries(this.data.chats).map(([key, c]) => ({ key,
      date: dayKey(now), dailySent: c.day === dayKey(now) ? c.dailySent || 0 : 0,
      dueAt: c.day === dayKey(now) ? c.dueAt : null,
      sent: c.sent || 0, voice: c.voice || 0,
      voiceRatio: c.sent ? Math.round(c.voice / c.sent * 1000) / 10 : 0,
      targetPerDay: cfg.maxPerDay ?? 1 }));
  }
}
