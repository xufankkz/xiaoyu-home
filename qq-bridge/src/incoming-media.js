// The transport, downloader and local analyzer are injected for isolated tests.
import fs from 'node:fs';
import path from 'node:path';

export async function understandIncomingMedia(segments, opts) {
  const results = [], images = [];
  const selected = (segments || []).filter(s => s?.type === 'record' || s?.type === 'video');
  for (const [index, segment] of selected.entries()) {
    const kind = segment.type, d = segment.data || {}, label = kind === 'record' ? '语音' : '视频';
    if (index >= 2) { results.push(`[${label}：本条最多处理两个媒体，请分开发送其余内容]`); continue; }
    if (opts[kind === 'record' ? 'voiceEnabled' : 'videoEnabled'] === false) {
      results.push(`[${label}理解未开启]`); continue;
    }
    try {
      if (kind === 'record' && opts.messageId) {
        try {
          const recognized = await opts.callOneBot('fetch_ptt_text', { message_id: opts.messageId }, 12000);
          const text = String(recognized?.text || '').trim();
          if (text) { results.push(`[语音转写·普通话（可能有误）：${text.slice(0, 8000)}]`); continue; }
        } catch { /* Local Mandarin model is the independent fallback. */ }
      }
      let bytes;
      const urls = [d.url, /^https?:\/\//i.test(d.file || '') ? d.file : null].filter(Boolean);
      if (kind === 'record' && /^[\w.+=@-]{1,512}$/.test(String(d.file || ''))) {
        for (const format of ['wav', null]) {
          try {
            const info = await opts.callOneBot('get_record', { file: String(d.file), ...(format ? { out_format: format } : {}) }, 25000);
            const base = info?.base64;
            if (base && base.length * 3 / 4 <= opts.maxBytes) bytes = Buffer.from(base, 'base64');
            if (info?.url) urls.push(info.url);
            if (!bytes && info?.file) bytes = opts.readLocal?.(info.file);
            if (bytes) break;
          } catch { /* Try a current signed QQ download URL. */ }
        }
      }
      if (!bytes && kind === 'video' && d.file) bytes = opts.readLocal?.(d.file);
      for (const url of [...new Set(urls)].slice(0, 3)) {
        if (bytes) break;
        try { bytes = (await opts.download(url, opts.maxBytes)).buffer; } catch { /* next source */ }
      }
      if (!bytes?.length) throw new Error('未能取得可解码的媒体，请重新发送语音／视频文件');
      const out = await opts.analyze(bytes, kind);
      const transcript = String(out.transcript?.text || '').slice(0, 10000);
      if (kind === 'record') {
        results.push(transcript ? `[语音转写·普通话（可能有误）：${transcript}]` : '[语音未识别出清晰人声，请对方重说或打字，不能猜内容]');
      } else {
        if (out.frames?.data && /^[a-f0-9]{64}$/.test(out.sha256)) {
          fs.mkdirSync(opts.cacheDir, { recursive: true });
          const target = path.join(opts.cacheDir, `video-${out.sha256}.jpg`);
          fs.writeFileSync(target, Buffer.from(out.frames.data, 'base64'));
          images.push({ kind: 'image', file: path.basename(target), videoFrames: out.sha256,
            summary: `视频按时间采样画面，${out.frames.timestamps.join('/')}秒` });
        }
        const audio = transcript ? `普通话音轨转写（可能有误）：${transcript}` :
          (out.audio_error || (out.has_audio ? '未识别出清晰人声' : '无音轨'));
        results.push(`[视频 ${out.duration}秒；画面为按时间顺序抽样，时间标在图上，并非逐帧观看；${audio}。只能依据已提供的采样画面和转写，不要猜未采样部分。]`);
      }
    } catch (error) {
      results.push(`[${label}理解失败：${String(error.message).slice(0, 240)}。应明确说明目前未听懂／未看清，不能假装理解。]`);
    }
  }
  return { text: results.join('\n'), images, hasMedia: selected.length > 0 };
}
