(function () {
  'use strict';
  const clone = v => JSON.parse(JSON.stringify(v));
  async function request(path, body) {
    const response = await fetch('/api/settings' + path, body === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Xiaoyu-Settings': '1' }, body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || '设置读取失败');
    return data.data;
  }
  function applyAppearance(value, remember = false) {
    const appearance = value || {};
    window.XiaoyuThemes.apply(appearance.theme, remember, appearance.color_mode || 'light', appearance.night_palette || 'charcoal');
    document.documentElement.style.setProperty('--font-scale', ({ standard: 1, large: 1.1, larger: 1.2 })[appearance.font_size] || 1);
    const scale = [90, 100, 110, 125].includes(appearance.scale) ? appearance.scale / 100 : 1;
    document.documentElement.style.setProperty('--ui-scale', scale);
    document.documentElement.style.setProperty('--ui-height', (window.innerHeight / scale) + 'px');
    document.body.dataset.density = appearance.density || 'comfortable';
    document.body.dataset.motion = appearance.animations === false ? 'off' : 'on';
  }
  async function syncAppearance() {
    if (document.hidden || window.XiaoyuSettings?.editingAppearance) return;
    try { const value = await request('/appearance'); if (!window.XiaoyuSettings.editingAppearance) applyAppearance(value, true); } catch (_) { /* Keep the last visible preference while offline. */ }
  }
  function download(name, value) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function mount(root, q) {
    const token = app.token;
    let data = await request('');
    if (token !== app.token) return;
    let original = clone(data.values), draft = clone(original), busy = false, disposed = false;
    applyAppearance(original.appearance, true);
    let model = null, douyinSettings = null;
    const initialTab = ['common', 'models', 'maintenance'].includes(q.get('tab')) ? q.get('tab') : 'common';
    const options = (values, selected) => values.map(([value, label]) => `<option value="${esc(value)}" ${value === selected ? 'selected' : ''}>${esc(label)}</option>`).join('');
    const field = (section, key, title, hint, control) => `<label class="setting-row"><span class="setting-label"><strong>${esc(title)}</strong><span>${esc(hint)}</span></span><span class="setting-control">${control}</span></label>`;
    const input = (section, key, title, hint, min, max, step = 1, type = 'number') => field(section, key, title, hint,
      `<input data-setting="${section}.${key}" aria-label="${esc(title)}" type="${type}" ${type === 'number' ? `min="${min}" max="${max}" step="${step}"` : ''} value="${esc(draft[section][key])}">`);
    const toggle = (section, key, title, hint) => field(section, key, title, hint,
      `<span class="setting-switch"><input data-setting="${section}.${key}" aria-label="${esc(title)}" type="checkbox" ${draft[section][key] ? 'checked' : ''}><span aria-hidden="true"></span></span>`);
    const select = (section, key, title, hint, values) => field(section, key, title, hint,
      `<select data-setting="${section}.${key}" aria-label="${esc(title)}">${options(values, draft[section][key])}</select>`);
    const card = (title, subtitle, content) => `<section class="card settings-card"><h2>${esc(title)}</h2><p class="settings-caption">${esc(subtitle)}</p>${content}</section>`;
    root.innerHTML = pageHead('设置', '把小屋调整成你和小鱼习惯的样子。') + `
      <div class="settings-shell">
        <div class="section-tabs settings-tabs" role="tablist" aria-label="设置分区">
          <button role="tab" data-settings-tab="common">常用设置</button>
          <button role="tab" data-settings-tab="models">模型与花费</button>
          <button role="tab" data-settings-tab="maintenance">数据与维护</button>
        </div>
        <form id="settingsForm">
          <div class="settings-panel" data-panel="common" role="tabpanel">
            ${card('小屋的四种模样', '点选即可预览，保存后网页和软件共用；切换时继续使用同一份数据。',
              '<fieldset class="theme-picker"><legend class="sr-only">选择界面主题</legend>' + window.XiaoyuThemes.catalog.map(theme =>
                `<label class="theme-option" data-theme-option="${theme.id}"><input type="radio" name="roomTheme" data-setting="appearance.theme" value="${theme.id}" ${draft.appearance.theme === theme.id ? 'checked' : ''}><span class="theme-preview" data-theme-preview="${theme.id}" aria-hidden="true"><i class="theme-mini-nav"><b></b><em></em><em></em><em></em></i><i class="theme-mini-main"><b></b><span><em></em><em></em></span></i></span><span class="theme-option-title">${theme.name}<span class="theme-selected">已选择</span></span><span class="theme-option-note">${theme.note}</span><span class="theme-layout">${theme.layout}</span></label>`
              ).join('') + '</fieldset><div class="theme-preview-status"><span id="themePreviewStatus" role="status"></span><button type="button" class="btn sm" id="themeRevert">还原已保存外观</button></div>')}
            ${card('看着舒服', '网页与软件共用这些显示偏好，保存后两边同步。',
              select('appearance', 'color_mode', '显示模式', '夜间模式适用于四种小屋；跟随系统会自动切换深浅色。选择后可预览，点击“保存修改”生效。', Object.entries(window.XiaoyuThemes.colorModes)) +
              select('appearance', 'night_palette', '夜间配色', '保持深灰背景，只切换数字、图标、标签等强调元素的搭配色。可选原淡紫、晴蓝（#4ea2f7）和暮蓝（#293044），保存后两端同步。', Object.entries(window.XiaoyuThemes.nightPalettes)) +
              select('appearance', 'font_size', '字体大小', '放大文字，保持边缘清晰。', [['standard', '标准'], ['large', '较大'], ['larger', '更大']]) +
              select('appearance', 'scale', '界面缩放', '调整整个界面的显示比例。', [[90, '90%'], [100, '100%'], [110, '110%'], [125, '125%']]) +
              select('appearance', 'density', '布局密度', '选择留白和列表间距。', [['comfortable', '舒适'], ['compact', '紧凑']]) +
              toggle('appearance', 'animations', '轻微动效', '关闭后立即切换，系统减少动态效果设置始终优先。'))}
            ${card('慢一点听，主动一点关心', '连续补充会重新等待；主动联系仍遵守白名单、暂停状态和现有权限。',
              input('chat', 'wait_seconds', '回复等待（秒）', '最后一条消息后的最短等待；未说完时可能再等一会儿。', 1, 120) +
              toggle('chat', 'memory_enabled', '聊天时参考记忆与近期经历', '自动带入相关记忆和有来源的近期经历，输入量会有所增加。关闭只停止自动带入，已有记录仍保留；独立记忆工具按原权限使用。') +
              toggle('chat', 'proactive_enabled', '每日主动联系', '为允许聊天的白名单对话安排主动关心。模型或联系人不可用时不保证送达。') +
              input('chat', 'daily_count', '每日主动联系目标', '每个对话每天最多 1–3 次，默认沿用 1 次。', 1, 3) +
              input('chat', 'idle_minutes', '空闲多久后主动联系（分钟）', '双方刚聊完时，先留出安静的时间。', 5, 1440) +
              input('chat', 'retry_minutes', '主动联系尝试间隔（分钟）', '避免重复找话题太频繁。', 5, 1440) +
              input('chat', 'start', '主动联系开始时间', '按服务所在电脑的本地时间。', 0, 0, 1, 'time') +
              input('chat', 'end', '主动联系结束时间', '结束时间应晚于开始时间。', 0, 0, 1, 'time') +
              input('chat', 'quiet_start', '免打扰开始时间', '只限制主动找人，不阻止正常回复。起止相同表示不设免打扰。', 0, 0, 1, 'time') +
              input('chat', 'quiet_end', '免打扰结束时间', '支持跨越午夜，例如 23:30 到 08:30。', 0, 0, 1, 'time'))}
            ${card('听见小鱼，也让她听见你', '保留当前音色、参考音频、自然拖尾和已有后处理。',
              toggle('voice', 'auto_enabled', '自动发送语音条', '适合用语音表达的短消息，会按比例选择语音。') +
              input('voice', 'percent', '自动语音比例（%）', '长期目标比例，不要求每十条严格发送三条。', 0, 100) +
              select('voice', 'default_tone', '试听默认语气', '用于默认试听；聊天时仍按语境选择情感。', Object.keys(draft.voice.speeds).map(key => [key, ({gentle:'温柔', playful:'俏皮', tsundere:'傲娇'})[key] || key])) +
              Object.entries(draft.voice.speeds).map(([key, speed]) => field('voice', 'speeds', (({gentle:'温柔', playful:'俏皮', tsundere:'傲娇'})[key] || key) + '语速', '1.00 为原速，各语气分别保留。', `<input data-speed="${esc(key)}" aria-label="${esc(key)}语速" type="number" min="0.8" max="1.5" step="0.01" value="${esc(speed)}">`)).join('') +
              toggle('voice', 'incoming_voice', '听懂普通话语音', '收到语音后转成文字理解，内容进入现有记忆链路。') +
              toggle('voice', 'incoming_video', '理解收到的视频', '沿用视频时间采样理解；媒体处理服务需在线。'))}
            ${card('每天学一点', '接入现有网页学习配额，不会因为打开或保存设置立即开始学习。',
              toggle('learning', 'enabled', '允许网页学习', '保留现有学习内容、来源和报告。') +
              toggle('learning', 'auto_run', '允许自动网页学习', '关闭后仍可从学习页面手动执行。') +
              input('learning', 'rounds_per_day', '每天最多学习轮数', '填写 0 表示当天不安排轮次。', 0, 60) +
              input('learning', 'items_per_round', '每轮最多条目', '限制每轮网页学习规模。', 1, 50) +
              input('learning', 'min_interval_minutes', '学习最短间隔（分钟）', '此处为网页学习配额；其他学习引擎设置保留在学习页。', 0, 1440) +
              '<a class="settings-link" href="#/learning">打开学习页面 →</a>')}
          </div>
          <div class="settings-panel" data-panel="models" role="tabpanel" hidden>
            ${card('小鱼使用的模型', '读取本地配置，不发送模型测试请求。', '<div id="settingsModel" class="settings-info">正在读取连接配置…</div><button type="button" class="btn" id="settingsModelRefresh">重新读取配置</button><details class="settings-key"><summary>更换小鱼专用 Key</summary><p class="tiny muted">同时更新小鱼聊天与后台任务使用的专用 Key。DSH 桌面 Key 保持分离，旧值保存在本机快照中。</p><label for="settingsKey">新的专用 Key</label><input type="password" id="settingsKey" autocomplete="new-password" spellcheck="false" placeholder="留空不修改"><button type="button" class="btn" id="settingsKeySave" disabled>保存新 Key</button><p id="settingsKeyResult" class="tiny" role="status"></p></details>')}
            <section class="card settings-card" id="settingsDouyinCard"><h2>抖音专用模型</h2><p class="settings-caption">抖音的接口、模型、专用 Key 和用量上限统一放在这里。</p><div id="settingsDouyin"></div></section>
            ${card('小鱼的画室', '聊天时可以请小鱼画画，也可以请她把原画分享给已授权的白名单联系人。',
              toggle('drawing', 'enabled', '允许画画', '按聊天请求绘画，不会因打开设置或日常监测调用绘画模型。') +
              select('drawing', 'key_mode', '绘画 Key 来源', '沿用小鱼 Key，或单独保存绘画 Key；聊天配置始终保留。', [['chat', '沿用小鱼专用 Key'], ['separate', '绘画专用 Key']]) +
              input('drawing', 'endpoint', '绘画接口根地址', '留空沿用聊天接口；填写如 https://服务地址/v1。', 0, 0, 1, 'url') +
              input('drawing', 'model', '绘画模型', '填写服务商已为当前 Key 开通的准确模型名。聊天模型不能直接代替绘画模型。', 0, 0, 1, 'text') +
              select('drawing', 'size', '画布尺寸', '请以绘画模型支持的尺寸为准。', [['1024x1024', '方形 · 1024 × 1024'], ['1536x1024', '横版 · 1536 × 1024'], ['1024x1536', '竖版 · 1024 × 1536']]) +
              input('drawing', 'daily_limit', '每天最多画几张', '所有对话共用上限，含失败请求；避免重复试画产生额外费用。', 1, 100) +
              field('drawing', 'style', '默认画风', '当前默认是白纸上的儿童彩笔涂鸦。画风控制线条和表现，小鱼结合聊天决定画什么。', `<textarea data-setting="drawing.style" aria-label="默认画风" maxlength="1200" rows="5">${esc(draft.drawing.style)}</textarea>`) +
              field('drawing', 'character_description', '小鱼的外观补充', '可留空使用当前角色资料；填写后帮助不同作品保持形象一致。', `<textarea data-setting="drawing.character_description" aria-label="小鱼的外观补充" maxlength="1200" rows="3">${esc(draft.drawing.character_description)}</textarea>`) +
              field('drawing', 'companion_description', '默认陪伴者外观', '可填写你想用的卡通形象；其他联系人有自己的资料时以对话为准。', `<textarea data-setting="drawing.companion_description" aria-label="默认陪伴者外观" maxlength="1200" rows="3">${esc(draft.drawing.companion_description)}</textarea>`) +
              '<div id="settingsDrawingStatus" class="settings-info" role="status">正在读取画室配置…</div><button type="button" class="btn" id="settingsDrawingRefresh">刷新画室状态</button><details class="settings-key"><summary>保存绘画专用 Key</summary><p class="tiny muted">保存后仅绘画使用该 Key，不修改聊天 Key。请先保存上面的设置。</p><input type="password" id="settingsDrawingKey" aria-label="绘画专用 Key" autocomplete="new-password" spellcheck="false" placeholder="留空不修改"><button type="button" class="btn" id="settingsDrawingKeySave">保存绘画 Key</button><p id="settingsDrawingKeyResult" class="tiny" role="status"></p></details>')}
            ${card('小鱼的画册', '保留最近作品、生成状态与送达状态。画面描述会进入原有聊天记忆。', '<div id="settingsDrawingGallery" class="drawing-gallery">正在打开画册…</div>')}
            ${card('花费心里有数', '只读取已记录花费。提醒不会自动停聊，也不会为了监测调用模型。',
              input('budget', 'daily_warning', '每日花费提醒（元）', '0 表示关闭提醒；基于本地已记录金额，可能与供应商账单有差异。', 0, 10000, .1) +
              '<div id="settingsBudgetStatus" class="settings-info"></div><a class="settings-link" href="#/cost">查看花费与 Token 记录 →</a>')}
          </div>
          <div class="settings-panel" data-panel="maintenance" role="tabpanel" hidden>
            ${card('给小屋留一份备份', '备份在本机保存并校验，包含原始配置和数据；不会覆盖旧备份。',
              toggle('backup', 'enabled', '每日自动备份', '共用网页服务运行时按电脑本地时间检查；错过时间会在当天补做一次。') +
              input('backup', 'time', '每日备份时间', '自动备份默认关闭，开启后每天最多尝试一次。', 0, 0, 1, 'time') +
              '<div id="settingsBackupStatus" class="settings-info" aria-live="polite"></div><button type="button" class="btn" id="settingsBackupNow">立即备份并校验</button>')}
            ${card('数据与原有设定', '旧数据、人设、称呼、白名单、关系和聊天模式沿用原有管理入口。',
              '<div class="settings-links"><a href="#/persona">人设</a><a href="#/contacts">联系人与称呼</a><a href="#/memory">记忆</a><a href="#/qq">QQ 接入</a></div><div class="settings-actions"><button type="button" class="btn" id="settingsExport">导出设置</button><button type="button" class="btn" id="settingsDiagnostics">导出诊断摘要</button></div><p class="tiny muted">导出文件不含 Key、聊天内容、联系人及记忆原文。完整恢复使用本机备份。</p>')}
            <section class="card" id="roomRecovery"></section>
            ${card('关于小鱼之家', '网页与软件加载同一套页面和 API。', '<div data-room-release>正在读取版本…</div><p class="tiny muted">设置以服务器保存值为准。界面偏好约 15 秒内同步到另一端；聊天、语音和学习选项保存后由现有服务读取。</p>')}
          </div>
          <div class="settings-savebar"><span id="settingsSaveState" role="status">已加载当前设置</span><div><button type="button" class="btn" id="settingsReload">重新加载</button><button type="submit" class="btn primary" id="settingsSave" disabled>保存修改</button></div></div>
        </form>
        <form id="settingsDouyinForm"></form>
      </div>`;
    const form = root.querySelector('#settingsForm'), state = root.querySelector('#settingsSaveState');
    const dirty = () => JSON.stringify(original) !== JSON.stringify(draft);
    const anyDirty = () => dirty() || !!douyinSettings?.isDirty();
    const anyBusy = () => busy || !!douyinSettings?.isBusy();
    function mark() {
      state.textContent = dirty() ? '尚未保存' : douyinSettings?.isDirty() ? '抖音模型设置尚未保存，请使用该区的保存按钮' : '已保存，两端共用';
      root.querySelector('#settingsSave').disabled = !dirty() || anyBusy();
      window.XiaoyuSettings.editingAppearance = JSON.stringify(original.appearance) !== JSON.stringify(draft.appearance);
      const appearanceChanged = window.XiaoyuSettings.editingAppearance;
      root.querySelector('#themePreviewStatus').textContent = (appearanceChanged ? '正在预览：' : '当前外观：') + window.XiaoyuThemes.resolve(draft.appearance.theme).name + ' · ' + window.XiaoyuThemes.colorModes[draft.appearance.color_mode || 'light'] + ' · ' + window.XiaoyuThemes.nightPalettes[draft.appearance.night_palette || 'charcoal'] + (appearanceChanged ? '，保存后两端同步。' : '');
      root.querySelector('#themeRevert').disabled = !appearanceChanged || busy;
      root.querySelector('#settingsKeySave').disabled = !model?.can_replace_key || anyBusy() || anyDirty();
      root.querySelector('#settingsDrawingKeySave').disabled = anyBusy() || anyDirty();
    }
    const guard = () => !anyBusy() && (!anyDirty() || window.confirm('设置尚未保存，要放弃这些修改吗？'));
    app.leave = guard;
    const unload = event => { if (anyDirty() || anyBusy()) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', unload);
    onDispose(() => { disposed = true; douyinSettings?.dispose(); window.XiaoyuSettings.editingAppearance = false; window.removeEventListener('beforeunload', unload); applyAppearance(original.appearance, true); });
    const syncChanged = event => {
      if (disposed || anyBusy() || event.detail.settings_revision === data.revision) return;
      if (anyDirty()) {
        state.textContent = '另一端已更新设置，当前草稿保留；请重新加载核对后再保存。';
      } else {
        const tab = root.querySelector('[data-settings-tab].active')?.dataset.settingsTab || initialTab;
        app.leave = null;
        if (location.hash !== '#/settings?tab=' + tab) location.hash = '#/settings?tab=' + tab;
        else route();
      }
    };
    window.addEventListener('xiaoyu:sync', syncChanged);
    onDispose(() => window.removeEventListener('xiaoyu:sync', syncChanged));
    window.XiaoyuRoom.recovery(root.querySelector('#roomRecovery'));
    window.XiaoyuRoom.sync();
    function showTab(tab) {
      root.querySelectorAll('[data-settings-tab]').forEach(button => {
        const active = button.dataset.settingsTab === tab;
        button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active));
        button.tabIndex = active ? 0 : -1;
      });
      root.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== tab; });
    }
    root.querySelectorAll('[data-settings-tab]').forEach(button => {
      button.type = 'button'; button.onclick = () => showTab(button.dataset.settingsTab);
      button.onkeydown = event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); const buttons = [...root.querySelectorAll('[data-settings-tab]')];
        const index = buttons.indexOf(button), next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
        buttons[next].click(); buttons[next].focus();
      };
    });
    showTab(initialTab);
    douyinSettings = window.XiaoyuDouyin.mountModelSettings(root.querySelector('#settingsDouyin'), {
      notify: mark, canSave: () => !busy && !dirty()
    });
    if (initialTab === 'models' && q.get('section') === 'douyin') root.querySelector('#settingsDouyinCard').scrollIntoView({block:'start'});
    root.querySelector('#themeRevert').onclick = () => {
      draft.appearance = clone(original.appearance);
      root.querySelectorAll('[data-setting^="appearance."]').forEach(input => {
        const value = draft.appearance[input.dataset.setting.split('.')[1]];
        if (input.type === 'radio') input.checked = input.value === value;
        else if (input.type === 'checkbox') input.checked = value;
        else input.value = value;
      });
      applyAppearance(draft.appearance); mark();
    };
    form.addEventListener('input', event => {
      const element = event.target;
      if (element.dataset.setting) {
        const [section, key] = element.dataset.setting.split('.');
        draft[section][key] = element.type === 'checkbox' ? element.checked : element.type === 'number' || key === 'scale' ? Number(element.value) : element.value;
      } else if (element.dataset.speed) draft.voice.speeds[element.dataset.speed] = Number(element.value);
      else return;
      applyAppearance(draft.appearance); mark();
    });
    form.onsubmit = async event => {
      event.preventDefault(); if (anyBusy() || !dirty() || !form.reportValidity()) return;
      const changes = {};
      for (const section of Object.keys(draft)) for (const key of Object.keys(draft[section])) {
        if (JSON.stringify(draft[section][key]) !== JSON.stringify(original[section][key])) (changes[section] ||= {})[key] = draft[section][key];
      }
      busy = true; form.inert = true; state.textContent = '正在备份并保存…';
      try {
        data = await request('', { revision: data.revision, changes });
        if (disposed) return;
        original = clone(data.values); draft = clone(original); applyAppearance(original.appearance, true);
        toast('设置已保存，网页与软件共用');
        readDrawing();
      } catch (error) { if (!disposed) { state.textContent = error.message; toast(error.message, 'bad'); } }
      finally { busy = false; if (!disposed) { form.inert = false; const message = state.textContent; mark(); if (dirty()) state.textContent = message; } }
    };
    root.querySelector('#settingsReload').onclick = async () => {
      if (!guard()) return;
      app.leave = null;
      route();
    };
    async function readModel() {
      try {
        model = await request('/model'); if (disposed) return;
        root.querySelector('#settingsModel').innerHTML = `<dl class="settings-model"><dt>供应商</dt><dd>${esc(model.provider)}</dd><dt>模型</dt><dd>${esc(model.model)}</dd><dt>连接地址</dt><dd>${esc(model.endpoint || '未配置')}</dd><dt>小鱼 Key</dt><dd>${model.configured ? '••••••••（已配置）' : '未配置'}</dd><dt>用途</dt><dd>${esc(model.purpose)}</dd></dl>`;
        mark();
      } catch (error) { if (!disposed) root.querySelector('#settingsModel').textContent = error.message; }
    }
    root.querySelector('#settingsModelRefresh').onclick = readModel;
    async function readDrawing() {
      try {
        const info = await request('/drawing'); if (disposed) return;
        root.querySelector('#settingsDrawingStatus').textContent = (info.reason || '画室状态暂不可用') +
          (info.used_today !== undefined ? ` · 今日 ${info.used_today}/${info.daily_limit} 次` : '') +
          (info.active ? ` · 正在处理 ${info.active} 项` : '');
      } catch (error) { if (!disposed) root.querySelector('#settingsDrawingStatus').textContent = error.message; }
      try {
        const result = await request('/drawing/gallery'); if (disposed) return;
        const labels = { generating: '正在画', ready: '已画好，未发送', sending: '正在发图', sent: '已发送', failed: '绘画失败', uncertain: '结果待核对' };
        root.querySelector('#settingsDrawingGallery').innerHTML = (result.items || []).map(item =>
          `<article class="drawing-work">${item.has_image ? `<a href="/api/settings/drawing/image?id=${encodeURIComponent(item.id)}" target="_blank" rel="noopener"><img src="/api/settings/drawing/image?id=${encodeURIComponent(item.id)}" alt="${esc(item.title)}" loading="lazy"></a>` : '<div class="drawing-placeholder">画纸还在等小鱼</div>'}<div><strong>${esc(item.title)}</strong><span class="tiny">${esc(labels[item.status] || item.status)} · ${esc(new Date(item.created_at).toLocaleString())}</span><p>${esc(item.description)}</p>${item.share_summary && (item.share_summary.destinations || item.share_summary.unsent || item.share_summary.uncertain) ? `<p class="tiny">原图分享：已送达 ${Number(item.share_summary.destinations) || 0} 个聊天${item.share_summary.unsent ? ` · 尚未送达 ${Number(item.share_summary.unsent)}` : ''}${item.share_summary.uncertain ? ` · 结果待核对 ${Number(item.share_summary.uncertain)}` : ''}</p>` : ''}${item.error ? `<p class="tiny">${esc(item.error)}</p>` : ''}</div></article>`
        ).join('') || '<p class="muted">画册还是空的。等你们的第一幅画。</p>';
      } catch (error) { if (!disposed) root.querySelector('#settingsDrawingGallery').textContent = error.message; }
    }
    root.querySelector('#settingsDrawingRefresh').onclick = readDrawing;
    root.querySelector('#settingsDrawingKeySave').onclick = async () => {
      if (anyBusy() || anyDirty()) return;
      const field = root.querySelector('#settingsDrawingKey'), output = root.querySelector('#settingsDrawingKeyResult');
      if (!field.value.trim()) { output.textContent = '请先填写绘画 Key。'; return; }
      busy = true; form.inert = true;
      try {
        const result = await request('/drawing-key', { key: field.value.trim(), revision: data.revision });
        field.value = ''; data.revision = result.revision;
        original.drawing.key_mode = draft.drawing.key_mode = 'separate';
        root.querySelector('[data-setting="drawing.key_mode"]').value = 'separate';
        output.textContent = result.message; await readDrawing();
      } catch (error) { output.textContent = error.message; }
      finally { busy = false; if (!disposed) { form.inert = false; mark(); } }
    };
    root.querySelector('#settingsKeySave').onclick = async () => {
      const field = root.querySelector('#settingsKey'), output = root.querySelector('#settingsKeyResult');
      if (anyBusy() || anyDirty() || !field.value.trim()) { output.textContent = '请先保存其他修改，再填写新的 Key。'; return; }
      busy = true; form.inert = true; output.textContent = '正在备份并保存专用 Key…';
      try {
        const result = await request('/key', { key: field.value.trim(), revision: data.revision });
        field.value = ''; data.revision = result.revision; output.textContent = result.message; await readModel();
      } catch (error) { output.textContent = error.message; }
      finally { busy = false; form.inert = false; mark(); }
    };
    function backupStatus(status) {
      if (disposed) return;
      root.querySelector('#settingsBackupStatus').innerHTML = `<p>${status.running ? '备份进行中…' : status.error ? esc(status.error) : status.last_success ? '最近成功：' + esc(status.last_success.replace('T', ' ')) : '尚未通过设置页创建完整备份。'}</p>${status.path ? '<p class="settings-path">' + esc(status.path) + '</p>' : ''}${status.verified ? '<p class="tiny">最近成功备份已通过文件哈希与数据库完整性校验。</p>' : ''}`;
      root.querySelector('#settingsBackupNow').disabled = !!status.running;
    }
    backupStatus(data.backup_status);
    root.querySelector('#settingsBackupNow').onclick = async () => {
      try { backupStatus(await request('/backup', {})); } catch (error) { toast(error.message, 'bad'); }
    };
    addTimer(async () => { try { backupStatus(await request('/backup')); } catch (_) {} }, 3000);
    for (const [id, path, filename] of [['settingsExport', '/export', '小鱼之家-设置.json'], ['settingsDiagnostics', '/diagnostics', '小鱼之家-诊断摘要.json']]) {
      root.querySelector('#' + id).onclick = async () => { try { download(filename, await request(path)); } catch (error) { toast(error.message, 'bad'); } };
    }
    try {
      const cost = await api('/api/cost?range=today');
      if (!disposed) root.querySelector('#settingsBudgetStatus').textContent = '今日已记录花费：¥' + fmtCost(cost.total) + '。供应商后台为最终账单依据。';
    } catch (_) { if (!disposed) root.querySelector('#settingsBudgetStatus').textContent = '花费记录暂不可用，可稍后查看花费页。'; }
    readModel(); readDrawing(); mark();
  }
  async function budgetNotice() {
    if (document.hidden) return;
    try {
      const settings = await request(''), limit = settings.values.budget.daily_warning;
      if (!(limit > 0)) { document.querySelector('#settingsBudgetNotice')?.remove(); return; }
      const cost = await api('/api/cost?range=today');
      if (cost.total >= limit) {
        let banner = document.querySelector('#settingsBudgetNotice');
        if (!banner) { banner = document.createElement('div'); banner.id = 'settingsBudgetNotice'; banner.className = 'settings-budget-notice'; banner.setAttribute('role', 'status'); document.querySelector('.main').prepend(banner); }
        banner.textContent = '今日已记录花费 ¥' + fmtCost(cost.total) + '，达到提醒金额 ¥' + fmtCost(limit) + '。';
      } else document.querySelector('#settingsBudgetNotice')?.remove();
    } catch (_) {}
  }
  window.XiaoyuSettings = { mount, applyAppearance, syncAppearance, editingAppearance: false,
    start() { syncAppearance(); budgetNotice(); setInterval(syncAppearance, 15000); setInterval(budgetNotice, 60000); window.addEventListener('resize', () => syncAppearance()); } };
})();
