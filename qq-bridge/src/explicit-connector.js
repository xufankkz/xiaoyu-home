// Compatibility names for explicit OneBot settings. Does not discover accounts.
import fs from 'node:fs';
import { releasePath } from './release-paths.js';
export async function discoverSnowLumaConnection() {
  const config = JSON.parse(fs.readFileSync(releasePath('qq-bridge/config.json'), 'utf8'));
  const connection = config.snowluma || {};
  if (!connection.httpUrl || !connection.wsUrl) throw new Error('请先配置 OneBot v11 接口。');
  return { baseUrl: connection.httpUrl, wsUrl: connection.wsUrl,
    token: process.env.XIAOYU_ONEBOT_TOKEN || connection.accessToken || '', wsToken: process.env.XIAOYU_ONEBOT_WS_TOKEN || connection.wsAccessToken || process.env.XIAOYU_ONEBOT_TOKEN || connection.accessToken || '',
    source: 'user-configured' };
}
export function persistSnowLumaEndpoint() { return { updated: false, changed: {} }; }
export function persistToken() { return { updated: false, from: '', to: '' }; }
