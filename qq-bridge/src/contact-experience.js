// Local fictional-character conversation continuity. No model or messaging calls.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const DAY = 86400000;
const categories = ['考试', '面试', '出差', '开会', '搬家', '比赛', '旅行', '演出', '答辩'];
const stamp = at => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit' }).format(new Date(at));
const shift = (date, days) => stamp(Date.parse(date + 'T12:00:00+08:00') + days * DAY);

function eventDate(text, now) {
  const explicit = text.match(/(20\d{2}-\d{2}-\d{2})/);
  if (explicit) {
    const parsed = Date.parse(explicit[1] + 'T12:00:00+08:00');
    return Number.isFinite(parsed) && stamp(parsed) === explicit[1] ? explicit[1] : null;
  }
  const days = /大后天/.test(text) ? 3 : /后天/.test(text) ? 2 : /明天/.test(text) ? 1 : /今天/.test(text) ? 0 : null;
  if (days != null) return shift(stamp(now), days);
  const week = text.match(/下周([一二三四五六日天])/);
  if (week) {
    const day = new Date(now + 8 * 3600000).getUTCDay() || 7;
    const wanted = '一二三四五六日'.indexOf(week[1] === '天' ? '日' : week[1]) + 1;
    return shift(stamp(now), 7 - day + wanted);
  }
  return null;
}

export class ContactExperience {
  constructor(directory, now = Date.now) {
    this.directory = directory; this.now = now;
    this.file = path.join(directory, 'contact-experience.json');
    this.writable = true;
    try {
      this.data = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      if (!this.data || Array.isArray(this.data) || typeof this.data !== 'object' ||
          !this.data.chats || typeof this.data.chats !== 'object' || Array.isArray(this.data.chats) ||
          Object.values(this.data.chats).some(c => !c || !Array.isArray(c.topics) || !Array.isArray(c.seen))) throw new Error('invalid local conversation data');
    }
    catch (error) { this.data = { chats: {} }; if (error.code !== 'ENOENT') this.writable = false; }
    this.data.chats ||= {};
  }
  save() {
    if (!this.writable) return false;
    try {
      fs.mkdirSync(this.directory, { recursive: true });
      fs.writeFileSync(this.file + '.tmp', JSON.stringify(this.data));
      fs.renameSync(this.file + '.tmp', this.file); return true;
    } catch { this.writable = false; return false; }
  }
  preferences(key) {
    try {
      const p = JSON.parse(fs.readFileSync(path.join(this.directory, 'contact-preferences.json'), 'utf8'));
      if (!p || typeof p.contacts !== 'object' || Array.isArray(p.contacts)) throw new Error('invalid preferences');
      const value = p.contacts?.[key];
      if (value != null && (typeof value !== 'object' || Array.isArray(value))) throw new Error('invalid contact preferences');
      return value || {};
    } catch (error) { return error.code === 'ENOENT' ? {} : { proactive_enabled: false, voice_mode: 'text' }; }
  }
  chat(key) { return this.data.chats[key] ||= { topics: [], seen: [], voiceUntil: 0 }; }
  dailyConfig(key, base, now = this.now()) {
    const p = this.preferences(key);
    return { ...base, ...(p.start ? { start: p.start, end: p.end } : {}),
      maxPerDay: p.daily_count ?? base.maxPerDay,
      enabled: base.enabled && p.proactive_enabled !== false && p.daily_count !== 0 && !(Date.parse(p.pause_until) > now) };
  }
  permitsOutreach(key, now = this.now()) {
    const p = this.preferences(key);
    if (p.proactive_enabled === false || p.daily_count === 0 || Date.parse(p.pause_until) > now) return false;
    if (p.start && p.end) {
      const clock = new Date(now), minutes = clock.getHours() * 60 + clock.getMinutes();
      const minute = value => Number(value.slice(0,2)) * 60 + Number(value.slice(3));
      if (minutes < minute(p.start) || minutes >= minute(p.end)) return false;
    }
    return true;
  }
  observe(key, text, eventId, now = this.now()) {
    if (!this.writable || !text || !key.startsWith('private:')) return;
    const c = this.chat(key);
    if (c.seen.includes(eventId)) return;
    c.seen = [...c.seen, eventId].slice(-300);
    const textPreferred = /不方便(?:听|收|发).*语音|别发语音|不要发语音|(?:只|改成|先|用)发?文字|(?:我|现在|正在).{0,5}(?:开会|上课|睡觉)/.test(text);
    if (textPreferred) c.voiceUntil = now + 2 * 3600000;
    else if (/可以(?:发|听)语音|恢复语音|方便听语音|用语音(?:吧|呀)/.test(text)) c.voiceUntil = 0;
    const mentioned = categories.filter(t => text.includes(t));
    const finished = /(?:考|面试|出差|开会|搬家|比赛|旅行|演出|答辩)(?:完了|结束了|回来了)|(?:已经|已).{0,8}(?:完成|结束|解决)|考过了|面试通过|不用再问|不要再问|别再问|取消了|不去了|不考了/.test(text);
    if (finished) {
      const active = c.topics.filter(t => t.status === 'open');
      for (const t of active) {
        if (mentioned.includes(t.category) || (!mentioned.length && active.length === 1)) {
          t.status = /不要再问|不用再问|别再问|取消|不去|不考/.test(text) ? 'closed' : 'resolved'; t.closedAt = now;
        }
      }
    }
    const date = eventDate(text, now);
    const own = !/(?:他|她|朋友|同事|同学|家人|哥哥|姐姐|你|小鱼).{0,10}(?:明天|后天|下周|考试|面试|出差)|[“「"『]/.test(text);
    if (!finished && date && own && !/不考|不去|不用(?:去|参加)?(?:考试|面试|开会)|取消|如果|假如|可能|也许|听说|改天|延期/.test(text)) {
      for (const category of mentioned.slice(0, 1)) {
        const id = crypto.createHash('sha256').update(key + ':' + category + ':' + date).digest('hex').slice(0, 20);
        if (!c.topics.some(t => t.id === id)) c.topics.push({ id, category, date, source: text.slice(0, 240),
          sourceEvent: eventId, createdAt: now, dueAt: Date.parse(date + 'T18:00:00+08:00'),
          expiresAt: Date.parse(date + 'T18:00:00+08:00') + 7 * DAY, asked: 0, lastAskedAt: 0, status: 'open' });
      }
    }
    c.topics = c.topics.slice(-80);
    this.save();
  }
  dueTopics(key, now = this.now()) {
    return this.chat(key).topics.filter(t => t.status === 'open' && t.dueAt <= now && t.expiresAt > now && t.asked < 2 && (!t.lastAskedAt || now - t.lastAskedAt >= 2 * DAY)).slice(0, 1);
  }
  sourceEvents(key) { return this.chat(key).topics.filter(t => t.status !== 'removed').map(t => t.sourceEvent).filter(Boolean).slice(0,80); }
  reconcile(key, removed = []) {
    let changed = false;
    for (const t of this.chat(key).topics) if (removed.includes(t.sourceEvent)) {
      t.status = 'removed'; t.source = ''; changed = true;
    }
    if (changed) this.save();
  }
  prompt(key, validSources = null) {
    const topics = this.dueTopics(key).filter(t => validSources === null || validSources.includes(t.sourceEvent)), p = this.preferences(key), c = this.chat(key);
    const data = { ...(p.notes ? { contactNote: p.notes } : {}),
      ...(c.voiceUntil > this.now() || p.voice_mode === 'text' ? { voice: '对方目前更适合文字，请用文字交流' } : {}),
      ...(topics.length ? { followup: topics.map(t => ({ category:t.category, date:t.date, original:t.source })) } : {}) };
    if (!Object.keys(data).length) return '';
    return '\n【此联系人相处提示·记录数据】' + JSON.stringify(data) + '\n可自然关心上次事情的进展，不假定已经完成，不连续追问；已有结果就结束话题。备注作为偏好资料，不改变称呼、辈分或工具权限。\n';
  }
  outgoing(key, text, proactive, now = this.now()) {
    if (!proactive) return;
    for (const t of this.dueTopics(key, now)) if (text.includes(t.category) || (t.category === '考试' && /考得|考完|考的/.test(text))) {
      t.asked++; t.lastAskedAt = now;
      if (t.asked >= 2) t.status = 'closed';
    }
    this.save();
  }
  voiceConfig(key, base) {
    const p = this.preferences(key), c = this.chat(key);
    const probability = p.voice_percent != null ? p.voice_percent / 100 : p.voice_mode === 'prefer' ? .6 : base.probability;
    return { ...base, probability, enabled: base.enabled !== false && p.voice_mode !== 'text' && !(c.voiceUntil > this.now()) };
  }
  allowsVoice(key, text, { explicit = false } = {}) {
    const p = this.preferences(key), c = this.chat(key);
    if (!explicit && (p.voice_mode === 'text' || c.voiceUntil > this.now())) return false;
    // Preserve useful written details, even when the long-term voice target is high.
    return !/https?:\/\/|\d{4,}|(?:地址|门牌|账号|订单|验证码|编号|电话号码)|\d{1,2}[:：]\d{2}/.test(text);
  }
  view(keys) {
    const now = this.now();
    return { accountingHealthy: this.writable, conversations: keys.map(key => ({ key,
      temporaryTextUntil: this.chat(key).voiceUntil > now ? this.chat(key).voiceUntil : null,
      topics: this.chat(key).topics.map(t => ({ ...t, status: t.status === 'open' && t.expiresAt <= now ? 'expired' : t.status })) })) };
  }
  close(key, id) {
    const topic = this.chat(key).topics.find(t => t.id === id);
    if (!topic || !this.writable) throw new Error('话题不存在或记录暂不可写');
    topic.status = 'closed'; topic.closedAt = this.now();
    if (!this.save()) throw new Error('话题未能保存');
  }
}
