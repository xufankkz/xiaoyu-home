/* Shared product controls. All writes are explicit user actions. */
(function () {
  'use strict';
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Xiaoyu-Settings': '1' },
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!result.ok || result.data?.ok === false) throw new Error(result.error || result.data?.error || result.data?.note || '操作未完成');
    return result.data;
  }
  const note = text => '<p class="tiny muted">' + esc(text) + '</p>';
  const active = (host, token) => host.isConnected && app.token === token;
  const operation = () => window.crypto?.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);

  async function memory(host, record, reopen) {
    const token = app.token;
    let data;
    try { data = await request('/api/memory/editorial?id=' + encodeURIComponent(record.id)); }
    catch (error) { if (active(host, token)) host.innerHTML = note(error.message); return; }
    if (!active(host, token)) return;
    host.innerHTML = '<div class="room-divider"></div><div class="room-actions"><strong>整理这段记忆</strong>' +
      (data.pinned ? pill('固定保留', 'primary') : '') + '</div>' +
      note('更正会建立新记录并保留原文；补充会单独保存。固定保留可避免自动遗忘，原有有效期仍然适用。') +
      (record.supersedes ? '<button type="button" class="btn sm" data-original="' + esc(record.supersedes) + '">查看更正前的原始记忆</button>' : '') +
      (data.editable ? '<div class="room-actions"><button type="button" class="btn" data-pin>' + (data.pinned ? '取消固定保留' : '固定保留') + '</button>' +
      '<select aria-label="记忆整理方式" data-action><option value="supplement">补充说明</option><option value="correct">更正内容</option></select></div>' +
      '<label class="room-input"><span>新的内容</span><textarea rows="4" maxlength="12000" data-content placeholder="填写补充说明；选择更正时，请填写完整的正确内容。"></textarea></label>' +
      '<div class="room-actions"><button type="button" class="btn primary" data-save>保存整理</button><span role="status" data-result></span></div>' :
      note('这是一条历史或已到期记录。请打开当前有效记录进行整理。')) +
      (data.related?.length ? '<details><summary>更正与补充记录 · ' + data.related.length + '</summary>' + data.related.map(r =>
        '<button type="button" class="room-source" data-related="' + esc(r.id) + '"><span>' + esc(r.action === 'supplement' ? '补充' : '更正') + ' · ' + esc(shortTs(r.t_created)) + '</span><strong>' + esc(truncate(r.content, 120)) + '</strong></button>').join('') + '</details>' : '');
    host.querySelectorAll('[data-related]').forEach(b => { b.onclick = () => reopen(b.dataset.related); });
    host.querySelector('[data-original]')?.addEventListener('click', e => reopen(e.currentTarget.dataset.original));
    if (!data.editable) return;
    const field = host.querySelector('[data-content]'), choice = host.querySelector('[data-action]');
    const result = host.querySelector('[data-result]');
    let pending = null, busy = false;
    field.oninput = choice.onchange = () => { pending = null; };
    async function save(action) {
      if (busy) return;
      const content = field.value.trim();
      if (action !== 'pin' && !content) { result.textContent = '请先填写内容。'; return; }
      const fingerprint = JSON.stringify([action, content, !data.pinned]);
      if (!pending || pending.fingerprint !== fingerprint) pending = { id: operation(), fingerprint };
      busy = true; host.querySelectorAll('button,select,textarea').forEach(e => e.disabled = true);
      result.textContent = '正在保存并校验备份…';
      try {
        const saved = await request('/api/memory/editorial', { id: record.id, revision: data.revision,
          action, content, pinned: !data.pinned, operation: pending.id });
        if (!active(host, token)) return;
        toast('记忆已保存，原文和来源保留');
        window.dispatchEvent(new CustomEvent('xiaoyu:memory-changed'));
        reopen(saved.id || record.id);
      } catch (error) {
        if (active(host, token)) result.textContent = error.message + '；如另一端已经修改，请关闭后重新打开。';
      } finally {
        busy = false; if (active(host, token)) host.querySelectorAll('button,select,textarea').forEach(e => e.disabled = false);
      }
    }
    host.querySelector('[data-save]').onclick = () => save(choice.value);
    host.querySelector('[data-pin]').onclick = () => save('pin');
  }

  async function contacts(host, roster) {
    const token = app.token;
    let data, topics;
    try { [data, topics] = await Promise.all([request('/api/contacts/preferences'), request('/api/contacts/topics').catch(() => null)]); }
    catch (error) { if (active(host, token)) host.innerHTML = errorCard(error); return; }
    if (!active(host, token)) return;
    const people = roster.private || [];
    if (!people.length) { host.innerHTML = ''; return; }
    let key = 'private:' + people[0].qq, dirty = false, busy = false, disposed = false;
    const previousGuard = app.leave;
    const guard = () => !busy && (!dirty || window.confirm('联系人偏好尚未保存，要放弃这些修改吗？'));
    app.leave = guard;
    const unload = event => { if (dirty || busy) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', unload);
    onDispose(() => { disposed = true; window.removeEventListener('beforeunload', unload); if (app.leave === guard) app.leave = previousGuard; });
    const options = (rows, selected) => rows.map(([v, label]) => '<option value="' + esc(v) + '"' + (String(v) === String(selected ?? '') ? ' selected' : '') + '>' + esc(label) + '</option>').join('');
    function render() {
      const p = data.contacts[key] || {}, c = topics?.conversations?.find(c => c.key === key);
      host.innerHTML = '<section class="card"><div class="card-title">每个人的相处习惯</div>' +
        note('留空表示沿用全局设置。这里只调整联系与表达习惯，既有称呼、辈分、白名单和音色继续使用。') +
        '<label class="room-input"><span>联系人</span><select data-person aria-label="联系人偏好">' + options(people.map(p => ['private:' + p.qq, p.alias || p.qq]), key) + '</select></label>' +
        '<form data-contact-form><div class="room-form-grid">' +
        '<label class="room-input"><span>主动联系</span><select name="proactive_enabled">' + options([['','沿用全局'],['true','允许'],['false','暂停主动联系']], p.proactive_enabled === undefined ? '' : String(p.proactive_enabled)) + '</select></label>' +
        '<label class="room-input"><span>每日主动联系次数</span><input name="daily_count" type="number" min="0" max="3" placeholder="沿用全局" value="' + esc(p.daily_count ?? '') + '"></label>' +
        '<label class="room-input"><span>联系开始时间</span><input name="start" type="time" value="' + esc(p.start || '') + '"></label>' +
        '<label class="room-input"><span>联系结束时间</span><input name="end" type="time" value="' + esc(p.end || '') + '"></label>' +
        '<label class="room-input"><span>语音偏好</span><select name="voice_mode">' + options([['inherit','沿用全局'],['text','优先文字'],['prefer','更喜欢语音']], p.voice_mode || 'inherit') + '</select></label>' +
        '<label class="room-input"><span>自动语音比例（%）</span><input name="voice_percent" type="number" min="0" max="100" placeholder="沿用偏好或全局" value="' + esc(p.voice_percent ?? '') + '"></label>' +
        '<label class="room-input"><span>暂缓主动联系到</span><input name="pause_until" type="datetime-local" value="' + esc(localInput(p.pause_until)) + '"></label></div>' +
        '<label class="room-input"><span>相处备注</span><textarea name="notes" maxlength="240" rows="2" placeholder="例如：工作日白天忙，喜欢先聊近况。">' + esc(p.notes || '') + '</textarea></label>' +
        '<div class="room-actions"><button class="btn primary" type="submit">保存此人偏好</button><button class="btn" type="button" data-inherit>全部沿用全局</button><span role="status" data-contact-status>已加载</span></div></form>' +
        '<div class="room-divider"></div><div class="card-title">上次那件事，后来呢？</div>' +
        note('从新消息里明确提到日期的事情中保留后续话题，放进原有主动联系中。收到结果、取消或“不用再问”就停止追问。') +
        (c?.temporaryTextUntil ? note('临时文字优先到 ' + new Date(c.temporaryTextUntil).toLocaleString()) : '') +
        (topics?.accountingHealthy === false ? note('话题记录暂不可写，现有记录保留。') : '') +
        (!topics ? note('话题记录暂时连接不上，稍后重新进入此页即可。') : '') +
        (c?.topics?.length ? c.topics.slice().reverse().slice(0, 12).map(t => '<div class="room-topic"><div><strong>' + esc(t.category) + '</strong> · ' + esc(t.date) + ' ' + pill(({open:'等待自然关心',resolved:'已有结果',closed:'已关闭',expired:'已到期',removed:'来源已移除'})[t.status] || '已保存') +
          '<p class="tiny muted">' + esc(t.status === 'removed' ? '来源已删除或更正，不再使用原内容。' : t.source) + '</p><span class="tiny muted">已关心 ' + esc(t.asked) + ' 次 · 来源：' + esc(shortTs(new Date(t.createdAt).toISOString())) + '</span></div>' +
          (t.status === 'open' ? '<button class="btn sm" type="button" data-close-topic="' + esc(t.id) + '">不再追问</button>' : '') + '</div>').join('') : note('目前还没有待关心的明确日期话题。')) + '</section>';
      const form = host.querySelector('[data-contact-form]'), status = host.querySelector('[data-contact-status]');
      form.oninput = () => { dirty = true; status.textContent = '尚未保存'; };
      host.querySelector('[data-person]').onchange = e => { if (!guard()) { e.target.value = key; return; } key = e.target.value; dirty = false; render(); };
      async function save(preferences) {
        if (busy) return;
        busy = true; form.inert = true; status.textContent = '正在备份并保存…';
        try {
          data = await request('/api/contacts/preferences', { key, revision: data.revision, preferences });
          if (!disposed) { dirty = false; render(); toast('联系人偏好已保存，两端共用'); }
        } catch (error) { if (!disposed) status.textContent = error.message + '；当前输入已保留。'; }
        finally { busy = false; if (form.isConnected) form.inert = false; }
      }
      form.onsubmit = event => {
        event.preventDefault(); if (!form.reportValidity()) return;
        const p = Object.fromEntries(new FormData(form));
        for (const k of ['daily_count','voice_percent']) if (p[k] !== '') p[k] = Number(p[k]);
        if (p.proactive_enabled !== '') p.proactive_enabled = p.proactive_enabled === 'true';
        if (p.pause_until) p.pause_until = new Date(p.pause_until).toISOString();
        save(p);
      };
      host.querySelector('[data-inherit]').onclick = () => save({});
      host.querySelectorAll('[data-close-topic]').forEach(button => { button.onclick = async () => {
        button.disabled = true;
        try { topics = await request('/api/contacts/topics', { action:'close', key, id:button.dataset.closeTopic }); if (!disposed) { if (!dirty) render(); else { button.textContent = '已关闭'; toast('话题已关闭，偏好草稿保留'); } } }
        catch (error) { if (!disposed) { toast(error.message, 'bad'); button.disabled = false; } }
      }; });
    }
    function localInput(value) {
      if (!value || !Number.isFinite(Date.parse(value))) return '';
      const d = new Date(value); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }
    const changed = async event => {
      if (!active(host, token) || event.detail.contacts_revision === data.revision) return;
      if (!dirty && !busy) {
        try { const fresh = await request('/api/contacts/preferences'); if (!dirty && !busy && active(host, token)) { data = fresh; render(); } }
        catch (_) { /* Next user visit can reload the saved values. */ }
      } else host.querySelector('[data-contact-status]').textContent = '另一端已更新联系人偏好；当前输入保留，请重新进入页面核对后保存。';
    };
    window.addEventListener('xiaoyu:sync', changed);
    onDispose(() => window.removeEventListener('xiaoyu:sync', changed));
    render();
  }

  async function recovery(host) {
    const token = app.token;
    let selected = '', busy = false;
    host.innerHTML = '<div class="card-title">备份对比与恢复预览</div>' + note('先查看差异，再将备份还原到独立副本检查。这里的核验不会覆盖正在使用的数据。') +
      '<div class="room-actions"><select data-backup aria-label="选择备份"></select><button type="button" class="btn" data-preview>查看差异</button><button type="button" class="btn" data-rehearse>核验独立副本</button></div><div data-recovery-result role="status"></div>';
    const result = host.querySelector('[data-recovery-result]'), select = host.querySelector('[data-backup]');
    function status(job) {
      busy = !!job?.running;
      host.querySelectorAll('button,select').forEach(b => b.disabled = busy || !select.options.length);
      if (busy) { result.innerHTML = note('正在校验备份' + (job.rehearse ? '并检查独立副本' : '与当前文件差异') + '，完成后会显示结果。'); return; }
      if (!job?.backup_id) return;
      if (!job.ok) { result.innerHTML = note(job.error); return; }
      result.innerHTML = '<p>已校验 ' + esc(job.verified_files) + ' 个文件。' + esc(job.note) + '</p>' +
        (job.staging ? note('独立副本：' + job.staging) : '') +
        (job.databases || []).map(d => '<div class="room-topic"><div><strong>' + esc(d.file) + '</strong>' +
          Object.entries(d.backup_counts || {}).map(([k,v]) => note(({memory_item:'记忆',knowledge_item:'知识',affinity_contact_day:'每日相处记录'})[k] + '：备份 ' + v + (d.current_counts[k] === undefined ? '' : ' / 当前 ' + d.current_counts[k]))).join('') + '</div></div>').join('') +
        '<details><summary>文件差异 · 最多显示 120 项</summary>' + ((job.differences || []).map(d => '<div class="room-diff"><span>' + esc(d.file) + '</span><span>' + esc(d.status) + '</span></div>').join('') || note('未发现所比较文件的差异。')) + '</details>';
    }
    async function poll() {
      try {
        const data = await request('/api/settings/recovery'); if (!active(host, token)) return;
        selected = select.value || selected;
        select.innerHTML = (data.items || []).map(item => '<option value="' + esc(item.id) + '"' + (item.id === selected ? ' selected' : '') + '>' + esc(item.id) + ' · ' + esc(item.files) + ' 文件</option>').join('');
        if (!select.options.length) result.innerHTML = note('还没有完整业务备份，可以先在上方立即备份。');
        status(data.job);
      } catch (error) { if (active(host, token)) result.textContent = error.message; }
    }
    async function start(rehearse) {
      if (busy || !select.value) return;
      busy = true; host.querySelectorAll('button,select').forEach(b => b.disabled = true);
      try { status(await request('/api/settings/recovery', { backup_id: select.value, rehearse })); }
      catch (error) { busy = false; host.querySelectorAll('button,select').forEach(b => b.disabled = false); result.textContent = error.message; }
    }
    host.querySelector('[data-preview]').onclick = () => start(false);
    host.querySelector('[data-rehearse]').onclick = () => start(true);
    await poll(); addTimer(poll, 3000);
  }

  async function life(host) {
    const token = app.token;
    try {
      const data = await request('/api/room/life'); if (!active(host, token)) return;
      host.innerHTML = '<div class="card-title">小屋里留下的日常</div>' + note(data.note) +
        ((data.items || []).map(r => '<a class="room-source" href="' + esc(r.href) + '"><span>' + esc(r.kind) + ' · ' + esc(shortTs(r.at)) + '</span><strong>' + esc(truncate(r.title, 65)) + '</strong><p>' + esc(r.text) + '</p></a>').join('') || note('日常会从实际保存的记录里慢慢出现。'));
    } catch (error) { if (active(host, token)) host.innerHTML = note('小屋记录暂时读取不到，稍后刷新即可。'); }
  }

  let lastSync = null, loadedBuild = null;
  async function sync() {
    if (document.hidden) return;
    try {
      const value = await request('/api/room/sync');
      loadedBuild ||= document.querySelector('meta[name="xiaoyu-build"]')?.content || value.build;
      const shownVersion = document.querySelector('meta[name="xiaoyu-version"]')?.content || value.release.version;
      document.querySelectorAll('[data-room-version]').forEach(e => e.textContent = 'v' + shownVersion);
      document.querySelectorAll('[data-room-release]').forEach(e => e.innerHTML = '<p><strong>小鱼之家 v' + esc(value.release.version) + '</strong> · ' + esc(value.release.name) + '</p>' + note(value.release.released_on) + '<ul>' + value.release.changes.map(c => '<li>' + esc(c) + '</li>').join('') + '</ul>');
      if (loadedBuild !== value.build && !document.querySelector('#roomUpdate')) {
        const banner = document.createElement('div'); banner.id = 'roomUpdate'; banner.className = 'room-update';
        banner.innerHTML = '<span>小屋已有更新，刷新后加载最新界面。</span><button type="button" class="btn sm">刷新加载更新</button>';
        banner.querySelector('button').onclick = () => { if (app.leave && !app.leave()) return; location.reload(); };
        document.body.append(banner);
      }
      if (lastSync && (lastSync.settings_revision !== value.settings_revision || lastSync.contacts_revision !== value.contacts_revision)) {
        window.dispatchEvent(new CustomEvent('xiaoyu:sync', { detail: value }));
      }
      lastSync = value;
    } catch (_) { /* Keep the current view when the local service is unavailable. */ }
  }
  function start() {
    const footer = document.querySelector('.nav-footer');
    const label = document.createElement('span'); label.className = 'room-version'; label.dataset.roomVersion = ''; label.title = '网页与软件共用版本';
    footer?.before(label); sync(); setInterval(sync, 15000);
    window.addEventListener('focus', sync);
  }
  window.XiaoyuRoom = { request, memory, contacts, recovery, life, start, sync };
})();
