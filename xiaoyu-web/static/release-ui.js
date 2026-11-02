/* Shared desktop/browser onboarding and standalone local chat. */
window.XiaoyuRelease = (() => {
  'use strict';
  async function request(path, body) {
    const response = await fetch('/api/release/' + path, body === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Xiaoyu-Settings': '1'}, body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || '本地操作未完成');
    return result.data;
  }
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const field = (label, id, value, type = 'text') => '<label class="release-field"><span>' + escape(label) + '</span><input id="' + id + '" type="' + type + '" value="' + escape(value) + '" autocomplete="off"></label>';
  async function welcome(root) {
    const state = await request('state'), settings = state.settings, model = settings.models.chat || {};
    root.innerHTML = '<div class="page-header"><h1>让你的鱼住进来</h1><p>小鱼之家 ' + escape(state.version) + ' · 测试版</p></div>' +
      '<div class="card"><h2>自己的角色，自己的小屋</h2><p>这是一个全新实例。可以填写角色资料，也可以迁入已有角色。模型调用会发送当前聊天及选用的记忆给你配置的服务商。</p>' +
      '<form id="releaseSetup">' + field('角色名字', 'rName', settings.profile.name) + field('怎样称呼你', 'rAddress', settings.profile.owner_address) +
      '<label class="release-field"><span>角色简介</span><textarea id="rDescription" rows="3">' + escape(settings.profile.description) + '</textarea></label>' +
      '<label class="release-field"><span>人格与表达设定</span><textarea id="rPersona" rows="10" placeholder="保留你的角色原有经历、偏好、称呼和表达习惯">' + escape(settings.persona) + '</textarea></label>' +
      '<h3>聊天模型</h3>' + field('API 根地址', 'rEndpoint', model.endpoint || '', 'url') + field('模型名', 'rModel', model.model || '') +
      field(model.key_configured ? 'API Key 已保存；留空保持原值' : 'API Key（仅保存在本机）', 'rKey', '', 'password') +
      '<label><input id="rNoKey" type="checkbox" ' + (model.key_optional ? 'checked' : '') + '> 本地接口无需 Key</label>' +
      '<div class="release-grid">' + field('输入单价（元／百万 tokens）', 'rInput', model.input_price ?? 0, 'number') +
      field('输出单价（元／百万 tokens）', 'rOutput', model.output_price ?? 0, 'number') + field('每日模型预算（元）', 'rBudget', settings.daily_budget, 'number') + '</div>' +
      '<p class="muted">按填写单价估算，预算为 0 时不发送收费请求。无法确认的请求保留预留额度，实际账单以服务商为准。</p>' +
      '<div class="room-actions"><button class="btn primary" type="submit">保存入住设置</button><span id="rResult" role="status"></span></div></form></div>' +
      '<div class="card"><h2>按需接入</h2><p>QQ、语音、表情包、绘画和本地模型保留各自的入口。用户填写自己的连接参数和素材；未接入的组件会显示未配置。</p>' +
      '<div class="room-actions"><a class="btn" href="#/chat">开始聊天</a><a class="btn" href="#/connections">QQ 与可选组件</a><a class="btn" href="#/voice">语音</a><a class="btn" href="#/settings">外观与其他设置</a></div></div>';
    root.querySelectorAll('input[type="number"]').forEach(input => { input.min = '0'; input.step = '0.001'; });
    root.querySelector('#releaseSetup').onsubmit = async event => {
      event.preventDefault();
      const button = event.submitter, result = root.querySelector('#rResult'), value = id => root.querySelector('#' + id).value;
      button.disabled = true; result.textContent = '正在保存…';
      const body = {revision: settings.revision, profile: {name: value('rName'), owner_address: value('rAddress'), description: value('rDescription')}, persona: value('rPersona'), daily_budget: Number(value('rBudget'))};
      if (value('rEndpoint') || value('rModel')) body.models = {chat: {endpoint: value('rEndpoint'), model: value('rModel'), api_key: value('rKey'), input_price: Number(value('rInput')), output_price: Number(value('rOutput')), key_optional: root.querySelector('#rNoKey').checked}};
      try { const saved = await request('settings', body); settings.revision = saved.revision; root.querySelector('#rKey').value = ''; result.textContent = '已保存；可前往聊天。'; }
      catch (error) { result.textContent = error.message; }
      finally { button.disabled = false; }
    };
  }
  async function chat(root) {
    root.innerHTML = '<div class="page-header"><h1>和她聊天</h1><p>在本机小屋聊天，使用同一份人格、记忆和情绪核心。</p></div><div class="card"><div id="localMessages" class="release-messages" aria-live="polite"></div>' +
      '<form id="localChatForm"><label class="release-field"><span>你的消息</span><textarea id="localInput" rows="3" maxlength="12000" required></textarea></label><div class="room-actions"><button class="btn primary" type="submit">发送</button><span id="localStatus" role="status"></span></div></form></div><p><a href="#/welcome">配置角色和模型</a></p>';
    const messages = root.querySelector('#localMessages');
    const render = items => { messages.innerHTML = items.length ? items.map(item => '<div class="release-turn"><p><strong>你</strong></p><p class="release-text">' + escape(item.user_text) + '</p>' +
      (item.reply ? '<p><strong>角色</strong></p><p class="release-text">' + escape(item.reply) + '</p>' : '<p class="muted">' + escape(item.error || '等待回复…') + '</p>') + '</div>').join('') : '<p class="muted">入住设置完成后，发出第一条消息吧。</p>'; };
    render((await request('chat')).items);
    root.querySelector('#localChatForm').onsubmit = async event => {
      event.preventDefault();
      const button = event.submitter, input = root.querySelector('#localInput'), status = root.querySelector('#localStatus');
      if (!input.value.trim()) return;
      button.disabled = true; status.textContent = '正在思考…';
      try {
        const reply = await request('chat', {text: input.value, request_id: crypto.randomUUID()});
        if (reply.status === 'completed') { input.value = ''; status.textContent = '已回复'; }
        else status.textContent = reply.error;
        render((await request('chat')).items);
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    };
  }
  async function migration(root) {
    root.innerHTML = '<div class="page-header"><h1>把你的鱼迁进来</h1><p>先预览差异，再增量补齐；原文件保持原样。</p></div>' +
      '<div class="card"><h2>迁入已有角色</h2><label class="release-field"><span>来源格式</span><select id="migrationKind"><option value="fish">小鱼之家加密迁移包（.fish）</option><option value="astrbot-livingmemory">AstrBot livingmemory 数据库（documents 格式）</option><option value="dsh-native">DSH 原生完整会话（JSONL / JSONL.Zst）</option><option value="dsh">DSH 对话 JSON 导出（sessions/messages-v1）</option><option value="astrbot">AstrBot 对话 JSON 导出（sessions/messages-v1）</option></select></label>' +
      '<label class="release-field"><span>选择本地迁移文件</span><input id="migrationFile" type="file" accept=".fish,.json,.jsonl,.zst,.db,.sqlite"></label>' +
      field('加密包密码（仅在本机使用）', 'migrationPassword', '', 'password') +
      '<div class="room-actions"><button class="btn primary" id="migrationPlan">预览迁入差异</button><button class="btn" id="migrationApply" disabled>依据预览迁入</button><span id="migrationStatus" role="status"></span></div><pre class="release-text" id="migrationPreview"></pre></div>' +
      '<div class="card"><h2>导出加密迁移包</h2><p>包含人格、核心记忆、情绪与思考记录及受支持的本地文档。不会导出 API Key、登录 Cookie、日志或外部模型权重。</p>' +
      field('设置迁移包密码（至少 8 个字符）', 'exportPassword', '', 'password') +
      '<button class="btn" id="migrationExport">导出 .fish 加密包</button><span id="exportStatus" role="status"></span></div>' +
      '<div class="card"><h2>兼容范围</h2><p>小鱼之家本版本格式直接支持。AstrBot 的 livingmemory 支持 doc_id、text、metadata 三列；其他插件状态需要单独适配。DSH 原生完整会话由官方版本化读取器验证，需要安装 DSH 组件；DSH/AstrBot JSON 对话导出需符合随包文档，未知原始目录不会被猜测或改写。媒体附件和模型权重保留原文件后自行接入。</p></div>';
    let plan = null;
    const status = root.querySelector('#migrationStatus'), apply = root.querySelector('#migrationApply');
    root.querySelector('#migrationPlan').onclick = async event => {
      const file = root.querySelector('#migrationFile').files[0];
      if (!file) { status.textContent = '请先选择文件。'; return; }
      if (file.size > 128 * 1024 * 1024) { status.textContent = '此测试版单个迁移包最多 128 MiB。'; return; }
      event.target.disabled = true; apply.disabled = true; status.textContent = '正在本机核对…';
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = ''; for (let index = 0; index < bytes.length; index += 32768) binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
        plan = await request('migration/plan', {kind: root.querySelector('#migrationKind').value, file: btoa(binary), password: root.querySelector('#migrationPassword').value});
        root.querySelector('#migrationPassword').value = '';
        root.querySelector('#migrationPreview').textContent = JSON.stringify({来源: plan.source, 新增记录: plan.additions, 已有相同记录: plan.existing, 保留的冲突记录: plan.conflicts_preserved, 未适配表: plan.unsupported_tables, 文档数: plan.documents, 人设: plan.has_persona ? '存在；仅空白角色可采用' : '不含人设'}, null, 2);
        apply.disabled = false; status.textContent = '预览完成，尚未写入。';
      } catch (error) { status.textContent = error.message; }
      finally { event.target.disabled = false; }
    };
    apply.onclick = async () => {
      if (!plan) return;
      apply.disabled = true; status.textContent = '正在核验快照并迁入…';
      try { const result = await request('migration/apply', {plan_id: plan.plan_id}); status.textContent = '迁入完成；原记录保留，已建立核验快照。'; root.querySelector('#migrationPreview').textContent = JSON.stringify(result, null, 2); }
      catch (error) { status.textContent = error.message; }
    };
    root.querySelector('#migrationExport').onclick = async event => {
      event.target.disabled = true; const resultStatus = root.querySelector('#exportStatus'); resultStatus.textContent = '正在本机导出…';
      try {
        const result = await request('migration/export', {password: root.querySelector('#exportPassword').value});
        root.querySelector('#exportPassword').value = '';
        const bytes = Uint8Array.from(atob(result.file), c => c.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], {type: 'application/octet-stream'})), anchor = document.createElement('a');
        anchor.href = url; anchor.download = result.filename; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
        resultStatus.textContent = '已生成加密包，请妥善保管密码。';
      } catch (error) { resultStatus.textContent = error.message; }
      finally { event.target.disabled = false; }
    };
  }
  return {welcome, chat, migration, request};
})();
