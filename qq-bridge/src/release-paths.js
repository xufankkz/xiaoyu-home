import fs from 'node:fs';
import path from 'node:path';
export function releasePath(relative = '') {
  if (!process.env.XIAOYU_WORKSPACE) throw new Error('请通过发行版启动器运行。');
  const root = path.resolve(process.env.XIAOYU_WORKSPACE), target = path.resolve(root, relative);
  if (target !== root && !target.startsWith(root + path.sep)) throw new Error('路径超出当前实例。');
  return target;
}
export function releaseUrl(service, suffix = '') {
  const state = JSON.parse(fs.readFileSync(process.env.XIAOYU_RUNTIME, 'utf8'));
  return (state.integrations?.[service] || `http://127.0.0.1:${state.ports[service]}`).replace(/\/+$/, '') + suffix;
}

// Only authenticate requests to this instance's own services. External connectors
// keep their own credentials, and internal redirects are never followed.
if (process.env.XIAOYU_RUNTIME && !globalThis.__xiaoyuInternalFetch) {
  const originalFetch = globalThis.fetch.bind(globalThis);
  globalThis.__xiaoyuInternalFetch = true;
  globalThis.fetch = (input, init = {}) => {
    const state = JSON.parse(fs.readFileSync(process.env.XIAOYU_RUNTIME, 'utf8'));
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
    const names = ['core', 'model', 'qzone', 'diary', 'memes', 'media'];
    if (url.hostname === '127.0.0.1' && names.some(name => state.ports[name] === Number(url.port))) {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-Xiaoyu-Internal', state.internal_token);
      return originalFetch(input, {...init, headers, redirect: 'error'});
    }
    return originalFetch(input, init);
  };
}
