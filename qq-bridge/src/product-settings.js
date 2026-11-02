// Product settings use the existing authenticated DSH client and its supported credential API.
import fs from 'node:fs';
import crypto from 'node:crypto';

export const CHAT_KEY_REF = 'XIAOYU_CHAT_API_KEY';
const checksum = (raw) => crypto.createHash('sha256').update(raw).digest('hex');

export function assertFileRevision(file, expected) {
  if (expected !== undefined && (typeof expected !== 'string' || checksum(fs.readFileSync(file)) !== expected)) {
    throw new Error('设置已被其他页面修改，请重新加载。');
  }
}

export function createProductModelSettings({ api, unwrap, llmFile, write }) {
  async function info() {
    const description = unwrap(await api.settings.describe(), 'settings.describe');
    const namespaces = Array.isArray(description) ? description : description?.namespaces || [];
    const find = (name) => namespaces.find(n => n.ns === name);
    const effective = (ns) => ({ ...(ns?.value || {}), ...(ns?.user || {}) });
    const chosen = effective(find('agent-default-model'));
    const providers = effective(find('llm-pi-ai')).providers || {};
    const provider = providers[chosen.provider] || {};
    const ref = provider.apiKeyEnv;
    let configured = false;
    if (ref === CHAT_KEY_REF) {
      const values = unwrap(await api.callUnary('credentials/describe', { refs: [CHAT_KEY_REF] }), 'credentials.describe');
      configured = values?.[CHAT_KEY_REF]?.configured === true || values?.[CHAT_KEY_REF]?.set === true || values?.[CHAT_KEY_REF]?.available === true;
      // Core and chat intentionally share this reference; never return its stored value.
      const core = JSON.parse(fs.readFileSync(llmFile, 'utf8').replace(/^\uFEFF/, ''));
      configured ||= !!core?.providers?.http?.api_key;
    }
    let endpoint = '';
    try { const u = new URL(provider.baseURL); endpoint = u.origin + u.pathname; } catch {}
    return { provider: String(chosen.provider || '未配置'), model: String(chosen.model || '未配置'),
      endpoint, key_ref: ref === CHAT_KEY_REF ? CHAT_KEY_REF : '其他凭据引用', configured,
      can_replace_key: ref === CHAT_KEY_REF, desktop_separate: ref === CHAT_KEY_REF,
      purpose: '小鱼聊天及小鱼后台任务；与 DSH 桌面 Key 分开。后台统计也可能包含记忆、学习等调用。' };
  }
  async function replaceKey(value) {
    if (typeof value !== 'string' || value.length < 16 || value.length > 512 || /\s/.test(value)) throw new Error('新 Key 格式无效。');
    const current = await info();
    if (!current.can_replace_key) throw new Error('当前模型未使用小鱼专用凭据，请先检查模型配置。');
    const raw = fs.readFileSync(llmFile, 'utf8');
    const core = JSON.parse(raw.replace(/^\uFEFF/, ''));
    if (!core?.providers?.http || typeof core.providers.http !== 'object') throw new Error('后台模型配置不可用。');
    core.providers.http.api_key = value;
    core.providers.http.api_key_env = CHAT_KEY_REF;
    write(llmFile, core);
    try {
      unwrap(await api.callUnary('credentials/set', { ref: CHAT_KEY_REF, value }), 'credentials.set');
    } catch {
      write(llmFile, JSON.parse(raw.replace(/^\uFEFF/, '')));
      throw new Error('凭据保存未确认，后台配置已回滚，请重新检查凭据状态。');
    }
    return { configured: true, desktop_separate: true, message: '小鱼专用 Key 已更新；DSH 桌面 Key 保持原值。未发送模型测试请求。' };
  }
  return { info, replaceKey };
}
