import { releasePath, releaseUrl } from './release-paths.js';
// The installed F reference and all tone parameters remain the source of truth.
import fs from 'node:fs';
const DEFAULT_CONFIG = releasePath("astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json");
export function readVoiceConfig(file = DEFAULT_CONFIG) {
  return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
}
export function conversationalTone(text, expression = {}) {
  if (expression.quiet) return 'gentle';
  if (expression.tone === 'gentle') return 'gentle';
  if (expression.primary && expression.primary !== 'joy') return 'gentle';
  if (/嘿|哈哈|嘻|夸我|好耶|开心|猜猜|逗你|哼哼/.test(text)) return 'playful';
  if (/才没有|才不|哼[！!，,。]|笨蛋/.test(text)) return 'tsundere';
  return 'gentle';
}
export async function synthesizeVoice(text, tone, { configFile, request = fetch } = {}) {
  const c = readVoiceConfig(configFile), clean = String(text || '').trim();
  if (!clean || clean.length > (c.max_chars || 44)) throw new Error(`语音需为 1–${c.max_chars || 44} 字，不能截断原句`);
  const usedTone = tone || c.default_tone || 'gentle';
  const t = c.tones?.[usedTone] || c.tones?.[c.default_tone] || {};
  const response = await request(releaseUrl('model', '/v1/audio/speech'), {
    method: 'POST', headers: { 'content-type': 'application/json' }, signal: AbortSignal.timeout(60000),
    body: JSON.stringify({ input: clean, tone: usedTone, text: clean, text_lang: c.gsv_text_lang || 'zh', ref_audio_path: t.ref,
      prompt_text: t.prompt || '', prompt_lang: c.gsv_prompt_lang || 'zh', text_split_method: 'cut0',
      batch_size: 1, media_type: 'wav', streaming_mode: false, parallel_infer: true,
      repetition_penalty: 1.25, temperature: Number(t.temperature ?? 0.52), speed_factor: Number(t.speed ?? 1.10) }),
  });
  if (!response.ok) throw new Error(`语音合成 HTTP ${response.status}`);
  const audio = Buffer.from(await response.arrayBuffer());
  if (audio.length < 1000 || audio.length > 8 * 1024 * 1024 || audio.toString('ascii', 0, 4) !== 'RIFF') throw new Error('合成音频格式或大小异常');
  return { audio, text: clean, tone: usedTone };
}
