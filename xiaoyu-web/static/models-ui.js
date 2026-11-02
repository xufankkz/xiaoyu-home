window.XiaoyuRelease.models = async function(root) {
  const request = window.XiaoyuRelease.request, state = await request('state'), settings = state.settings;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const groups = [['chat','聊天与核心任务','QQ 的多模态理解也使用此模型；需要看图时请选择支持图片的模型。'], ['vision','图片理解与表情标注','用于表情包自动描述等独立看图请求；请选择支持图片的聊天接口。'], ['drawing','绘画','兼容 /images/generations，每次只生成一张；按每次费用预留预算。'], ['douyin','抖音观看理解','兼容聊天接口，分析抽样画面需支持图片。只发送公开角色介绍和当前观看资料。']];
  const fields = [['endpoint','API 根地址','url'], ['model','模型名','text'], ['api_key','API Key（留空保持）','password'], ['input_price','输入单价（元／百万 tokens）','number'], ['output_price','输出单价（元／百万 tokens）','number'], ['price_per_request','绘画每次预留费用（元；其他类型填 0）','number']];
  root.innerHTML = '<div class="page-header"><h1>模型与费用</h1><p>模型由使用者自行配置；费用按填写单价估算。</p></div>' + groups.map(([id,title,note]) => {
    const model = settings.models[id] || {};
    return '<form class="card modelForm" data-model="' + id + '"><h2>' + title + '</h2><p>' + note + '</p>' + fields.map(([name,label,type]) => '<label class="release-field"><span>' + label + '</span><input name="' + name + '" type="' + type + '" min="0" step="0.001" value="' + esc(type === 'password' ? '' : model[name] ?? (type === 'number' ? 0 : '')) + '" autocomplete="off"></label>').join('') + '<label class="release-check"><input name="key_optional" type="checkbox" ' + (model.key_optional ? 'checked' : '') + '> 此接口无需 Key</label><button class="btn primary">保存此模型</button><span role="status"></span></form>';
  }).join('') + '<div class="card"><h2>今日请求账本</h2><p>请求数 ' + state.usage.requests + '，估算费用 ' + esc(state.usage.estimated_cost.toFixed(6)) + ' 元，每日预算 ' + esc(state.usage.daily_budget) + ' 元。</p><p>' + esc(state.usage.note) + '</p><pre class="release-text">' + esc(JSON.stringify(state.usage.recent, null, 2)) + '</pre></div>';
  root.querySelectorAll('.modelForm').forEach(form => form.onsubmit = async event => {
    event.preventDefault(); const model = {}, button = form.querySelector('button'), status = form.querySelector('[role="status"]'); button.disabled = true;
    for (const [name,,type] of fields) model[name] = type === 'number' ? Number(form.elements[name].value) : form.elements[name].value.trim();
    model.key_optional = form.elements.key_optional.checked;
    try { const result = await request('settings', {revision: settings.revision, models: {[form.dataset.model]: model}}); settings.revision = result.revision; form.elements.api_key.value = ''; status.textContent = '已保存'; }
    catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
  });
};
