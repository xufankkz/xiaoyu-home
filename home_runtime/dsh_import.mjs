// Read a user-selected log through the official, versioned physical codecs.
// The codec operates on supplied JSON values and writes no DSH state.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const entry = path.join(input.component, 'node_modules/@deepseek-ai/dsh-session-format-catalog/lib/index.js');
  const { sessionFormatCatalog } = await import(pathToFileURL(entry).href);
  const rows = input.text.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
  if (!rows.length) throw new Error('empty session log');
  const restore = sessionFormatCatalog.createRestore(rows[0], { recovery: 'strict', validation: 'current' });
  for (const row of rows.slice(1)) restore.decodeRow(row);
  const result = restore.finish();
  process.stdout.write(JSON.stringify({ok:true, artifact:result}));
} catch {
  process.stdout.write(JSON.stringify({ok:false,error:'DSH 日志未通过官方格式校验；原文件未修改。请导出完整会话，未知或需子会话证据的版本须先在原 DSH 导出。'}));
  process.exitCode = 1;
}
