/* Shared web/desktop indicator. Polls local saved state; no model requests. */
'use strict';

window.ChatOnlineStatus = (() => {
  function markup() {
    return '<div class="chat-online" id="homeOnlineStatus">' +
      '<button type="button" class="chat-online-button" aria-label="在线状态：正在读取，点击查看详情并刷新" ' +
        'aria-expanded="false" aria-controls="homeOnlineDetails">' +
        '<span class="chat-online-dot" data-online="false" aria-hidden="true"></span>' +
      '</button>' +
      '<div class="chat-online-details" id="homeOnlineDetails" role="region" aria-label="在线状态详情" hidden>' +
        '<div class="chat-online-heading">在线状态</div>' +
        '<p class="chat-online-refresh" role="status" hidden></p>' +
        '<p class="chat-online-message" aria-live="polite">正在读取连接状态…</p>' +
        '<p class="chat-online-note"></p>' +
        '<div class="chat-online-time"></div>' +
        '<div class="chat-online-cost">状态检测不额外消耗模型 token</div>' +
      '</div></div>';
  }

  function mount(host) {
    if (!host) return () => {};
    const button = host.querySelector('button');
    const dot = host.querySelector('.chat-online-dot');
    const details = host.querySelector('.chat-online-details');
    let disposed = false;
    let activeRequest = null;
    const feedback = host.querySelector('.chat-online-refresh');

    function setOpen(open, returnFocus = false) {
      details.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      if (returnFocus) button.focus();
    }

    function localTime(value) {
      const date = new Date(value);
      return value && Number.isFinite(date.getTime())
        ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date)
        : '';
    }

    function render(status) {
      const online = status.online === true;
      dot.dataset.online = String(online);
      button.setAttribute('aria-label', '在线状态：' + (online ? '在线' : '不可聊天') + '，点击查看详情并刷新');
      host.querySelector('.chat-online-message').textContent = status.message;
      host.querySelector('.chat-online-note').textContent = status.note || '';
      const checked = localTime(status.checked_at);
      const request = localTime(status.last_request_at);
      host.querySelector('.chat-online-time').textContent =
        (checked ? '状态更新：' + checked : '') + (request ? '\n最近真实请求：' + request : '');
    }

    async function refresh(force = false) {
      if (disposed || document.hidden || (activeRequest && !force)) return;
      activeRequest?.controller.abort();
      const request = { controller: new AbortController(), timeout: null };
      activeRequest = request;
      button.setAttribute('aria-busy', 'true');
      if (force) {
        feedback.hidden = false;
        feedback.textContent = '正在刷新在线状态…';
      }
      request.timeout = setTimeout(() => request.controller.abort(), 5000);
      try {
        const response = await fetch('/api/chat/status' + (force ? '?refresh=1' : ''),
          { signal: request.controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error('Local status unavailable');
        const result = await response.json();
        if (result.ok !== true || typeof result.data?.online !== 'boolean' || !result.data.message) {
          throw new Error('Incomplete local status');
        }
        if (!disposed && activeRequest === request) {
          render(result.data);
          feedback.hidden = !force;
          if (force) feedback.textContent = '已重新读取最新状态';
        }
      } catch (_) {
        if (!disposed && activeRequest === request) {
          render({ online: false,
          message: '当前不可聊天，原因：无法读取本地状态服务，暂时不能确认聊天连接',
          note: '可能是本地页面服务断开或读取超时，恢复后会自动更新。', checked_at: new Date().toISOString() });
          feedback.hidden = !force;
          if (force) feedback.textContent = '本次刷新未成功，暂时无法确认连接状态';
        }
      } finally {
        clearTimeout(request.timeout);
        if (activeRequest === request) {
          activeRequest = null;
          button.removeAttribute('aria-busy');
        }
      }
    }

    const onClick = () => { setOpen(details.hidden); refresh(true); };
    const onOutside = event => { if (!host.contains(event.target)) setOpen(false); };
    const onKey = event => {
      if (event.key === 'Escape' && !details.hidden) { setOpen(false, true); event.preventDefault(); }
    };
    const onVisible = () => { if (!document.hidden) refresh(); };
    button.addEventListener('click', onClick);
    document.addEventListener('click', onOutside);
    document.addEventListener('keydown', onKey);
    document.addEventListener('visibilitychange', onVisible);
    const timer = setInterval(() => refresh(), 15000);
    refresh();

    return () => {
      disposed = true;
      clearInterval(timer);
      clearTimeout(activeRequest?.timeout);
      activeRequest?.controller.abort();
      button.removeEventListener('click', onClick);
      document.removeEventListener('click', onOutside);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }

  return { markup, mount };
})();
