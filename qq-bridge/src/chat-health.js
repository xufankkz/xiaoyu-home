// Passive chat health: observe existing turns only; never issue a model request.
import fs from 'node:fs';
import path from 'node:path';

export const HEALTH_CODES = new Set([
  'credentials', 'balance', 'rate_limit', 'model_unavailable',
  'upstream_unavailable', 'upstream_timeout', 'chat_error'
]);

export function classifyChatError(error = {}) {
  error = error && typeof error === 'object' ? error : { message: String(error ?? '') };
  const message = [error.code, error.status, error.statusCode, error.message,
    error.cause?.code, error.cause?.message].filter(v => v != null).join(' ').slice(0, 8000);
  if (/insufficient[_ -]?(quota|balance|credit)|余额不足|额度不足|欠费|payment.required|\b402\b/i.test(message)) return 'balance';
  if (/\b40[13]\b|unauthori[sz]ed|invalid.api.key|authentication|鉴权|密钥.*(无效|错误)|权限不足/i.test(message)) return 'credentials';
  if (/\b429\b|rate[_ -]?limit|too.many.requests|限流|请求过于频繁/i.test(message)) return 'rate_limit';
  if (/model.*(not.found|unavailable|not.exist)|模型.*(不存在|不可用|未找到)/i.test(message)) return 'model_unavailable';
  if (/ETIMEDOUT|UND_ERR_CONNECT_TIMEOUT|timed?.?out|timeout|超时/i.test(message)) return 'upstream_timeout';
  if (/\b50[0234]\b|ECONNREFUSED|ENOTFOUND|ECONNRESET|fetch failed|service(?:[\s._-]+temporarily)?[\s._-]+unavailable|bad.gateway|上游.*(异常|不可用)/i.test(message)) return 'upstream_unavailable';
  return 'chat_error';
}

export function createChatHealth({ file, now = Date.now, log = () => {} } = {}) {
  let streamConnected = false;
  let lastResult = null;
  let lastSuccessAt = null;
  const seen = new Map();
  try {
    if (file && fs.existsSync(file)) {
      const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
      const row = saved.lastResult;
      if (row && Number.isFinite(row.at) && row.at > 0 &&
          (row.result === 'success' || (row.result === 'error' && HEALTH_CODES.has(row.code)))) {
        lastResult = { result: row.result, code: row.result === 'error' ? row.code : null, at: row.at };
      }
      if (Number.isFinite(saved.lastSuccessAt) && saved.lastSuccessAt > 0) lastSuccessAt = saved.lastSuccessAt;
    }
  } catch { log('聊天状态历史不可读，本次仅观测后续真实请求'); }

  function save() {
    if (!file) return;
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      const temp = file + '.tmp';
      fs.writeFileSync(temp, JSON.stringify({ version: 1, lastResult, lastSuccessAt }) + '\n', 'utf8');
      fs.renameSync(temp, file);
    } catch { log('聊天状态历史未能保存，当前状态仍在内存中'); }
  }

  function remember(key) {
    if (seen.has(key)) return false;
    seen.set(key, true);
    if (seen.size > 2048) seen.delete(seen.keys().next().value);
    return true;
  }

  function success() {
    const at = now();
    lastResult = { result: 'success', code: null, at };
    lastSuccessAt = at;
    save();
  }

  return {
    noteStream(connected) { streamConnected = connected === true; },
    // Call ONLY after matching a live result to a pending QQ send tool in the bridge.
    // A tool may return normally with sent:0; require actual delivery acknowledgement.
    noteSendResult(sessionId, event) {
      if (event?.type !== 'tool/result') return false;
      const message = event.data?.message;
      const callId = message?.source?.callId;
      if (callId == null || message?.isError === true || !Array.isArray(message?.content)) return false;
      if (message.content.some(block => block?.isError === true)) return false;
      const delivered = message.content.some(block => {
        if (block?.type !== 'text' || typeof block.text !== 'string') return false;
        try {
          const result = JSON.parse(block.text);
          return result?.ok === true && Number.isFinite(result.sent) && result.sent > 0;
        } catch { return false; }
      });
      if (!delivered || !remember(`send:${sessionId}:${callId}`)) return false;
      success();
      return true;
    },
    // Call ONLY for live events from current QQ chat sessions, never snapshots or learners.
    noteEvent(sessionId, event) {
      if (event?.type !== 'turn/end') return;
      const reason = event.data?.reason;
      if (reason?.kind !== 'completed' && reason?.kind !== 'error') return;
      if (!remember(`turn:${sessionId}:${event.data?.turn}`)) return;
      if (reason.kind === 'completed') {
        success();
      } else {
        lastResult = { result: 'error', code: classifyChatError(reason.error), at: now() };
        save();
      }
    },
    snapshot() {
      return { streamConnected, lastResult: lastResult ? { ...lastResult } : null, lastSuccessAt };
    }
  };
}
