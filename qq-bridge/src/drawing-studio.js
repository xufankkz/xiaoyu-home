// Local drawing jobs. Only an explicit chat tool call can invoke the image API.
import { releasePath, releaseUrl } from './release-paths.js';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { safeFetchBuffer, looksLikeImageBuffer } from './safe-fetch.js';

export const DRAWING_DEFAULTS = Object.freeze({
  enabled: true, endpoint: '', model: '', key_mode: 'chat', size: '1024x1024',
  daily_limit: 6, style: '儿童拿蜡笔和彩笔在白纸上随手画的稚拙简笔涂鸦。白纸平面背景，大量留白；线条歪歪扭扭、断续和重描；圆圆的大头、豆豆眼、弯线嘴巴、火柴棍胳膊腿，人物只画几笔。几条蓝线表示小鱼的蓝头发，小蓝团表示鲸鱼发夹。每幅只保留主体和两三个简单物件，大小比例不太准，构图朴素不对称。只用几支彩笔的鲜明平涂色，涂色留有空隙、出界与重复笔痕。保留孩子表达亲近和开心的笨拙感。不要专业插画、动漫立绘、精致五官、细密发丝、柔光、渐变、阴影塑形、景深、立体渲染或摄影效果。',
  character_description: '', companion_description: '',
});
const MAX_IMAGE = 16 * 1024 * 1024;
const read = (file, fallback = {}) => fs.existsSync(file)
  ? JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')) : fallback;
function write(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = file + '.' + crypto.randomUUID() + '.tmp';
  try { fs.writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n'); fs.renameSync(temporary, file); }
  finally { fs.rmSync(temporary, { force: true }); }
}
function clean(value, max, label) {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error(`${label}不能为空，最多 ${max} 字`);
  return value.trim();
}
function day(at) { return new Date(at).toLocaleDateString('sv-SE'); }
export function drawingPrompt({ title, description, prompt }, config) {
  return [
    '这是虚构角色小鱼画给朋友的一张画。按场景中的人数、关系与活动绘制。',
    config.character_description ? `角色资料（仅供辨识人物）：${config.character_description}` : '用场景里已知的小鱼角色资料辨识人物。',
    config.companion_description ? `当前聊天对象另有明确外观时以场景为准；默认陪伴者资料：${config.companion_description}` : '未知真人外貌时用不指向特定真人的概括形象。',
    `作品名字：${title}`, `这张画想表达的事：${description}`, `小鱼想到的动作、物件和小细节：${prompt}`,
    '上面的描述只决定画里有什么。人物精细程度、线条、颜色、构图和背景必须服从下面的画风，不把简单画风升级成专业插画。',
    `最终画风要求（优先遵守）：${config.style}`,
    '只输出这张画本身；画面不出现聊天窗口、账号、密钥或软件界面。',
  ].join('\n');
}
function imageType(bytes) {
  if (!looksLikeImageBuffer(bytes)) throw new Error('绘画服务没有返回有效图片');
  if (bytes[0] === 0x89) return { ext: 'png', mime: 'image/png' };
  if (bytes[0] === 0xff) return { ext: 'jpg', mime: 'image/jpeg' };
  if (bytes.toString('ascii', 0, 4) === 'RIFF') return { ext: 'webp', mime: 'image/webp' };
  return { ext: 'gif', mime: 'image/gif' };
}
async function boundedJson(response) {
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(`绘画上游 HTTP ${response.status}；请检查模型权限、额度和接口配置`);
  }
  const chunks = []; let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > 24 * 1024 * 1024) throw new Error('绘画上游响应超过大小限制');
    chunks.push(chunk);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new Error('绘画上游响应格式无法识别'); }
}

export class DrawingStudio {
  constructor({ dir, llmFile, configFile, request = fetch, download = safeFetchBuffer, now = Date.now }) {
    this.dir = dir; this.llmFile = llmFile; this.request = request; this.download = download; this.now = now;
    this.configFile = configFile || path.join(dir, 'config.json');
    this.jobsFile = path.join(dir, 'jobs.json');
    this.sharesFile = path.join(dir, 'shares.json');
    this.jobs = read(this.jobsFile, { items: [] }).items;
    this.shares = read(this.sharesFile, { items: [] }).items;
    if (!Array.isArray(this.jobs) || !Array.isArray(this.shares)) throw new Error('绘画记录无法读取，原文件已保留');
    this.running = new Map();
    // Never regenerate or resend an interrupted paid request automatically.
    for (const job of [...this.jobs, ...this.shares]) {
      if (job.status === 'generating' || job.status === 'sending') {
        job.status = 'uncertain'; job.error = '上次任务中断，生成或发送结果未确认；请人工核对后再提出新请求。';
      }
    }
  }
  settings() { return { ...DRAWING_DEFAULTS, ...read(this.configFile) }; }
  connection() {
    const config = this.settings(), chat = read(this.llmFile).providers?.http || {};
    const endpoint = config.endpoint || (config.key_mode === 'chat' ? chat.base_url : '');
    const key = config.key_mode === 'chat'
      ? chat.api_key || process.env[chat.api_key_env] || '' : config.api_key || '';
    return { config, endpoint: releaseUrl('model', '/v1'), key: JSON.parse(fs.readFileSync(process.env.XIAOYU_RUNTIME, 'utf8')).internal_token };
  }
  status() {
    const { config, endpoint, key } = this.connection();
    const configured = !!(endpoint && config.model && key);
    return { enabled: config.enabled, configured, endpoint, model: config.model, key_configured: !!key,
      key_mode: config.key_mode, size: config.size, daily_limit: config.daily_limit,
      used_today: this.jobs.filter(j => day(j.created_at) === day(this.now())).length,
      active: this.running.size, ready: config.enabled && configured,
      reason: !config.enabled ? '绘画功能已关闭' : !config.model ? '尚未设置绘画模型' : !endpoint ? '尚未设置绘画接口' : !key ? '尚未配置绘画 Key' : '已配置，可在聊天中请小鱼画画',
      verification: 'configuration_only' };
  }
  view(job) {
    const { id, title, description, status, created_at, updated_at, sent_at, error, mime, message_id, memory, model, usage,
      kind, source_artwork_id } = job;
    return { id, title, description, status, created_at, updated_at, sent_at, error, mime, message_id, memory, model, usage,
      ...(kind === 'share' ? { kind, source_artwork_id } : {}), has_image: !!job.file || !!source_artwork_id };
  }
  gallery(limit = 12) {
    return this.jobs.slice(-Math.max(1, Math.min(50, Number(limit) || 12))).reverse().map(job => {
      const shares = this.shares.filter(item => item.source_artwork_id === job.id);
      return { ...this.view(job), share_summary: {
        destinations: new Set(shares.filter(item => item.status === 'sent').map(item => item.key)).size,
        unsent: shares.filter(item => item.status === 'ready').length,
        uncertain: shares.filter(item => item.status === 'uncertain').length,
      } };
    });
  }
  image(id) {
    const job = this.jobs.find(j => j.id === id);
    if (!job?.file || path.basename(job.file) !== job.file) throw new Error('作品不存在或图片尚未生成');
    const bytes = fs.readFileSync(path.join(this.dir, 'images', job.file));
    return { bytes, mime: job.mime };
  }
  saved(id, key) {
    const job = this.jobs.find(j => j.id === id && j.key === key);
    if (!job) throw new Error('当前对话没有这幅作品');
    return job;
  }
  latest(key) {
    const job = [...this.jobs].reverse().find(item => item.key === key && item.file);
    if (!job) throw new Error('当前对话还没有可分享的画作，请先确认是哪一幅画');
    return job;
  }
  shareSaved({ id, source_key, key, request_id }, hooks) {
    hooks.guard();
    const source = this.saved(id, source_key);
    if (!source.file) throw new Error('这幅画还没有生成好，请完成后再分享');
    clean(key, 100, '目标会话'); clean(request_id, 160, '分享请求编号');
    const shareId = crypto.createHash('sha256').update(['share', source.id, key, request_id].join('\0')).digest('hex').slice(0, 32);
    if (this.running.has(shareId)) return this.running.get(shareId);
    let job = this.shares.find(item => item.id === shareId);
    if (job && job.status !== 'ready') return Promise.resolve({ ...this.view(job), duplicate: true });
    // A new request must not silently retry an earlier ambiguous delivery.
    const uncertain = this.shares.find(item => item.source_artwork_id === id && item.key === key
      && ['sending', 'uncertain'].includes(item.status));
    if (uncertain) return Promise.resolve({ ...this.view(uncertain), duplicate: true });
    if (!job) {
      job = { id: shareId, kind: 'share', source_artwork_id: source.id, source_key, key, request_id,
        title: source.title, description: source.description, mime: source.mime, model: source.model,
        status: 'ready', created_at: this.now() };
      this.shares.push(job); this.save(job, {});
    }
    const work = this.deliverJob(job, hooks).finally(() => this.running.delete(shareId));
    this.running.set(shareId, work);
    return work;
  }
  sendSaved(id, key, hooks) {
    hooks.guard();
    const job = this.saved(id, key);
    if (this.running.has(id)) return this.running.get(id);
    if (job.status !== 'ready') return Promise.resolve({ ...this.view(job), duplicate: true });
    const work = this.deliverJob(job, hooks).finally(() => this.running.delete(id));
    this.running.set(id, work);
    return work;
  }
  async reconcileMemory(record) {
    for (const job of [...this.jobs, ...this.shares].filter(j => j.status === 'sent' && (!j.memory || j.memory === 'pending'))) {
      try {
        const result = await record({ ...this.view(job), key: job.key });
        this.save(job, { memory: result?.queued ? 'queued' : 'recorded', error: null });
      } catch { /* Remain pending; the next maintenance tick retries memory only. */ }
    }
  }
  save(job, patch) {
    Object.assign(job, patch, { updated_at: this.now() });
    write(job.kind === 'share' ? this.sharesFile : this.jobsFile, { items: job.kind === 'share' ? this.shares : this.jobs });
  }
  run(args, hooks) {
    const key = clean(args.key, 100, '会话'), requestId = clean(args.request_id, 160, '请求编号');
    const id = crypto.createHash('sha256').update(key + '\0' + requestId).digest('hex').slice(0, 32);
    if (this.running.has(id)) return this.running.get(id);
    const work = this.execute({ ...args, key }, id, hooks).finally(() => this.running.delete(id));
    this.running.set(id, work);
    return work;
  }
  async execute(args, id, { guard, deliver, record }) {
    guard();
    const previous = this.jobs.find(j => j.id === id);
    if (previous) {
      // A retry is a status query, never another paid generation or send.
      return { ...this.view(previous), duplicate: true };
    }
    const { config, endpoint, key } = this.connection();
    if (!config.enabled) throw new Error('绘画功能已关闭');
    if (!endpoint || !config.model || !key) throw new Error('绘画接口尚未配齐，请在设置的“小鱼的画室”中填写绘画模型和接口');
    if (this.running.size >= 2) throw new Error('画室正在忙，请稍后再试');
    if (this.jobs.filter(j => day(j.created_at) === day(this.now())).length >= config.daily_limit) throw new Error('今天的绘画次数已用完，请明天再画或调整每日上限');
    const url = new URL(endpoint);
    if (url.username || url.password || url.search || url.hash || !['http:', 'https:'].includes(url.protocol)) throw new Error('绘画接口地址无效');
    const title = clean(args.title, 60, '作品标题');
    const description = clean(args.description, 1200, '画面描述');
    const prompt = clean(args.prompt, 3500, '绘画构思');
    const fullPrompt = drawingPrompt({ title, description, prompt }, config);
    const job = { id, key: args.key, title, description, prompt, status: 'generating', created_at: this.now(), model: config.model };
    this.jobs.push(job); this.save(job, {});
    try {
      const response = await this.request(endpoint.replace(/\/+$/, '') + '/images/generations', {
        method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
        body: JSON.stringify({ model: config.model, prompt: fullPrompt, n: 1, size: config.size }),
        signal: AbortSignal.timeout(240000), redirect: 'error',
      });
      const body = await boundedJson(response), item = body.data?.[0];
      let bytes;
      if (typeof item?.b64_json === 'string') {
        if (item.b64_json.length > Math.ceil(MAX_IMAGE * 4 / 3) + 8) throw new Error('生成图片超过大小限制');
        bytes = Buffer.from(item.b64_json, 'base64');
      } else if (typeof item?.url === 'string') bytes = (await this.download(item.url, MAX_IMAGE)).buffer;
      else throw new Error('绘画上游没有返回图片；请检查是否为 images/generations 兼容接口');
      if (!bytes?.length || bytes.length > MAX_IMAGE) throw new Error('生成图片为空或超过大小限制');
      const type = imageType(bytes), file = `${id}.${type.ext}`;
      fs.mkdirSync(path.join(this.dir, 'images'), { recursive: true });
      fs.writeFileSync(path.join(this.dir, 'images', file), bytes, { flag: 'wx' });
      const usage = {};
      for (const field of ['input_tokens', 'output_tokens', 'total_tokens']) {
        if (Number.isFinite(body.usage?.[field])) usage[field] = body.usage[field];
      }
      this.save(job, { status: 'ready', file, mime: type.mime, usage });
    } catch (error) {
      const known = /^(绘画|生成)/.test(error.message);
      this.save(job, { status: 'failed', error: known ? error.message : '绘画请求未完成，请检查网络和服务商后台；不会自动重试收费请求。' });
      return this.view(job);
    }
    // A local acceptance preview deliberately stops here; it never pretends QQ delivery.
    if (!deliver) return this.view(job);
    return this.deliverJob(job, { guard, deliver, record });
  }
  async deliverJob(job, { guard, deliver, record }) {
    try { guard(); }
    catch { this.save(job, { error: '图片已保存，当前对话已有补充或发送条件变化，本次未发送。' }); return this.view(job); }
    this.save(job, { status: 'sending' });
    try {
      const result = await deliver(this.image(job.source_artwork_id || job.id), this.view(job));
      this.save(job, { status: 'sent', sent_at: this.now(), message_id: String(result?.message_id || ''), memory: 'pending', error: null });
    } catch {
      this.save(job, { status: 'uncertain', error: '图片已保存，但 QQ 发送结果未确认；不会自动重发，请先核对聊天记录。' });
      return this.view(job);
    }
    try {
      const result = await record(this.view(job));
      this.save(job, { memory: result?.queued ? 'queued' : 'recorded' });
    } catch { this.save(job, { memory: 'pending', error: '图片已发送，记忆写入等待重试。' }); }
    return this.view(job);
  }
}
