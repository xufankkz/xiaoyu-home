/* Daily contact impressions, shared by browser and desktop. Reads never score. */
'use strict';

async function viewAffinityCard(host) {
  const token = app.token;
  let requestId = 0;
  let day = ui.contacts.affinityDate || '';
  const signed = value => (value > 0 ? '+' : '') + Number(value || 0).toFixed(1);
  async function load(selected) {
    const request = ++requestId;
    host.innerHTML = '<div class="card" id="cAffinity">' + skeleton(2) + '</div>';
    try {
      const data = await api('/api/contacts/affinity' + (selected ? '?date=' + encodeURIComponent(selected) : ''));
      if (app.token !== token || request !== requestId || !host.isConnected) return;
      if (data.offline || data.ok === false) {
        host.innerHTML = '<div class="card" id="cAffinity"><div class="card-title">每日相处 · 好感度排行</div>' +
          emptyBox('暂时连不上小鱼的记录', '稍后刷新即可，上次保存的结果仍会保留。') + '</div>';
        return;
      }
      day = selected || '';
      ui.contacts.affinityDate = day;
      const latest = (data.runs || [])[0];
      const warning = latest && latest.status !== 'done'
        ? '<div class="affinity-notice">' + esc(latest.day) + ' 的总结' +
          (latest.status === 'running' ? '正在处理；页面不会额外发起总结。' : '未完成；继续保留上次结果，今天不会重复调用。') + '</div>' : '';
      const labels = {updated: '当日已回顾', no_activity: '当天无新互动', insufficient: '当日依据不足', pending: '等待每日回顾'};
      const rows = (data.rows || []).map(row => '<tr>' +
        '<td class="affinity-rank">' + (row.known ? esc(row.rank) : '—') + '</td>' +
        '<td><strong>' + esc(row.alias || '未设置称呼') + '</strong><div class="tiny muted">' + esc(labels[row.status] || '已保存') + '</div></td>' +
        '<td><div class="affinity-score">' + (row.known ? '<strong>' + Number(row.score).toFixed(1) + '</strong><span class="tiny muted"> / 100</span>' : '<span class="muted">暂未评分</span>') + '</div>' +
          (row.known ? '<div class="affinity-meter" aria-label="好感度 ' + Number(row.score).toFixed(1) + '"><i style="width:' + clamp(row.score, 0, 100) + '%"></i></div>' : '') + '</td>' +
        '<td class="affinity-delta ' + (row.delta > 0 ? 'positive' : row.delta < 0 ? 'negative' : '') + '">' + (row.known ? signed(row.delta) : '—') + '</td>' +
        '<td class="affinity-impression">' + esc(row.summary || '等待每日回顾，原有关系照旧。') +
          (row.dimensions && Object.keys(row.dimensions).length ? '<details><summary>相处维度</summary><div class="tiny muted">' +
            [['care','关心'],['respect','尊重'],['understanding','倾听理解'],['enjoyment','轻松愉快']].map(([key,label]) => esc(label) + ' ' + esc(row.dimensions[key] ?? '—')).join(' · ') + '</div></details>' : '') +
          (row.explanation ? '<details class="affinity-explanation"><summary>变化说明与依据</summary><div class="tiny muted">' +
            (row.known ? esc(row.explanation.before) + ' → ' + esc(row.explanation.after) + ' 分。' : '') + esc(row.explanation.reason) + '</div>' +
            (row.explanation.evidence || []).map(e => '<blockquote><time class="tiny muted">' + esc(shortTs(e.at)) + '</time><p>' + esc(e.text) + '</p></blockquote>').join('') +
            (row.explanation.unavailable ? '<p class="tiny muted">部分原始依据已删除或不可读取，不再展示原文。</p>' : '') + '</details>' : '') + '</td></tr>').join('');
      host.innerHTML = '<section class="card" id="cAffinity">' +
        '<div class="card-title">每日相处 · 好感度排行<span class="title-extra">' + pill('每日一次', 'primary') + '</span></div>' +
        '<div class="affinity-toolbar"><span class="muted">回顾日期</span><input type="date" id="aDate" aria-label="相处回顾日期" value="' + esc(selected || data.date || '') + '">' +
        '<button class="btn" id="aLatest">最新结果</button><button class="btn ghost" id="aRefresh">刷新记录</button></div>' +
        '<p class="tiny muted">每天北京时间 00:10 统一回顾前一天，聊天时读取已保存的相处印象。</p>' + warning +
        (!data.has_summary ? '<div class="affinity-notice">' + (selected ? '这一天还没有保存的回顾。' : '第一份回顾会在每日总结后出现，先让小鱼慢慢感受大家的关心。') + '</div>' : '') +
        '<div class="table-wrap"><table class="tbl affinity-table"><thead><tr><th>排名</th><th>联系人</th><th>好感度</th><th>当日变化</th><th>小鱼的相处印象</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
        '<div class="affinity-footer tiny muted"><span>关心、尊重、倾听和相处感受共同影响；不按消息数量累加。无互动不扣分，依据不足不比较。</span>' +
        '<span>下一次：' + esc((data.next_run || '').slice(0,16).replace('T',' ')) + ' 北京时间</span></div></section>';
      host.querySelector('#aDate').onchange = event => load(event.target.value);
      host.querySelector('#aLatest').onclick = () => load('');
      host.querySelector('#aRefresh').onclick = () => load(day);
    } catch (error) {
      if (app.token === token && request === requestId && host.isConnected) host.innerHTML = errorCard(error);
    }
  }
  await load(day);
}
