// Pure preparation for a shared personality save. No writes or live actions.
import yaml from 'js-yaml';
import { extractPresetPrefix, renderPresetPromptYaml } from './preset-prompt.js';
import { renderPatch } from '../scripts/build-agent-preset-patches.mjs';

export function prepareCharacterPersona(text, sources) {
  const persona = String(text ?? '').replaceAll('\r\n', '\n').trim();
  if (!persona || persona.length > 400000) throw new Error('人设为空或内容过长');
  if (/【小鱼·人设·(?:开始|结束)/.test(persona)) throw new Error('人设正文中不能包含内部同步标记');
  const result = {};
  for (const name of ['qq-chat', 'qq-chat-v2']) {
    const source = sources[name];
    const prefix = extractPresetPrefix(source?.text);
    if (prefix === null) throw new Error(`${name} 的预设正文无法读取`);
    const pattern = /(【小鱼·人设·开始[^\n]*\n)[\s\S]*?(\n[^\S\n]*【小鱼·人设·结束】)/g;
    const starts = [...prefix.matchAll(/【小鱼·人设·开始/g)].length;
    const ends = [...prefix.matchAll(/【小鱼·人设·结束/g)].length;
    let nextPrefix;
    if (name === 'qq-chat' && starts === 0 && ends === 0) {
      // Older V1 installations have no character section. Retain their entire
      // parsed prefix, including the V1 sending protocol, and initialize it once.
      nextPrefix = prefix + '\n\n【小鱼·人设·开始】\n' + persona + '\n【小鱼·人设·结束】';
    } else {
      if (starts !== 1 || ends !== 1 || [...prefix.matchAll(pattern)].length !== 1) {
        throw new Error(`${name} 的人设同步标记缺失或重复`);
      }
      nextPrefix = prefix.replace(pattern, (_match, start, end) => start + persona + end);
    }
    const variables = [...nextPrefix.matchAll(/\{\{([a-z][a-z0-9_]*)\}\}/g)].map(m => m[1]);
    if (variables.some(v => !['model', 'provider'].includes(v))) throw new Error('人设含有当前模型运行时不支持的模板变量');
    const nextSource = renderPresetPromptYaml(source.text, nextPrefix, name === 'qq-chat' ? 'v1' : 'v2');
    result[name] = { source: nextSource,
      bundle: renderPatch(name, { rows: yaml.load(nextSource), metadata: yaml.load(source.metadata) }) };
  }
  return result;
}
