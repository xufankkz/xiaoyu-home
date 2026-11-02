/* Declarative connector forms shared by the web and embedded desktop. */
window.XiaoyuRelease.connections = async function(root) {
  const request = window.XiaoyuRelease.request, state = await request('integrations');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const groups = [
    ['onebot', 'QQ：通用 OneBot v11', '先在自己的连接端开启 HTTP API 和正向 WebSocket。标准消息按 OneBot 协议连接；表情、戳一戳等扩展取决于连接端。空白名单不会放行任何联系人。', [
      ['enabled','启用 QQ 桥接','checkbox'], ['http_url','HTTP 地址'], ['ws_url','正向 WebSocket 地址'], ['token','HTTP Token（留空保持）','password'], ['ws_token','WS Token（相同时留空）','password'],
      ['owner_qq','主人 QQ 号'], ['allow_private','获授权私聊 QQ 号（逗号分隔）','ids'], ['allow_groups','获授权群号（逗号分隔）','ids']]],
    ['dsh', 'DSH 会话核心', '组件包中的独立 DSH 使用入住设置里的聊天模型和统一预算，数据留在本实例。手动接入已有 DSH 时，请自行配置随包预设和所需工具。', [
      ['enabled','启用 DSH','checkbox'], ['managed','使用组件包中的独立 DSH','checkbox'], ['http_url','手动连接 DSH 本机地址'], ['provider','手动连接的 Provider ID'], ['model','手动连接的模型 ID'], ['token','手动连接的启动令牌（留空保持）','password']]],
    ['tts', '语音合成', '使用自己的音色和后端；不含发布者的模型或参考音频。支持 GPT-SoVITS 和 OpenAI 兼容语音 API。', [
      ['enabled','启用语音后端','checkbox'], ['backend','协议','select'], ['http_url','语音 API 根地址'], ['api_key','API Key（留空保持）','password'], ['model','OpenAI 协议模型名'], ['voice','OpenAI 协议 Voice'],
      ['reference_path','GPT-SoVITS 参考音频绝对路径（服务端可读取）'], ['prompt_text','参考音频文本'], ['price_per_request','每次请求预留费用（元；本地免费填 0）','number']]],
    ['asr', '语音转写', '支持 OpenAI 兼容 /audio/transcriptions。本地部署和远程 API 均可，启用后才向所填后端发送音频。', [
      ['enabled','启用语音转写','checkbox'], ['http_url','转写 API 根地址'], ['model','转写模型名'], ['api_key','API Key（留空保持）','password'], ['price_per_request','每次转写预留费用（元；本地免费填 0）','number']]],
    ['media','本机媒体解码','指向自己安装的 FFmpeg bin 文件夹，需包含 ffmpeg.exe 与 ffprobe.exe。解码在本机进行。', [['executable_dir','FFmpeg bin 文件夹']]]
  ];
  root.innerHTML = '<div class="page-header"><h1>接入与组件</h1><p>保存后应用到本测试版；连接凭据只保存在本机。</p></div><form id="connectorForm">' + groups.map(([group,title,description,fields]) =>
    '<div class="card"><h2>' + title + '</h2><p>' + description + '</p>' + fields.map(([key,label,type]) => {
      const id = group + '_' + key, value = state.values[group]?.[key];
      if (type === 'checkbox') return '<label class="release-check"><input type="checkbox" id="' + id + '" ' + ((value === true || (group === 'dsh' && key === 'managed' && value === undefined)) ? 'checked' : '') + '> ' + label + '</label>';
      if (type === 'select') return '<label class="release-field"><span>' + label + '</span><select id="' + id + '"><option value="gpt-sovits">GPT-SoVITS /tts</option><option value="openai">OpenAI /audio/speech</option></select></label>';
      return '<label class="release-field"><span>' + label + (type === 'password' && state.values[group]?.[key + '_configured'] ? ' · 已保存' : '') + '</span><input id="' + id + '" type="' + (['number','password'].includes(type) ? type : 'text') + '" value="' + escape(type === 'password' ? '' : type === 'ids' ? (value || []).join(', ') : value ?? (type === 'number' ? 0 : '')) + '" autocomplete="off" min="0" step="0.001"></label>';
    }).join('') + '</div>').join('') + '<div class="card"><h2>后台活动与已安装组件</h2><label class="release-check"><input type="checkbox" id="automatic" ' + (state.automation_enabled ? 'checked' : '') + '> 允许核心日记、总结和学习等后台任务</label><p>首次默认关闭；模型请求受同一份每日预算约束，细项仍在原设置页面配置。请按服务商实际价格填写费用；远程服务填 0 会按零费用估算，不能据此保证账单限额。</p><p>' +
    Object.entries(state.components).map(([name,item]) => escape(name) + '：' + (item.installed ? '已安装 ' + escape(item.version) : '未安装')).join('　') +
    '</p><div class="room-actions"><button class="btn primary" type="submit">保存并应用接入</button><button class="btn" id="checkConnectors" type="button">检查连接状态</button><span id="connectorStatus" role="status"></span></div><pre class="release-text" id="connectorResult"></pre></div></form>';
  root.insertAdjacentHTML('beforeend', '<form class="card" id="memeImportForm"><h2>导入自己的表情图片</h2><p>支持 PNG、JPEG、GIF、WebP，每张最多 8 MiB。原图保留，按文件哈希去重；不会自动上传、发消息或调用模型。导入后可由已配置的 QQ 表情工具检索。</p><input type="file" id="memeImportFiles" accept=".png,.jpg,.jpeg,.gif,.webp" multiple required><button class="btn" type="submit">导入所选图片</button><span id="memeImportStatus" role="status"></span></form>');
  root.querySelector('#memeImportForm').onsubmit = async event => {
    event.preventDefault(); const button = event.submitter, status = root.querySelector('#memeImportStatus'); button.disabled = true;
    let added = 0, duplicates = 0;
    try {
      for (const file of root.querySelector('#memeImportFiles').files) {
        if (file.size > 8 * 1024 * 1024) throw new Error('每张图片最多 8 MiB：' + file.name);
        const bytes = new Uint8Array(await file.arrayBuffer()); let binary = '';
        for (let offset=0; offset<bytes.length; offset+=32768) binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
        const result = await request('memes/import', {file:btoa(binary),name:file.name});
        if (result.added) added++; else duplicates++;
      }
      status.textContent = '已导入 ' + added + ' 张，重复 ' + duplicates + ' 张。';
    } catch (error) { status.textContent = '已导入 ' + added + ' 张；' + error.message; }
    finally { button.disabled = false; }
  };
  root.querySelector('#tts_backend').value = state.values.tts?.backend || 'gpt-sovits';
  root.querySelector('#connectorForm').onsubmit = async event => {
    event.preventDefault(); const button = event.submitter, status = root.querySelector('#connectorStatus'); button.disabled = true;
    const values = {};
    for (const [group,,,fields] of groups) {
      values[group] = {};
      for (const [key,,type] of fields) { const input = root.querySelector('#' + group + '_' + key);
        values[group][key] = type === 'checkbox' ? input.checked : type === 'ids' ? input.value.split(/[,，\s]+/).filter(Boolean) : type === 'number' ? Number(input.value) : input.value.trim(); }
    }
    try { const saved = await request('integrations', {revision: state.settings.revision, integrations: values, automation_enabled: root.querySelector('#automatic').checked});
      state.settings.revision = saved.settings.revision; root.querySelectorAll('input[type="password"]').forEach(input => input.value = ''); status.textContent = '已保存，当前实例正在应用连接设置。'; }
    catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
  };
  root.querySelector('#checkConnectors').onclick = async () => {
    try { const result = await request('integrations/check', {}); root.querySelector('#connectorResult').textContent = JSON.stringify({连接: result.status, 本实例进程: result.processes}, null, 2); }
    catch (error) { root.querySelector('#connectorStatus').textContent = error.message; }
  };
};
