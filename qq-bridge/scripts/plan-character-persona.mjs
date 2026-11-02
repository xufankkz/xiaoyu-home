// stdin/stdout are used only by the local settings store. Never calls DSH/model/QQ.
import { prepareCharacterPersona } from '../src/character-persona.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
try {
  const body = JSON.parse(input);
  process.stdout.write(JSON.stringify(prepareCharacterPersona(body.text, body.sources)));
} catch {
  process.stderr.write('无法准备同步的人设，请检查预设结构、正文长度和模板变量。');
  process.exitCode = 1;
}
