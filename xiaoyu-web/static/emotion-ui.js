/* Shared fictional character dashboard for browser and embedded desktop. */
'use strict';

async function viewEmotionDashboard(root, q) {
  const st = ui.emotion;
  const tabs = [['overview', '情绪总览'], ['character', '角色状态'], ['daily', '每日变化']];
  const requested = q.get('tab') || st.tab;
  const tab = tabs.some(item => item[0] === requested) ? requested : 'overview';
  const selectedDay = q.get('date') || st.date || todayStr();
  st.tab = tab;
  st.date = /^\d{4}-\d{2}-\d{2}$/.test(selectedDay) ? selectedDay : todayStr();
  const token = app.token;
  let requestId = 0;
  let data = null;
  const colors = ['#527ba9', '#34826e', '#bd5964', '#8773ad', '#b26c91'];
  const visible = {joy: true, happiness: true, sadness: true, inferiority: true, shyness: true};
  const signed = value => (num(value) > 0 ? '+' : '') + num(value).toFixed(2);
  const line = (label, value, color, baseline) => {
    const val = clamp(value, 0, 100);
    return '<div class="emotion-meter-row"><span>' + esc(label) + '</span>' +
      '<div class="emotion-meter" role="meter" aria-label="' + esc(label) + '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + val + '">' +
      '<i style="width:' + val + '%;background:' + color + '"></i>' +
      (baseline === undefined ? '' : '<b title="基线 ' + num(baseline).toFixed(1) + '" style="left:' + clamp(baseline, 0, 100) + '%"></b>') + '</div>' +
      '<strong>' + val.toFixed(1) + '</strong></div>';
  };
  const summary = (label, value, note, kind) => '<div class="card emotion-summary ' + kind + '">' +
    '<div class="emotion-summary-label">' + esc(label) + '</div><div class="emotion-summary-value">' + esc(value) + '</div>' +
    '<div class="tiny muted">' + esc(note) + '</div></div>';
  const navigate = patch => {
    const query = new URLSearchParams({tab: st.tab, date: st.date});
    Object.entries(patch).forEach(([key, value]) => query.set(key, value));
    location.hash = '#/emotion?' + query.toString();
  };

  root.innerHTML = pageHead('情绪', '她的心情，和每天的小小变化') +
    '<div class="emotion-toolbar"><div class="section-tabs" id="eTabs" role="tablist" aria-label="情绪分区">' +
    tabs.map(([key, name]) => '<button type="button" role="tab" data-tab="' + key + '" aria-selected="' + (key === tab) +
      '" class="' + (key === tab ? 'active' : '') + '">' + name + '</button>').join('') + '</div>' +
    '<button type="button" class="btn sm ghost" id="eRefresh">刷新状态</button></div>' +
    '<div class="emotion-update tiny muted" id="eUpdated">正在读取她的状态…</div>' +
    '<section id="eOverview" ' + (tab !== 'overview' ? 'hidden' : '') + '><div id="eSummary" class="emotion-summaries"></div>' +
      '<div class="emotion-columns"><div id="eEmotionDetail"></div><div id="eSupportDetail"></div></div></section>' +
    '<section id="eCharacterPanel" ' + (tab !== 'character' ? 'hidden' : '') + '><div class="card" id="eTop"></div>' +
      '<div class="card" id="eCharacter"></div></section>' +
    '<section id="eDailyPanel" ' + (tab !== 'daily' ? 'hidden' : '') + '><div class="card">' +
      '<div class="card-title">每日变化<span class="title-extra">各项统一为 0–100 点</span></div>' +
      '<div class="emotion-day-controls"><button type="button" class="btn sm ghost" id="ePrevDay">前一天</button>' +
      '<input type="date" id="eDay" aria-label="每日变化日期" value="' + esc(st.date) + '">' +
      '<button type="button" class="btn sm ghost" id="eNextDay">后一天</button>' +
      '<button type="button" class="btn sm ghost" id="eToday">今天</button>' +
      '<select id="eDailyHistory" aria-label="已有记录日期"><option value="">选择有记录的日期</option></select></div>' +
      '<div id="eDailyBody">' + skeleton(4) + '</div></div></section>' +
    '<div class="card" id="eTrendCard"><div class="card-title">情绪历史<span class="title-extra" id="eCount"></span></div>' +
      '<div class="row emotion-history-controls"><div class="seg" id="eHistoryMode">' +
        '<button type="button" data-mode="recent">最近快照</button><button type="button" data-mode="daily">近 30 天均值</button></div></div>' +
      '<canvas class="chart-canvas" id="eChart"></canvas><div class="legend" id="eLegend"></div></div>' +
    '<div class="card" id="eEvents"></div>';

  $('#eTabs', root).onclick = event => {
    const button = event.target.closest('[data-tab]');
    if (button) navigate({tab: button.dataset.tab});
  };
  const changeDay = offset => {
    const day = new Date(st.date + 'T12:00:00');
    day.setDate(day.getDate() + offset);
    navigate({date: day.getFullYear() + '-' + pad2(day.getMonth()+1) + '-' + pad2(day.getDate())});
  };
  $('#eDay', root).onchange = event => { if (event.target.value) navigate({date: event.target.value}); };
  $('#ePrevDay', root).onclick = () => changeDay(-1);
  $('#eNextDay', root).onclick = () => changeDay(1);
  $('#eToday', root).onclick = () => navigate({date: todayStr()});
  $('#eDailyHistory', root).onchange = event => { if (event.target.value) navigate({date: event.target.value}); };
  $('#eRefresh', root).onclick = () => load(true);
  $('#eHistoryMode', root).onclick = event => {
    const button = event.target.closest('[data-mode]');
    if (button) { st.history = button.dataset.mode; redraw(); }
  };

  function renderCurrent(cur, character) {
    $('#eTop', root).innerHTML = '<div class="card-title">当前情绪<span class="title-extra">' + esc(shortTs(cur && cur.t)) + '</span></div>' +
      (cur ? '<div class="mood-capsule"><span class="dot"></span>主导 ' + esc(cur.dominant_label || '—') + '</div>' +
        '<div class="row emotion-pills">' + (cur.values || []).map(v => pill(v.label + ' ' + num(v.value).toFixed(1), 'dim')).join('') + '</div>' :
        emptyBox('状态暂不可用', '核心连接恢复后刷新即可'));
    const state = character.state || {};
    $('#eCharacter', root).innerHTML = '<div class="card-title">小鱼的状态</div>' +
      (character.block ? '<div class="emotion-character-copy">' + esc(character.block) + '</div><div class="row emotion-pills">' +
        pill(state.rest_anticipation ? '准备休息' : '日常活动', 'primary') + pill('互动余波 ' + (state.seeds || []).length + ' 项') + '</div>' +
        '<p class="tiny muted">这里与对话使用同一份角色状态，互动余波会随时间消退。</p>' : emptyBox('状态暂不可用', character.note || '请稍后刷新'));
  }

  function renderOverview(d) {
    const dash = d.dashboard || {}, emo = dash.emotion || {}, needs = dash.needs || {}, params = dash.parameters || {};
    const cur = d.current, values = emo.values || {}, baselines = emo.baseline || {};
    const traits = params.personality || {}, shifts = params.deltas || {}, hormones = params.hormones || {};
    const state = (d.character || {}).state || {};
    if (!Object.keys(values).length) {
      $('#eSummary', root).innerHTML = '';
      $('#eEmotionDetail', root).innerHTML = '<div class="card">' + emptyBox('详细状态暂不可用', '核心连接恢复后刷新即可；已有历史记录继续保留。') + '</div>';
      $('#eSupportDetail', root).innerHTML = '';
      return;
    }
    const maxTrait = Object.keys(traits).sort((a,b) => num(traits[b])-num(traits[a]))[0];
    const traitNames = {extraversion:'外向', neuroticism:'敏感', agreeableness:'亲和'};
    const toneNames = {lively:'轻快活泼', low:'安静低沉', shy_verbose:'害羞又想说话', shy_short:'有些不好意思', normal:'自然平稳'};
    const pol = dash.policy || {};
    const expression = (d.character || {}).expression || {};
    $('#eSummary', root).innerHTML = summary('当前情绪', (cur || {}).dominant_label || '—', '当前强度最高的一项', 'emotion-summary-mood') +
      summary('日常状态', needs.label || '平静', '能量 ' + (num((needs.values || {}).energy)*100).toFixed(0) + ' · 社交 ' + (num((needs.values || {}).social_connection)*100).toFixed(0), '') +
      summary('表达倾向', expression.label || toneNames[pol.tone] || '自然平稳', expression.reason || pol.tone_hint || '随当前状态调整', '') +
      summary('动态性格', traitNames[maxTrait] || '—', '外向 ' + num(traits.extraversion).toFixed(2) + ' · 亲和 ' + num(traits.agreeableness).toFixed(2), '');
    let gates = (dash.memory_gates || []).map(g => '<div class="emotion-gate">' + pill(g.category, g.open ? 'ok' : '') +
      '<span class="tiny muted">' + num(g.value).toFixed(0) + ' / ' + num(g.threshold).toFixed(0) + ' · ' + (g.open ? '可唤起' : '未达阈值') + '</span></div>').join('');
    const shy = state.last_shyness_reaction;
    $('#eEmotionDetail', root).innerHTML = '<div class="card"><div class="card-title">五维情绪<span class="title-extra">0–100</span></div>' +
      (d.dims || []).map((dim,i) => line(dim.label, values[dim.key], colors[i], baselines[dim.key]) +
        '<div class="emotion-baseline tiny muted">基线 ' + num(baselines[dim.key]).toFixed(1) + ' · 相对基线 ' + signed(num(values[dim.key])-num(baselines[dim.key])) + '</div>').join('') +
      '<p class="tiny muted emotion-footnote">每一项是独立强度，不是加起来等于 100% 的占比。细线表示各自基线。</p>' +
      (shy && shy.t ? '<div class="emotion-reaction">最近一次害羞反应 <strong>' + signed(shy.delta) + '</strong> 点 · ' + esc(shortTs(shy.t)) + '</div>' : '') +
      '<a class="emotion-inline-link" href="#/emotion?tab=daily&date=' + esc(st.date) + '">查看每天增加、减少与波动范围 →</a></div>' +
      '<div class="card"><div class="card-title">情绪与记忆唤起</div><div class="emotion-gates">' + gates + '</div>' +
      '<p class="tiny muted emotion-footnote">显示当前情绪是否达到相应记忆分类的唤起阈值。</p></div>';
    const needsDef = [['energy','能量','#bc9858'],['social_connection','社交连接','#5d8db8'],['cognitive_novelty','新鲜感','#739c85']];
    const parameterDef = [['dopamine','多巴胺','#b77d9a'],['cortisol','皮质醇','#bd777f'],['oxytocin','催产素','#6a9eae'],['serotonin','血清素','#6b9d89']];
    $('#eSupportDetail', root).innerHTML = '<div class="card"><div class="card-title">日常状态<span class="title-extra">原版内稳态</span></div>' +
      needsDef.map(([key,label,color]) => line(label,num((needs.values || {})[key])*100,color,num((needs.setpoints || {})[key])*100)).join('') +
      '<p class="tiny muted emotion-footnote">疲劳 ' + num((needs.motives || {}).fatigue).toFixed(2) + ' · 孤独 ' + num((needs.motives || {}).loneliness).toFixed(2) +
        ' · 无聊 ' + num((needs.motives || {}).boredom).toFixed(2) + '</p></div>' +
      '<div class="card"><div class="card-title">角色参数<span class="title-extra">沿用原版四项</span></div>' +
      parameterDef.map(([key,label,color]) => hormones[key] ? line(label,num(hormones[key].value)*100,color,num(hormones[key].baseline)*100) : '').join('') +
      '<p class="tiny muted emotion-footnote">用于小鱼的虚构角色表现。' + (params.fused ? '当前已进入缓和状态。' : '') + '</p></div>' +
      '<div class="card"><div class="card-title">动态性格<span class="title-extra">相对初始设定</span></div>' +
      ['extraversion','neuroticism','agreeableness'].map(key => '<div class="emotion-trait"><span>' + traitNames[key] + '</span><strong>' + num(traits[key]).toFixed(2) +
        '</strong><span class="tiny muted">' + signed(shifts[key]) + '</span></div>').join('') + '</div>';
  }

  function renderDaily(daily) {
    const history = $('#eDailyHistory', root);
    history.innerHTML = '<option value="">选择有记录的日期</option>' + (daily.available_dates || []).slice().reverse().map(day =>
      '<option value="' + esc(day) + '" ' + (day === st.date ? 'selected' : '') + '>' + esc(day) + '</option>').join('');
    if (!daily.items) {
      $('#eDailyBody', root).innerHTML = emptyBox('每日记录暂不可用', '核心连接恢复后刷新即可'); return;
    }
    let group = '';
    const rows = daily.items.map(item => {
      let prefix = '';
      if (group !== item.group) { group = item.group; prefix = '<tr class="emotion-group-row"><th colspan="5">' + esc(group) + '</th></tr>'; }
      return prefix + '<tr><td>' + esc(item.label) + '</td>' + (item.available ?
        '<td class="num">' + num(item.open).toFixed(2) + ' → ' + num(item.close).toFixed(2) + '</td>' +
        '<td class="num emotion-net" data-delta="' + num(item.delta) + '">' + signed(item.delta) + '</td>' +
        '<td class="num">' + num(item.low).toFixed(2) + ' — ' + num(item.high).toFixed(2) + '</td>' +
        '<td class="num">' + num(item.swing).toFixed(2) + '</td>' : '<td colspan="4" class="muted">当天尚无记录</td>') + '</tr>';
    }).join('');
    $('#eDailyBody', root).innerHTML = '<p class="tiny muted">' + esc(daily.basis) + '</p><div class="table-wrap"><table class="tbl emotion-daily-table">' +
      '<thead><tr><th>项目</th><th>日初 → ' + (st.date === daily.today ? '当前' : '日末') + '</th><th>净变化</th><th>最低 — 最高</th><th>波动幅度</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div><p class="tiny muted emotion-footnote">' + esc(daily.sampling_note) + '</p>' +
      '<p class="tiny muted">五维情绪可使用已有历史快照。其他各项从 ' + esc(daily.started_at ? shortTs(daily.started_at) : '本次升级首次记录时') + ' 开始记录；空白日期不补造数据。</p>';
  }

  function renderEvents(events) {
    const labels = {praise:'被夸奖',intimacy:'友好亲近',cared:'被关心',ignored:'等待回应',insult:'不愉快的话',abandoned:'分别',request_met:'愿望得到回应',request_denied:'请求未获同意',scale:'情绪触发'};
    const dimLabels = {joy:'愉悦',happiness:'幸福',sadness:'难过',inferiority:'自卑',shyness:'害羞'};
    $('#eEvents', root).innerHTML = '<div class="card-title">最近互动记录<span class="title-extra">原版奖罚记录</span></div>' +
      (events.length ? '<div class="table-wrap"><table class="tbl"><thead><tr><th>时间</th><th>触发</th><th>加权变化</th></tr></thead><tbody>' +
        events.map(event => '<tr><td>' + esc(shortTs(event.t)) + '</td><td>' + esc(labels[event.trigger] || '其他互动') + '</td><td>' +
          esc(Object.entries(event.delta || {}).filter(([,v])=>num(v)!==0).map(([key,value]) => (dimLabels[key] || '情绪') + ' ' + signed(num(value)*num(event.rpe_weight,1))).join(' · ')) + '</td></tr>').join('') +
        '</tbody></table></div>' : emptyBox('还没有互动记录', '实际发生的记录会保留在这里'));
  }

  function redraw() {
    if (!data || !root.isConnected || token !== app.token) return;
    const daily = st.history === 'daily';
    const history = daily ? data.daily_trend || {} : data;
    $('#eCount', root).textContent = daily ? num(history.count) + ' 个有记录的日期' : num(data.count) + ' 个快照';
    $$('#eHistoryMode button', root).forEach(button => button.classList.toggle('active', button.dataset.mode === st.history));
    drawLines($('#eChart', root), {series:history.series || [],times:history.times || [],dims:data.dims || [],colors,visible});
  }

  async function load(manual = false) {
    const id = ++requestId;
    const button = $('#eRefresh', root);
    if (manual) { button.disabled = true; button.textContent = '读取中…'; }
    try {
      const result = await api('/api/emotion?limit=160&date=' + encodeURIComponent(st.date));
      if (id !== requestId || token !== app.token || !root.isConnected) return;
      data = result;
      renderCurrent(data.current, data.character || {});
      renderOverview(data);
      renderDaily(data.daily || {});
      renderEvents((data.dashboard || {}).events || []);
      $('#eUpdated', root).textContent = (data.current && data.current.historical ? '核心暂不可用 · 当前为历史快照 ' : '状态更新 ') + shortTs((data.current || {}).t) + ' · 每 15 秒刷新';
      const legend = $('#eLegend', root);
      legend.innerHTML = (data.dims || []).map((dim,i) => '<button type="button" class="lg ' + (visible[dim.key] ? '' : 'off') + '" data-key="' + esc(dim.key) +
        '" aria-pressed="' + visible[dim.key] + '"><i style="background:' + colors[i] + '"></i>' + esc(dim.label) + '</button>').join('');
      legend.onclick = event => {
        const item = event.target.closest('[data-key]');
        if (!item) return;
        visible[item.dataset.key] = !visible[item.dataset.key];
        item.classList.toggle('off', !visible[item.dataset.key]);
        item.setAttribute('aria-pressed', String(visible[item.dataset.key]));
        redraw();
      };
      redraw();
    } catch (error) {
      if (id !== requestId || token !== app.token) return;
      $('#eUpdated', root).textContent = '状态读取失败：' + error.message;
      $('#eDailyBody', root).innerHTML = emptyBox('读取失败', error.message);
      if (!data) $('#eSummary', root).innerHTML = errorCard(error);
    } finally {
      if (token === app.token && id === requestId) { button.disabled = false; button.textContent = '刷新状态'; }
    }
  }
  onWindowResize(redraw);
  await load();
  if (token === app.token) addTimer(() => load(), 15000);
}
