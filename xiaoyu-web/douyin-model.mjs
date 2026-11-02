// One bounded authenticated model request. Private config arrives through stdin only.
import process from 'node:process';
let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
  if (input.length > 12 * 1024 * 1024) throw new Error('request too large');
}
try {
  const args = JSON.parse(input), url = new URL(args.endpoint.replace(/\/+$/, '') + '/chat/completions');
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) throw new Error('invalid endpoint');
  const response = await fetch(url, { method: 'POST', headers: {
    Authorization: 'Bearer ' + args.api_key, 'Content-Type': 'application/json', Accept: 'application/json',
  }, body: JSON.stringify({ model: args.model, messages: args.messages, max_tokens: 2200,
    thinking: { type: 'disabled' }, response_format: { type: 'json_object' } }), signal: AbortSignal.timeout(75000) });
  if (!response.ok) {
    await response.body?.cancel();
    process.stdout.write(JSON.stringify({ ok: false, error: '抖音模型接口 HTTP ' + response.status }));
  } else {
    let size = 0, chunks = [];
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > 4*1024*1024) throw new Error('response too large');
      chunks.push(chunk);
    }
    const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    let text = data.choices?.[0]?.message?.content;
    if (typeof text !== 'string') throw new Error('invalid result');
    text = text.replace(/^```(?:json)?\s*/, '').replace(/\s*```$/, '').trim();
    let parsed;
    try { parsed = JSON.parse(text); }
    catch { throw new Error('invalid JSON'); }
    process.stdout.write(JSON.stringify({ ok: true, result: parsed, usage: data.usage || {} }));
  }
} catch {
  process.stdout.write(JSON.stringify({ ok: false, error: '抖音模型响应未能确认，请检查配置或稍后重试。' }));
}
