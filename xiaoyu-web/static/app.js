/* ============================================================
   小鱼 Web · 单页应用
   hash 路由 #/home 等；无依赖，原生 JS。
   ============================================================ */
'use strict';

/* ────────────────────── 基础工具 ────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, num(v))); }

function pad2(n) { return String(n).padStart(2, '0'); }

function todayStr() {
  const d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

function fmtCost(v) {
  const n = num(v);
  if (n === 0) return '0.00';
  if (n < 0.01) return n.toFixed(4);
  if (n < 1) return n.toFixed(3);
  return n.toFixed(2);
}

function fmtInt(v) {
  const n = num(v);
  if (n >= 10000) return (n / 10000).toFixed(1) + '万';
  return String(Math.round(n));
}

function shortTs(s) {
  s = String(s || '');
  if (!s) return '';
  const m = s.match(/(\d{2}):(\d{2})/);
  if (s.length >= 16 && s[10] === ' ') return s.slice(5, 16);
  if (s.length >= 16 && s[10] === 'T') return s.slice(5, 16).replace('T', ' ');
  return m ? s : s.slice(0, 16);
}

function timeOnly(s) {
  const m = String(s || '').match(/(\d{2}):(\d{2}):(\d{2})/);
  return m ? m[1] + ':' + m[2] : shortTs(s);
}

function truncate(s, n) {
  s = String(s === null || s === undefined ? '' : s);
  return s.length > n ? s.slice(0, n) + '…' : s;
}

/* ────────────────────── 提示 / 弹层 ────────────────────── */

function toast(msg, kind = '') {
  const wrap = $('#toasts');
  if (!wrap) return;
  const d = document.createElement('div');
  d.className = 'toast ' + kind;
  d.textContent = String(msg);
  wrap.appendChild(d);
  setTimeout(() => {
    d.style.transition = 'opacity .25s, transform .25s';
    d.style.opacity = '0';
    d.style.transform = 'translateY(6px)';
    setTimeout(() => d.remove(), 280);
  }, 2600);
}

let modalEscHandler = null;

function closeModal() {
  const root = $('#modalRoot');
  if (root) root.innerHTML = '';
  if (modalEscHandler) {
    document.removeEventListener('keydown', modalEscHandler);
    modalEscHandler = null;
  }
}

function openModal(title, bodyHtml) {
  closeModal();
  const root = $('#modalRoot');
  root.innerHTML =
    '<div class="modal-mask" id="modalMask">' +
      '<div class="modal">' +
        '<div class="modal-head">' +
          '<h3 class="modal-title">' + esc(title) + '</h3>' +
          '<span class="spacer"></span>' +
          '<button class="btn sm ghost" id="modalClose">关闭</button>' +
        '</div>' +
        '<div class="modal-body">' + bodyHtml + '</div>' +
      '</div>' +
    '</div>';
  $('#modalClose').onclick = closeModal;
  $('#modalMask').addEventListener('click', (e) => { if (e.target.id === 'modalMask') closeModal(); });
  modalEscHandler = (e) => { if (e.key === 'Escape') closeModal(); };
  document.addEventListener('keydown', modalEscHandler);
}

/* ────────────────────── API ────────────────────── */

async function api(path, body) {
  const opt = { headers: { 'Accept': 'application/json' } };
  if (body !== undefined) {
    opt.method = 'POST';
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  if (!res.ok) {
    let msg = 'HTTP ' + res.status;
    try { const j = await res.json(); if (j && j.error) msg = j.error; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  const j = await res.json();
  if (!j || j.ok !== true) throw new Error((j && j.error) || '接口返回失败');
  return j.data;
}

/* ────────────────────── HTML 片段 ────────────────────── */

function emptyBox(msg = '暂无数据', hint = '') {
  return '<div class="empty">' + esc(msg) +
    (hint ? '<span class="hint">' + esc(hint) + '</span>' : '') + '</div>';
}

function skeleton(n = 3) {
  let out = '';
  for (let i = 0; i < n; i++) {
    out += '<div class="skeleton" style="width:' + (92 - i * 12) + '%"></div>';
  }
  return out;
}

function pageHead(title, sub = '', actions = '') {
  return '<div class="page-head"><h2 class="page-title">' + esc(title) + '</h2>' +
    '<span class="page-sub">' + esc(sub) + '</span>' + actions + '</div>';
}

function roomIcon(name, className = 'nav-icon') {
  name = ({welcome: 'settings', chat: 'thinking', migration: 'memory', components: 'qq'})[name] || name;
  const build = document.querySelector('meta[name="xiaoyu-build"]')?.content || document.querySelector('meta[name="xiaoyu-version"]')?.content || '';
  return '<svg class="' + className + '" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/icons.svg?build=' + encodeURIComponent(build) + '#' + name + '"></use></svg>';
}

function pill(text, kind = '') {
  return '<span class="pill ' + kind + '">' + esc(text) + '</span>';
}

function errorCard(err, extra) {
  return '<div class="card">' + emptyBox('暂无数据',
    (err && err.message ? err.message : String(err || '')) +
    (extra ? ' · ' + extra : '')) + '</div>';
}

/* ────────────────────── 全局状态 ────────────────────── */

const app = {
  timers: [],
  disposers: [],
  leave: null,
  token: 0,
  graph: null,
};

const ui = {
  thinking: { order: 'desc', auto: true, session: '', peer: '', date: '', offset: 0, sig: '' },
  memory: { q: '', view: 'long1' },
  learning: { tab: 'intents' },
  voice: { tone: '', audioId: '' },
  contacts: {},
  cost: { range: '7d' },
  diary: { kind: 'daily', date: '', expanded: false },
  emotion: { tab: 'overview', date: '', history: 'recent' },
};

function addTimer(fn, ms) {
  const id = setInterval(fn, ms);
  app.timers.push(id);
  return id;
}

function clearTimers() {
  app.timers.forEach((t) => clearInterval(t));
  app.timers = [];
}

function onDispose(fn) { app.disposers.push(fn); }

function disposeAll() {
  app.disposers.forEach((fn) => { try { fn(); } catch (_) { /* ignore */ } });
  app.disposers = [];
  if (app.graph) { try { app.graph.destroy(); } catch (_) { /* ignore */ } app.graph = null; }
}

function onWindowResize(fn) {
  window.addEventListener('resize', fn);
  window.addEventListener('xiaoyu:appearance', fn);
  onDispose(() => { window.removeEventListener('resize', fn); window.removeEventListener('xiaoyu:appearance', fn); });
}

function canvasColor(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue('--chart-' + name).trim() || fallback;
}

/* ────────────────────── 画布工具 ────────────────────── */

function setupCanvas(canvas) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(80, canvas.clientWidth || canvas.parentElement.clientWidth || 300);
  const h = Math.max(60, canvas.clientHeight || 190);
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function roundRectPath(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h);
  ctx.lineTo(x, y + h);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

/* 折线图：五维情绪 */
function drawLines(canvas, cfg) {
  const { ctx, w, h } = setupCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const padL = 24, padR = 6, padT = 8, padB = 16;
  const iw = Math.max(10, w - padL - padR);
  const ih = Math.max(10, h - padT - padB);

  ctx.font = '9px system-ui, sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  [0, 25, 50, 75, 100].forEach((v) => {
    const y = padT + ih * (1 - v / 100);
    ctx.strokeStyle = canvasColor('grid', '#d3deeb');
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, y + 0.5);
    ctx.lineTo(w - padR, y + 0.5);
    ctx.stroke();
    ctx.fillStyle = canvasColor('label', '#71819a');
    ctx.fillText(String(v), padL - 4, y);
  });

  const series = cfg.series || [];
  const n = series.length;
  if (!n) {
    ctx.fillStyle = canvasColor('label', '#66768b');
    ctx.textAlign = 'center';
    ctx.font = '12px system-ui, sans-serif';
    ctx.fillText('暂无数据', w / 2, h / 2);
    return;
  }

  const xAt = (i) => (n === 1 ? padL + iw / 2 : padL + (iw * i) / (n - 1));
  const yAt = (v) => padT + ih * (1 - clamp(v, 0, 100) / 100);

  (cfg.dims || []).forEach((d, di) => {
    if (cfg.visible && cfg.visible[d.key] === false) return;
    const night = ['#89b6e4','#8bd3b2','#f19da6','#bcafe5','#e2a6cb'];
    const color = (document.documentElement.dataset.scheme === 'dark' ? night : cfg.colors)[di % cfg.colors.length];
    ctx.beginPath();
    series.forEach((row, i) => {
      const x = xAt(i);
      const y = yAt(row[d.key]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.7;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.shadowColor = color;
    ctx.shadowBlur = 0;
    ctx.stroke();
    ctx.shadowBlur = 0;

    const lastY = yAt(series[n - 1][d.key]);
    ctx.beginPath();
    ctx.arc(xAt(n - 1), lastY, 2.6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  });

  const times = cfg.times || [];
  ctx.fillStyle = canvasColor('label', '#71819a');
  ctx.font = '9px system-ui, sans-serif';
  ctx.textBaseline = 'top';
  if (times.length) {
    ctx.textAlign = 'left';
    ctx.fillText(timeOnly(times[0]), padL, h - padB + 4);
    if (times.length > 2) {
      ctx.textAlign = 'center';
      ctx.fillText(timeOnly(times[Math.floor(times.length / 2)]), padL + iw / 2, h - padB + 4);
    }
    ctx.textAlign = 'right';
    ctx.fillText(timeOnly(times[times.length - 1]), w - padR, h - padB + 4);
  }
}

/* 柱状图 */
function drawBars(canvas, rows, opts) {
  const { ctx, w, h } = setupCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const padL = 6, padR = 6, padT = 14, padB = 18;
  const iw = Math.max(10, w - padL - padR);
  const ih = Math.max(10, h - padT - padB);

  if (!rows || !rows.length) {
    ctx.fillStyle = canvasColor('label', '#66768b');
    ctx.font = '12px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('暂无数据', w / 2, h / 2);
    return;
  }

  const max = Math.max.apply(null, rows.map((r) => num(r.value)).concat([0.0001]));
  const gap = rows.length > 18 ? 1.5 : rows.length > 8 ? 3 : 6;
  // 柱子有最大宽度：只有 1~2 天数据时不要糊成一大块
  const bw = Math.max(3, Math.min(42, (iw - gap * (rows.length - 1)) / rows.length));
  const groupW = bw * rows.length + gap * (rows.length - 1);
  const startX = padL + Math.max(0, (iw - groupW) / 2);

  ctx.strokeStyle = canvasColor('grid', '#d3deeb');
  ctx.beginPath();
  ctx.moveTo(padL, padT + ih + 0.5);
  ctx.lineTo(w - padR, padT + ih + 0.5);
  ctx.stroke();

  rows.forEach((r, i) => {
    const v = num(r.value);
    const bh = Math.max(v > 0 ? 2 : 0, (v / max) * ih);
    const x = startX + i * (bw + gap);
    const y = padT + ih - bh;
    const g = ctx.createLinearGradient(0, y, 0, y + bh);
    g.addColorStop(0, canvasColor('color', '#6289b7'));
    g.addColorStop(1, canvasColor('color', '#527ba9'));
    ctx.fillStyle = g;
    roundRectPath(ctx, x, y, bw, bh, Math.min(3, bw / 2));
    ctx.fill();
  });

  // x 轴标签：首 / 中 / 尾
  ctx.fillStyle = canvasColor('label', '#71819a');
  ctx.font = '9px system-ui, sans-serif';
  ctx.textBaseline = 'top';
  const labels = opts && opts.labels ? opts.labels : [];
  const idxs = rows.length > 2 ? [0, Math.floor(rows.length / 2), rows.length - 1] : [0];
  idxs.forEach((i, k) => {
    if (!labels[i]) return;
    ctx.textAlign = k === 0 ? 'left' : k === idxs.length - 1 ? 'right' : 'center';
    const cx = startX + i * (bw + gap) + bw / 2;
    ctx.fillText(labels[i], k === 0 ? padL : k === idxs.length - 1 ? w - padR : cx, h - padB + 4);
  });

  // 最大值提示
  const maxIdx = rows.reduce((best, r, i) => (num(r.value) > num(rows[best].value) ? i : best), 0);
  if (num(rows[maxIdx].value) > 0) {
    ctx.fillStyle = canvasColor('label', '#66768b');
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText((opts && opts.fmt ? opts.fmt(rows[maxIdx].value) : String(rows[maxIdx].value)),
      padL + 1, 1);
  }
}

/* 曲线图：花费按天 */
function drawCurve(canvas, rows, opts) {
  const { ctx, w, h } = setupCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const padL = 40, padR = 10, padT = 16, padB = 22;
  const iw = Math.max(10, w - padL - padR);
  const ih = Math.max(10, h - padT - padB);

  if (!rows || !rows.length) {
    ctx.fillStyle = canvasColor('label', '#66768b');
    ctx.font = '12px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('暂无数据', w / 2, h / 2);
    return;
  }

  const values = rows.map((r) => Math.max(0, num(r.value)));
  const rawMax = Math.max.apply(null, values.concat([0.0001]));
  // 取一个好看的刻度上限（1/2/5 × 10^n）
  const mag = Math.pow(10, Math.floor(Math.log10(rawMax)));
  const norm = rawMax / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  const max = nice * mag * 1.08;

  const xAt = (i) => (rows.length === 1 ? padL + iw / 2 : padL + (iw * i) / (rows.length - 1));
  const yAt = (v) => padT + ih * (1 - clamp(v / max, 0, 1));

  // 横向网格 + 金额刻度
  ctx.font = '9px system-ui, sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const v = (max * i) / 4;
    const y = padT + ih * (1 - i / 4);
    ctx.strokeStyle = canvasColor('grid', '#d3deeb');
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, y + 0.5);
    ctx.lineTo(w - padR, y + 0.5);
    ctx.stroke();
    ctx.fillStyle = canvasColor('label', '#71819a');
    ctx.fillText('¥' + fmtCost(v), padL - 5, y);
  }

  // 平滑曲线路径（相邻点用二次贝塞尔的中点法连接）
  function tracePath() {
    ctx.beginPath();
    ctx.moveTo(xAt(0), yAt(values[0]));
    for (let i = 1; i < rows.length; i++) {
      const x0 = xAt(i - 1), y0 = yAt(values[i - 1]);
      const x1 = xAt(i), y1 = yAt(values[i]);
      const mx = (x0 + x1) / 2;
      ctx.bezierCurveTo(mx, y0, mx, y1, x1, y1);
    }
  }

  // 面积渐变
  tracePath();
  ctx.lineTo(xAt(rows.length - 1), padT + ih);
  ctx.lineTo(xAt(0), padT + ih);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, padT, 0, padT + ih);
  grad.addColorStop(0, 'rgba(82,123,169,.30)');
  grad.addColorStop(1, 'rgba(82,123,169,0)');
  ctx.fillStyle = grad;
  ctx.fill();

  // 曲线
  tracePath();
  ctx.strokeStyle = canvasColor('color', '#6289b7');
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.shadowColor = 'rgba(82,123,169,.75)';
  ctx.shadowBlur = 0;
  ctx.stroke();
  ctx.shadowBlur = 0;

  // 数据点：点少时全标，点多时只标首/中/尾
  const showDots = rows.length <= 24;
  const labelIdx = new Set(rows.length > 2 ? [0, Math.floor((rows.length - 1) / 2), rows.length - 1] : [0]);
  rows.forEach((r, i) => {
    const x = xAt(i), y = yAt(values[i]);
    if (showDots || labelIdx.has(i)) {
      ctx.beginPath();
      ctx.arc(x, y, showDots ? 2.2 : 1.8, 0, Math.PI * 2);
      ctx.fillStyle = canvasColor('color', '#6289b7');
      ctx.fill();
    }
    if (labelIdx.has(i) && values[i] > 0) {
      ctx.fillStyle = canvasColor('label', '#667b96');
      ctx.font = '9px system-ui, sans-serif';
      ctx.textAlign = i === 0 ? 'left' : i === rows.length - 1 ? 'right' : 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText('¥' + fmtCost(values[i]), x, y - 5);
    }
  });

  // x 轴日期
  ctx.fillStyle = canvasColor('label', '#71819a');
  ctx.font = '9px system-ui, sans-serif';
  ctx.textBaseline = 'top';
  const labels = opts && opts.labels ? opts.labels : rows.map((r) => String(r.label || ''));
  [0, Math.floor((rows.length - 1) / 2), rows.length - 1].filter((v, i, a) => a.indexOf(v) === i).forEach((i, k, arr) => {
    if (!labels[i]) return;
    ctx.textAlign = k === 0 ? 'left' : k === arr.length - 1 ? 'right' : 'center';
    ctx.fillText(labels[i], k === 0 ? padL : k === arr.length - 1 ? w - padR : xAt(i), h - padB + 5);
  });
}

/* 记忆星图的共用绘制与交互位于 memory-graph.js。 */
function createGraph(canvas, data, onPick) {
  return createMemoryConstellation(canvas, data, onPick);
}

/* ══════════════════════════════════════════════════════════
   视图
   ══════════════════════════════════════════════════════════ */

/* ────────────── 首页 ────────────── */

async function viewHome(root) {
  const roomDate = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
  root.innerHTML =
    pageHead('首页', '从这里，看看她的一天', ChatOnlineStatus.markup()) +
    '<div class="hero">' +
      '<div class="salon-only room-scene" aria-hidden="true"><div class="room-wall-line"></div><div class="room-floor"></div><div class="room-rug"></div><img class="room-window" src="/static/room-window.svg" alt=""><img class="room-shelf" src="/static/room-shelf.svg" alt=""></div>' +
      '<div class="hero-date">' + roomIcon('sun', '') + esc(roomDate) + '</div>' +
      '<div class="hero-main">' +
        '<div class="hero-techline"><span class="room-default">XIAOYU’S LITTLE ROOM</span><span class="salon-only">小鱼的房间 · 欢迎来坐坐</span></div>' +
        '<div class="hero-name"><span class="room-default">小鱼的日常，<br>都在这里。</span><span class="salon-only">欢迎回家，<br>小鱼在呢。</span></div>' +
        '<div class="hero-tag"><span class="room-default">偶尔冒泡，有点嘴硬。<br>把想到的、记得的，慢慢收好。</span><span class="salon-only">窗边发会儿呆，<br>也把你说的话好好收着。</span></div>' +
        '<div class="hero-mood" id="heroMood"></div>' +
      '</div>' +
      '<img class="hero-portrait" id="heroPortrait" alt="小鱼立绘" ' +
        'src="/api/asset?name=xiaoyu_portrait.png">' +
      '<img class="salon-only room-desk" src="/static/room-desk.svg" alt="" aria-hidden="true">' +
      '<div class="salon-only salon-scene-note" aria-hidden="true">给你留了一个位置。</div>' +
    '</div>' +
    '<div id="homeCards">' + skeleton(5) + '</div>';

  onDispose(ChatOnlineStatus.mount($('#homeOnlineStatus')));

  const img = $('#heroPortrait');
  if (img) {
    img.onerror = () => { img.style.display = 'none'; };
  }

  let d;
  try {
    d = await api('/api/home');
  } catch (e) {
    $('#homeCards').innerHTML = errorCard(e, '后端 /api/home');
    return;
  }

  const emo = d.emotion || null;
  const mem = d.memory || {};
  const cost = d.cost || {};
  const diary = d.diary_today;

  // hero 情绪胶囊
  const hm = $('#heroMood');
  if (hm && emo) {
    hm.innerHTML = '<div class="mood-capsule"><span class="dot"></span>' +
      esc(emo.dominant_label || emo.dominant || '平静') + '</div>';
  }

  // 卡 1：她现在
  let emoHtml;
  if (emo && emo.values && emo.values.length) {
    const bars = emo.values.map((v) => (
      '<div class="bar-row" style="grid-template-columns:52px minmax(0,1fr) 40px">' +
        '<span class="bar-name">' + esc(v.label) + '</span>' +
        '<div class="bar-track"><i class="bar-fill" style="width:' + clamp(v.value, 0, 100) + '%"></i></div>' +
        '<span class="bar-val tiny muted">' + num(v.value).toFixed(1) + '</span>' +
      '</div>'
    )).join('');
    emoHtml =
      '<div class="row" style="margin-bottom:9px">' +
        '<div class="mood-capsule"><span class="dot"></span>' + esc(emo.dominant_label || '—') + '</div>' +
        '<span class="spacer"></span>' +
        '<span class="tiny muted">' + esc(shortTs(emo.t)) + '</span>' +
      '</div>' +
      bars;
  } else {
    emoHtml = emptyBox('暂无数据', 'emotion_snapshot 还没记录');
  }

  const memCount = num(mem.count);
  const memMeter = clamp((memCount / 500) * 100, 2, 100);

  const cardNow =
    '<div class="card" id="homeNow">' +
      '<div class="card-title">她现在<span class="title-extra">主导情绪 / 记忆</span></div>' +
      emoHtml +
      '<div style="margin-top:11px">' +
        '<div class="row-between tiny muted" style="margin-bottom:3px">' +
          '<span>记忆条数</span><span class="num">' + memCount + ' 条</span>' +
        '</div>' +
        '<div class="meter"><i style="width:' + memMeter.toFixed(1) + '%"></i></div>' +
        '<div class="row tiny muted" style="margin-top:6px">' +
          '<span>知识 ' + num(mem.knowledge) + ' 条</span>' +
          (diary ? '<span class="spacer"></span><span class="pill ok">今日日记 · ' + esc(diary.mood || '已写') + '</span>' : '') +
        '</div>' +
      '</div>' +
    '</div>';

  // 卡 2：今日花费
  const bd = (cost.breakdown || []).slice(0, 4);
  const maxBd = Math.max.apply(null, bd.map((b) => num(b.cost)).concat([0.0001]));
  const bdHtml = bd.length
    ? bd.map((b) => (
        '<div class="bar-row">' +
          '<span class="bar-name">' + esc(b.tag) + '</span>' +
          '<div class="bar-track"><i class="bar-fill" style="width:' + clamp((num(b.cost) / maxBd) * 100, 3, 100) + '%"></i></div>' +
          '<span class="bar-val tiny muted">¥' + fmtCost(b.cost) + '</span>' +
        '</div>'
      )).join('')
    : '<div class="tiny muted">今天还没有花费记录</div>';

  const cardCost =
    '<div class="card" id="homeCost">' +
      '<div class="card-title">今日花费<span class="title-extra">' + esc(cost.date || todayStr()) + '</span></div>' +
      '<div class="big-num">¥' + fmtCost(cost.total) + '</div>' +
      '<div class="stat-grid" style="margin-top:9px">' +
        '<div class="stat"><div class="k">轮次</div><div class="v">' + num(cost.rounds) + '</div></div>' +
        '<div class="stat"><div class="k">输入 token</div><div class="v">' + fmtInt(cost.input_tokens) + '</div></div>' +
        '<div class="stat"><div class="k">输出 token</div><div class="v">' + fmtInt(cost.output_tokens) + '</div></div>' +
      '</div>' +
      '<div style="margin-top:11px">' + bdHtml + '</div>' +
    '</div>';

  // 卡 3：快速入口
  const learn = d.learning;
  const quick = [
    ['#/diary', '日记', diary ? ('今日 ' + diary.chars + ' 字') : '今天还没写'],
    ['#/memory', '记忆 + 星图', memCount + ' 条记忆'],
    ['#/thinking', '思考流', 'DSH 会话实时'],
    ['#/voice', '语音合成', '三套语气'],
    ['#/learning', '学习', learn ? ('今日 ' + num(learn.today_rounds) + '/' + num(learn.rounds_per_day) + ' 轮') : '配额'],
    ['#/qzone', 'QQ 空间', '发说说'],
    ['#/emotion', '情绪曲线', '五维折线'],
    ['#/qq', 'QQ 接入', d.qq ? (num(d.qq.conversations) + ' 个会话') : '会话状态'],
  ];
  const cardQuick =
    '<div class="card" id="homeQuick">' +
      '<div class="card-title">快速入口<span class="title-extra">' + esc(d.generated_at || '') + '</span></div>' +
      '<div class="quick-grid">' +
        quick.map(([href, name, sub]) =>
          '<button class="quick" data-href="' + href + '">' +
            '<div class="quick-title-row">' + roomIcon(href.slice(2), 'quick-icon') + '<div class="q-name">' + esc(name) + '</div></div>' +
            '<div class="q-sub">' + esc(sub) + '</div>' +
          '</button>').join('') +
      '</div>' +
    '</div>';

  const box = $('#homeCards');
  box.className = 'home-cards';
  box.innerHTML = cardNow + cardCost + cardQuick;
  const life = document.createElement('section'); life.id = 'roomLife'; life.className = 'card'; box.append(life);
  window.XiaoyuRoom.life(life);

  $$('.quick', box).forEach((b) => {
    b.onclick = () => { location.hash = b.dataset.href; };
  });
}

/* ────────────── 思考 ────────────── */

async function viewThinking(root, q) {
  const st = ui.thinking;
  st.date = q && parseLocalDay(q.get('d')) ? q.get('d') : st.date || todayStr();
  st.offset = 0;
  root.innerHTML =
    pageHead('思考', '按联系人和日期查看历史；英文内容在本机翻译，不调用翻译 API') +
    '<div class="think-dates">' +
      '<label for="tDate">查看日期</label>' +
      '<button class="btn sm ghost" id="tPrevDay">前一天</button>' +
      '<input type="date" id="tDate" aria-label="思考日期" value="' + esc(st.date) + '">' +
      '<button class="btn sm ghost" id="tNextDay">后一天</button>' +
      '<button class="btn sm" id="tToday">今天</button>' +
      '<label for="tHistory">历史</label>' +
      '<select id="tHistory" aria-label="有记录的历史日期"><option value="">有记录的日期</option></select>' +
    '</div>' +
    '<div class="think-toolbar">' +
      '<div class="seg" id="tOrder">' +
        '<button data-o="desc" class="' + (st.order === 'desc' ? 'active' : '') + '">最新在上</button>' +
        '<button data-o="asc" class="' + (st.order === 'asc' ? 'active' : '') + '">最早在上</button>' +
      '</div>' +
      '<button class="btn sm" id="tAuto">' + (st.auto ? '自动刷新 3s：开' : '自动刷新：关') + '</button>' +
      '<button class="btn sm ghost" id="tRefresh">刷新</button>' +
    '</div>' +
    '<div class="think-peers" id="tPeers"></div>' +
    '<div class="card" style="padding:9px 12px;margin-bottom:11px">' +
      '<div class="row-between tiny muted">' +
        '<span id="tMeta">加载中…</span>' +
        '<span id="tCount"></span>' +
      '</div>' +
    '</div>' +
    '<div class="think-pagination" id="tPager" hidden>' +
      '<button class="btn sm ghost" id="tPrevPage">上一页</button>' +
      '<span class="tiny muted" id="tPageInfo"></span>' +
      '<button class="btn sm ghost" id="tNextPage">下一页</button>' +
    '</div>' +
    '<div id="tList">' + skeleton(4) + '</div>';

  const listEl = $('#tList');
  const peersEl = $('#tPeers');
  const metaEl = $('#tMeta');
  const countEl = $('#tCount');
  const dateEl = $('#tDate');
  const historyEl = $('#tHistory');
  const pageSize = 160;

  let alive = true;
  let requestId = 0;
  let loading = false;
  let historySignature = '';
  onDispose(() => { alive = false; });

  function renderPeers(peers, current) {
    st.peers = peers || [];
    if (!peers || !peers.length) {
      peersEl.innerHTML = '<span class="tiny muted">暂无聊天对象</span>';
      return;
    }
    peersEl.innerHTML = peers.map((p) => {
      const active = p.key === current;
      return '<button class="think-peer' + (active ? ' active' : '') + '" data-peer="' + esc(p.key) + '">' +
        '<span class="tp-name">' + esc(p.label || p.qq) + '</span>' +
        '<span class="tp-meta">' + (p.has_data ? esc(p.time || '有记录') : '待开始') + '</span>' +
        '</button>';
    }).join('');
  }

  function renderEvents(items) {
    if (!items.length) {
      listEl.innerHTML = emptyBox('当天暂无可展示的思考', st.date + ' 没有可阅读的思考内容，可以选择其他日期或联系人');
      return;
    }
    const html = items.map((it) => {
      const head = '<div class="ev-head">';
      const tail = '<span>' + esc(it.time || '') + '</span></div>';
      if (it.kind === 'reasoning') {
        return '<div class="ev k-reasoning" data-event-id="' + esc(it.id) + '">' + head +
          '<span class="ev-kind">小鱼的思考</span>' +
          (it.translation ? '<span class="tiny muted">' + (it.translation === 'ready' ? '中文译文' : it.translation === 'pending' ? '正在本机翻译' : it.translation === 'stopped' ? '已停止重试' : '等待本地重试') + '</span>' : '') +
          '<span class="spacer"></span>' + tail +
          '<div class="ev-body">' + esc(it.text) + '</div></div>';
      }
      if (it.kind === 'assistant') {
        return '<div class="ev k-assistant" data-event-id="' + esc(it.id) + '">' + head +
          '<span class="ev-kind">小鱼的话</span><span class="spacer"></span>' + tail +
          '<div class="ev-body">' + esc(it.text) + '</div></div>';
      }
      return '';
    }).join('');
    listEl.innerHTML = html;
  }

  async function load(force) {
    if (loading && !force) return;
    const thisRequest = ++requestId;
    loading = true;
    try {
      const query = '/api/thinking?limit=' + pageSize + '&offset=' + st.offset +
        '&date=' + encodeURIComponent(st.date) + '&order=' + encodeURIComponent(st.order) +
        (st.peer ? '&peer=' + encodeURIComponent(st.peer) : '') +
        (st.session ? '&session=' + encodeURIComponent(st.session) : '');
      const d = await api(query);
      if (!alive || thisRequest !== requestId) return;

      const items = d.items || [];
      const meta = d.session || {};
      if (meta.peer) st.peer = meta.peer;
      if (meta.id) st.session = meta.id;
      renderPeers(d.peers || [], st.peer);
      const dates = d.available_dates || [];
      const nextHistorySignature = st.peer + '|' + dates.join('|');
      if (nextHistorySignature !== historySignature) {
        historyEl.innerHTML = '<option value="">有记录的日期（' + dates.length + ' 天）</option>' +
          dates.slice().reverse().map(day => '<option value="' + esc(day) + '">' + esc(day) + '</option>').join('');
        historySignature = nextHistorySignature;
      }
      historyEl.value = dates.includes(st.date) ? st.date : '';

      const sig = [d.revision, st.date, st.offset, st.order, meta.peer, d.total_items, d.error].join('|');
      const changed = sig !== st.sig || force;
      st.sig = sig;

      const peerBadge = meta.peer_alias
        ? '<span class="pill primary" style="padding:0 6px">' + esc(meta.peer_alias) + ' · QQ ' + esc(meta.peer_qq) + '</span>'
        : '';
      metaEl.innerHTML =
        (peerBadge ? peerBadge + ' · ' : '') +
        '<strong id="tSelectedDate">' + esc(st.date) + '</strong>' +
        ' · ' + num(d.session_count) + ' 段会话' +
        (d.hidden_items > 0 ? ' · 已隐藏运行记录' : '') +
        (d.error ? ' · ' + esc(d.error) : '');
      const total = num(d.total_items);
      countEl.textContent = (items.length ? (st.offset + 1) + '–' + (st.offset + items.length) + ' / ' : '') + total + ' 条内容' +
        (st.auto ? ' · 3s 刷新' : '');
      $('#tPager').hidden = total <= pageSize;
      $('#tPrevPage').disabled = st.offset === 0;
      $('#tNextPage').disabled = !d.has_more;
      $('#tPageInfo').textContent = '第 ' + (Math.floor(st.offset / pageSize) + 1) + ' / ' + Math.max(1, Math.ceil(total / pageSize)) + ' 页';

      if (changed) {
        const y = window.scrollY;
        renderEvents(items);
        window.scrollTo(0, y);
      }
    } catch (e) {
      if (!alive || thisRequest !== requestId) return;
      listEl.innerHTML = errorCard(e, '会话日志：当前实例的数据目录 / dsh / sessions');
      metaEl.textContent = '读取失败';
    } finally {
      if (thisRequest === requestId) loading = false;
    }
  }

  function selectDay(day) {
    if (!parseLocalDay(day)) {
      dateEl.value = st.date;
      return;
    }
    st.date = day;
    st.offset = 0;
    dateEl.value = day;
    history.replaceState(null, '', '#/thinking?d=' + encodeURIComponent(day));
    metaEl.textContent = '正在读取 ' + day + '…';
    countEl.textContent = '';
    $('#tPager').hidden = true;
    listEl.innerHTML = skeleton(3);
    load(true);
  }

  function shiftDay(amount) {
    const day = parseLocalDay(st.date);
    day.setDate(day.getDate() + amount);
    selectDay(localDayString(day));
  }

  dateEl.onchange = () => selectDay(dateEl.value);
  historyEl.onchange = () => { if (historyEl.value) selectDay(historyEl.value); };
  $('#tPrevDay').onclick = () => shiftDay(-1);
  $('#tNextDay').onclick = () => shiftDay(1);
  $('#tToday').onclick = () => selectDay(todayStr());
  $('#tPrevPage').onclick = () => { if (!loading) { st.offset = Math.max(0, st.offset - pageSize); load(true); } };
  $('#tNextPage').onclick = () => { if (!loading) { st.offset += pageSize; load(true); } };

  $('#tOrder').onclick = (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    st.order = b.dataset.o;
    st.offset = 0;
    st.sig = '';
    $$('#tOrder button').forEach((x) => x.classList.toggle('active', x === b));
    load(true);
  };

  $('#tRefresh').onclick = () => load(true);

  $('#tAuto').onclick = () => {
    st.auto = !st.auto;
    $('#tAuto').textContent = st.auto ? '自动刷新 3s：开' : '自动刷新：关';
    toast(st.auto ? '自动刷新已开启（3 秒）' : '自动刷新已关闭');
    if (st.auto) addTimer(() => load(false), 3000);
    else clearTimers();
  };

  peersEl.onclick = (e) => {
    const b = e.target.closest('button[data-peer]');
    if (!b) return;
    const p = (st.peers || []).find((x) => x.key === b.dataset.peer);
    st.peer = b.dataset.peer;
    st.session = p && p.session ? p.session : '';
    st.offset = 0;
    st.sig = '';
    load(true);
  };

  await load(true);
  if (st.auto) addTimer(() => load(false), 3000);
}

/* ────────────── 人设 ────────────── */

async function viewPersona(root) {
  root.innerHTML = pageHead('人设', 'personality.md 全文') + '<div id="pBody">' + skeleton(6) + '</div>';

  let d;
  try {
    d = await api('/api/persona');
  } catch (e) {
    $('#pBody').innerHTML = errorCard(e);
    return;
  }

  if (!d.exists) {
    $('#pBody').innerHTML = '<div class="card">' +
      emptyBox('暂无数据', '读不到 ' + (d.path || 'personality.md')) +
      '<div class="row" style="margin-top:10px"><button class="btn sm" id="pReload">重试</button></div></div>';
    $('#pReload').onclick = () => viewPersona(root);
    return;
  }

  $('#pBody').innerHTML =
    '<div class="card">' +
      '<div class="card-title">人格设定' +
        '<span class="title-extra">' + esc(d.chars) + ' 字符 · ' + esc(d.modified || '') + '</span>' +
      '</div>' +
      '<div class="tiny muted mono" style="margin-bottom:7px;overflow-wrap:anywhere">' + esc(d.path) + '</div>' +
      '<textarea id="pText" class="persona-area" spellcheck="false"></textarea>' +
      '<div class="sticky-actions">' +
        '<button class="btn primary" id="pSave">保存</button>' +
        '<button class="btn ghost" id="pReset">还原改动</button>' +
        '<span class="spacer"></span>' +
        '<span class="tiny muted" id="pStat"></span>' +
      '</div>' +
    '</div>';

  const ta = $('#pText');
  ta.value = d.text;
  const stat = $('#pStat');
  let dirty = false;

  function refreshStat() {
    const cur = ta.value.length;
    stat.textContent = (dirty ? '未保存 · ' : '') + cur + ' 字符';
    stat.style.color = dirty ? 'var(--warn)' : '';
  }
  refreshStat();

  ta.addEventListener('input', () => {
    dirty = ta.value !== d.text;
    refreshStat();
  });

  app.leave = () => {
    if (!dirty) return true;
    return window.confirm('人设还没保存，确定离开？');
  };

  $('#pReset').onclick = () => {
    ta.value = d.text;
    dirty = false;
    refreshStat();
    toast('已还原为磁盘上的内容');
  };

  $('#pSave').onclick = async () => {
    const btn = $('#pSave');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> 保存中';
    try {
      const r = await api('/api/persona', { text: ta.value, revision: d.revision });
      d.text = r.text;
      d.revision = r.revision;
      ta.value = r.text;
      dirty = false;
      refreshStat();
      toast('人设与 QQ 预设已同步保存', 'ok');
      openModal('已保存', '<div class="modal-body">' +
        '<p>人设及两个 QQ 预设已同步保存：</p>' +
        '<div class="tiny mono" style="overflow-wrap:anywhere">' + esc(r.path || '') + '</div>' +
        '<p style="margin-top:12px;color:var(--warn)">聊天空闲时重新加载 DSH 后生效，当前对话不会在保存时被打断。</p>' +
        (r.backup ? '<p class="tiny muted">旧文件备份：' + esc(r.backup) + '</p>' : '') +
        '</div>');
    } catch (e) {
      toast('保存失败：' + e.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = '保存';
    }
  };
}

/* ────────────── 语音 ────────────── */

async function viewVoice(root) {
  root.innerHTML = pageHead('语音', 'GPT-SoVITS 三套语气') + '<div id="vBody">' + skeleton(4) + '</div>';

  let d;
  try {
    d = await api('/api/voice');
  } catch (e) {
    $('#vBody').innerHTML = errorCard(e);
    return;
  }

  const tones = d.tones || [];
  if (!tones.length) {
    $('#vBody').innerHTML = '<div class="card">' +
      emptyBox('暂无数据', '读不到 ' + (d.path || 'voice_config.json')) + '</div>';
    return;
  }
  if (!ui.voice.tone || !tones.some((t) => t.name === ui.voice.tone)) {
    ui.voice.tone = d.default_tone || tones[0].name;
  }

  const offline = d.tts_online === false;
  const toneHtml = tones.map((t) => (
    '<div class="tone' + (t.name === ui.voice.tone ? ' sel' : '') + '" data-tone="' + esc(t.name) + '">' +
      '<span class="tone-radio"></span>' +
      '<span style="min-width:0;flex:1">' +
        '<span class="tone-name">' + esc(t.name) + '</span>' +
        '<span class="tone-meta"> · 温度 ' + num(t.temperature).toFixed(2) +
          ' · 语速 ' + num(t.speed).toFixed(2) + '</span>' +
        (t.ref_exists ? '' : '<div class="tiny" style="color:var(--warn)">参考音频不存在</div>') +
      '</span>' +
    '</div>'
  )).join('');

  const owner = (ui.contacts.owner || '');
  const autoVoice = d.automatic_voice || {};
  const dailyContact = d.daily_contact || {};
  const care = d.conversation_care || {};
  const settling = care.replySettling || {};
  const localWaiting = care.localWait || {};
  const careChats = care.conversations || [];
  const contacted = careChats.filter(c => c.dailySent > 0).length;
  const understanding = d.understanding || {};
  const quietHours = (care.daily || {}).quietHours || ['23:30', '08:30'];
  const conversationHtml = '<div class="card">' +
    '<div class="card-title">自动交流<span class="title-extra">' + (care.paused ? '已暂停' : '与 QQ 共用机制') + '</span></div>' +
    '<div class="small">主动联系：' + (dailyContact.enabled ? '每个白名单对话每天 1 次，内容承接最近聊天' : '未开启') +
    '；安静时段 ' + esc(quietHours.join('–')) + '。</div>' +
    (settling.enabled ? '<div class="small" style="margin-top:8px">回复节奏：最后一条消息后约 ' +
      Math.round((settling.quietMs || 10000) / 1000) + ' 秒，连续补充重新计时；语音和视频处理完后，再一起回复。</div>' : '') +
    (localWaiting.enabled ? '<div class="small" style="margin-top:8px">等待消息：约 ' +
      Math.round((localWaiting.idleMs || 60000) / 1000) + ' 秒无新消息后本地收尾；新消息再唤醒，主动活动按时执行，不轮询模型。</div>' : '') +
    '<div class="small" style="margin-top:8px">自主语音：' + (autoVoice.enabled ? '日常回复约 ' + Math.round((autoVoice.probability || 0) * 100) + '% 使用语音条' : '未开启') +
    '，语音内容同步进入记忆；长文字保留完整文本。</div>' +
    '<div class="small" style="margin-top:8px">听语音：' + (understanding.voiceEnabled ? '普通话转写' : '未开启') +
    ' · 看视频：' + (understanding.videoEnabled ? '时间采样画面与普通话音轨，支持 5 分钟以内、64 MB 以内' : '未开启') + '</div>' +
    '<div class="tiny muted" style="margin-top:10px">今日已主动联系 ' + contacted + ' / ' + careChats.length +
    ' 个对话 · 本地媒体服务' + (d.media_service && d.media_service.ok ? '在线' : '离线') +
    ' · 发送失败不计为完成，恢复后再尝试。</div></div>';
  $('#vBody').innerHTML =
    (offline ? '<div class="card" style="border-color:rgba(146,103,47,.4);margin-bottom:11px">' +
        '<div class="row"><span class="pill warn">离线</span>' +
        '<span class="small">配置的语音服务尚未连接；请前往“接入与组件”填写自己的后端。</span></div>' +
      '</div>' : '') +
    conversationHtml + '<div class="card">' +
      '<div class="card-title">语气<span class="title-extra">' + (offline ? '服务离线' : '语音接口在线') + '</span></div>' +
      '<div class="tone-list" id="toneList">' + toneHtml + '</div>' +
    '</div>' +
    '<div class="card">' +
      '<div class="card-title">文本<span class="title-extra" id="vCount">0 字符</span></div>' +
      '<textarea id="vText" rows="4" placeholder="写一句想听她说的话…" maxlength="300"></textarea>' +
      '<div class="row" style="margin-top:9px">' +
        '<button class="btn primary" id="vSynth">合成并播放</button>' +
        '<button class="btn" id="vSendQQ">发到 QQ</button>' +
        '<span class="spacer"></span>' +
        '<span class="tiny muted">' + esc(d.max_chars) + ' 字以内更自然</span>' +
      '</div>' +
      '<div id="vAudioBox" style="margin-top:9px"></div>' +
    '</div>';

  const ta = $('#vText');
  const cnt = $('#vCount');
  function updCount() { cnt.textContent = ta.value.length + ' 字符'; }
  ta.addEventListener('input', updCount);

  $$('#toneList .tone').forEach((el) => {
    el.onclick = () => {
      ui.voice.tone = el.dataset.tone;
      $$('#toneList .tone').forEach((x) => x.classList.toggle('sel', x === el));
    };
  });

  async function synth() {
    const text = ta.value.trim();
    if (!text) { toast('先写点文本'); ta.focus(); return null; }
    if (ta.value.length > 300) { toast('太长了，300 字以内'); return null; }
    const btn = $('#vSynth');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> 合成中…';
    try {
      const r = await api('/api/voice/synth', { text: text, tone: ui.voice.tone });
      ui.voice.audioId = r.id;
      $('#vAudioBox').innerHTML =
        '<audio controls autoplay src="' + esc(r.url) + '?t=' + Date.now() + '"></audio>' +
        '<div class="tiny muted" style="margin-top:4px">' +
          esc(r.tone) + ' · ' + (num(r.bytes) / 1024).toFixed(0) + ' KB</div>';
      toast('合成完成', 'ok');
      return r;
    } catch (e) {
      $('#vAudioBox').innerHTML = emptyBox('合成失败', e.message);
      toast('合成失败：' + e.message, 'err');
      return null;
    } finally {
      btn.disabled = false;
      btn.textContent = '合成并播放';
    }
  }

  $('#vSynth').onclick = synth;

  $('#vSendQQ').onclick = async () => {
    let id = ui.voice.audioId;
    if (!id) {
      const r = await synth();
      if (!r) return;
      id = r.id;
    }
    const qq = window.prompt('发到哪个 QQ？（留空 = config.json 里的 ownerQQ）', owner || '');
    if (qq === null) return;
    if (!window.confirm('确定把这段语音发到 QQ ' + (qq.trim() || 'owner') + ' 吗？')) return;
    const btn = $('#vSendQQ');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> 发送中…';
    try {
      const r = await api('/api/voice/send', { id: id, user_id: qq.trim() || undefined });
      toast('已发送到 QQ ' + r.user_id, 'ok');
    } catch (e) {
      toast('发送失败：' + e.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = '发到 QQ';
    }
  };
}

/* ────────────── 联系人 ────────────── */

async function viewContacts(root) {
  const token = app.token;
  root.innerHTML = pageHead('联系人', '熟悉的人，慢慢积累的相处时光') + '<div id="cBody">' + skeleton(3) + '</div>';
  let d;
  try {
    d = await api('/api/contacts');
  } catch (e) {
    if (token !== app.token || !root.isConnected) return;
    $('#cBody').innerHTML = errorCard(e);
    return;
  }
  if (token !== app.token || !root.isConnected) return;
  ui.contacts.owner = d.owner || '';

  const rows = (d.private || []).map((p) =>
    '<tr>' +
      '<td class="mono">' + esc(p.qq) + '</td>' +
      '<td>' + (p.alias ? esc(p.alias) : '<span class="muted">—</span>') +
        (p.is_owner ? ' ' + pill('owner', 'primary') : '') + '</td>' +
      '<td class="muted">私聊</td>' +
      '<td class="num muted">' + (p.recent !== undefined ? esc(p.recent) : '—') + '</td>' +
    '</tr>').join('');

  const groups = (d.groups || []).map((g) =>
    '<tr><td class="mono">' + esc(g.qq) + '</td><td>' + esc(g.alias || '—') + '</td>' +
    '<td class="muted">群聊</td><td class="num muted">—</td></tr>').join('');

  $('#cBody').innerHTML =
    '<div id="cAffinityHost"></div><div id="cPreferencesHost">' + skeleton(2) + '</div>' +
    '<div class="card" id="cCrossConversation" style="margin-bottom:11px">' +
      '<div class="card-title">跨对话联系' +
        '<span class="title-extra">' + pill(d.cross_conversation?.enabled ? '已开启' : '未开启', d.cross_conversation?.enabled ? 'primary' : '') + '</span></div>' +
      '<div>' + (d.cross_conversation?.enabled
        ? '小鱼可在所有白名单聊天之间发送文字、查询最近消息。发送对象以目标联系人的称呼为准。'
        : '小鱼的文字聊天目前分别处理，跨对话联系尚未开启。') + '</div>' +
      '<div class="tiny muted" style="margin-top:8px">读取最近消息不会替其他聊天标记已读；白名单、暂停及发送频率设置继续生效。</div>' +
    '</div>' +
    '<div class="card">' +
      '<div class="card-title">白名单' +
        '<span class="title-extra">' + num((d.private || []).length) + ' 人 · ' +
        num((d.groups || []).length) + ' 群</span></div>' +
      '<div class="row" style="margin-bottom:9px">' +
        pill('owner ' + (d.owner || '—'), 'primary') +
        (d.allow_all_when_empty ? pill('空名单时放行全部', 'warn') : '') +
      '</div>' +
      ((d.private || []).length || (d.groups || []).length
        ? '<div class="table-wrap"><table class="tbl">' +
            '<thead><tr><th>QQ</th><th>称呼</th><th>类型</th><th class="num">最近</th></tr></thead>' +
            '<tbody>' + rows + groups + '</tbody></table></div>'
        : emptyBox('暂无数据', 'allow.private / allow.groups 都是空的')) +
      '<div class="tiny muted" style="margin-top:10px">称呼来自 aliases.json · ' + esc(d.aliases_path || '') + '</div>' +
    '</div>';
  await Promise.all([viewAffinityCard($('#cAffinityHost')), window.XiaoyuRoom.contacts($('#cPreferencesHost'), d)]);
}

/* ────────────── 空间 ────────────── */

async function viewQzone(root) {
  root.innerHTML = pageHead('空间', 'QQ 空间说说与评论') +
    '<div class="card">' +
      '<div class="card-title">发说说</div>' +
      '<textarea id="qzInput" rows="3" placeholder="想说点什么…"></textarea>' +
      '<div class="row" style="margin-top:8px">' +
        '<button class="btn primary" id="qzSend">发布</button>' +
        '<span class="spacer"></span><span class="tiny muted" id="qzWho"></span>' +
      '</div>' +
    '</div>' +
    '<div class="card" id="qzFeed" style="margin-top:11px">' + skeleton(4) + '</div>';

  $('#qzSend').onclick = async () => {
    const content = $('#qzInput').value.trim();
    if (!content) { toast('内容不能为空'); return; }
    if (!window.confirm('确定发布这条说说？')) return;
    const btn = $('#qzSend');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> 发布中';
    try {
      await api('/api/qzone/post', { content: content });
      $('#qzInput').value = '';
      toast('已发布', 'ok');
      await loadFeed();
    } catch (e) {
      toast('发布失败：' + e.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = '发布';
    }
  };

  async function loadFeed() {
    const el = $('#qzFeed');
    try {
      const d = await api('/api/qzone');
      $('#qzWho').textContent = d.whoami ? ('登录 ' + d.whoami.uin) : '';
      const items = d.items || [];
      if (!items.length) {
        el.innerHTML = '<div class="card-title">说说</div>' +
          emptyBox('暂无数据', (d.errors || []).join('；') || 'feed 是空的');
        return;
      }
      el.innerHTML = '<div class="card-title">说说<span class="title-extra">' + items.length + ' 条</span></div>' +
        items.map((it) => {
          const cms = (it.comments || []).map((c) =>
            '<div class="qz-comment"><span class="who">' + esc(c.name || c.uin) + '</span>' +
            esc(c.content) + '</div>').join('');
          return '<div class="qz-post">' +
            '<div class="qz-text">' + esc(it.content) + '</div>' +
            '<div class="row tiny muted" style="margin-top:4px">' +
              '<span>' + esc(it.time) + '</span>' +
              '<span class="spacer"></span>' +
              '<span>' + num((it.comments || []).length) + ' 条评论</span>' +
            '</div>' +
            (cms ? '<div class="qz-comments">' + cms + '</div>' : '') +
          '</div>';
        }).join('');
    } catch (e) {
      el.innerHTML = '<div class="card-title">说说</div>' + emptyBox('暂无数据', e.message);
    }
  }

  await loadFeed();
}

/* ────────────── 记忆 + 星图 ────────────── */

function memoryKind(kind) {
  return ({ semantic_belief: '长期信念', episodic_state: '短时状态',
    episodic_event: '事件记录', legacy_record: '原有记录', user_correction: '用户更正', user_supplement: '用户补充' })[kind] || '记录';
}

async function viewMemory(root, q) {
  const views = [
    ['long1', '1号长期记忆'], ['long2', '2号长期记忆'], ['diary', '短期 · 日记本'],
    ['pending', '短期 · 待总结'], ['instant', '瞬时记忆'], ['language', '语言记忆'],
    ['emotion', '情绪记忆'], ['residual', '思维链 · 最近想法'], ['all', '全部记忆'], ['graph', '记忆星图'],
  ];
  // 精简导航入口；原有分类、数据和历史链接继续可用。
  const visibleViews = views.filter(v => !['diary', 'pending', 'emotion'].includes(v[0]));
  const requested = q.get('view') || ui.memory.view;
  const selected = views.some(v => v[0] === requested) ? requested : 'long1';
  ui.memory.view = selected;
  const label = views.find(v => v[0] === selected)[1];
  const pageToken = app.token;
  const active = () => pageToken === app.token;
  const group = q.get('group') || '';
  const subtypeLabels = { personality: '性格设定', friend: '朋友档案', owner: '老大', conclusion: '沉淀结论' };
  const hints = {
    long1: '性格、朋友、老大与沉淀结论，按旧版分类查看。点击记录可读原文。',
    long2: '按情绪分类查看已沉淀的长期记忆。',
    diary: '短期记忆中保存的日记片段。完整日记也可在左侧「日记」中按日期查看。',
    pending: '等待总结或升级为长期记忆的短期内容。',
    instant: '按创建时间查看瞬时记忆；保留当前记录，不在页面中执行清理。',
    language: '原有语言记忆账本。', emotion: '原有情绪记忆账本。',
    all: '查看各层记忆，包括保留原文的到期与更正记录。',
  };
  root.innerHTML =
    pageHead('记忆', '分类记忆与星图') +
    '<div class="section-tabs" id="mTabs" role="tablist" aria-label="记忆分区">' +
      visibleViews.map(v => '<button role="tab" aria-selected="' + (selected === v[0]) + '" data-view="' + v[0] +
        '" class="' + (selected === v[0] ? 'active' : '') + '">' + esc(v[1]) +
        '<span class="tab-count" data-count="' + v[0] + '"></span></button>').join('') + '</div>' +
    '<div class="card" id="mRetrieval">' + skeleton(2) + '</div>' +
    (selected === 'graph' ? '<div class="card">' +
      '<div class="card-title">记忆星图<span class="title-extra" id="gStats">加载中…</span></div>' +
      '<div class="graph-wrap memory-atlas-shell">' + memoryConstellationMarkup() + '</div>' +
    '</div>' : selected === 'residual' ? '<div class="card" id="mResidual">' + skeleton(4) + '</div>' :
    '<div class="card">' +
      '<div class="card-title">' + esc(label) + '<span class="title-extra" id="mCount"></span></div>' +
      '<p class="tiny muted" id="mHint">' + esc(hints[selected] || '') + '</p>' +
      '<div class="row" style="margin-bottom:9px">' +
        '<input type="search" id="mSearch" aria-label="搜索当前记忆分区" placeholder="搜索此分区的摘要、原文、概念…" value="' + esc(ui.memory.q) + '">' +
        '<select id="mGroup" aria-label="记忆分类" hidden></select>' +
      '</div>' +
      '<div id="mList">' + skeleton(5) + '</div>' +
      '<button class="btn sm ghost load-more" id="mMore" hidden>加载更多</button>' +
    '</div>');
  const retrievalEl = $('#mRetrieval', root);
  const tabsEl = $('#mTabs', root);
  tabsEl.onclick = e => {
    const button = e.target.closest('button[data-view]');
    if (button) location.hash = '#/memory?view=' + button.dataset.view;
  };

  async function openDetail(id) {
    openModal('记忆详情', skeleton(4));
    const pendingModal = $('#modalMask');
    try {
      const d = await api('/api/memory/detail?id=' + encodeURIComponent(id));
      if (!active() || $('#modalMask') !== pendingModal) return;
      const concepts = (d.concepts || []).map((c) => pill(c, 'dim')).join(' ');
      let body;
      if (d.kind === 'knowledge') {
        body =
          '<div class="kv">' +
            '<div class="k">类型</div><div>知识 · ' + esc(d.domain || '—') + '</div>' +
            '<div class="k">置信度</div><div>' + num(d.confidence).toFixed(2) + '</div>' +
            '<div class="k">学习于</div><div>' + esc(d.learned_at || '—') + '</div>' +
            '<div class="k">来源</div><div>' + esc(d.source || '未记录') + '</div>' +
            '<div class="k">id</div><div class="mono tiny">' + esc(d.id) + '</div>' +
          '</div>' +
          '<div style="margin:10px 0 5px">' + concepts + '</div>' +
          '<div style="border-top:1px solid var(--line);margin-top:10px;padding-top:10px">' + esc(d.content || '') + '</div>';
      } else {
        body =
          '<div class="kv">' +
            '<div class="k">层级</div><div>' + esc(d.layer) + ' / ' + esc(d.subtype) + '</div>' +
            '<div class="k">情绪</div><div>' + esc(d.emotion_tag || '—') + '</div>' +
            '<div class="k">显著程度</div><div>' + num(d.salience).toFixed(3) + '</div>' +
            '<div class="k">创建</div><div>' + esc(d.t_created || '—') + '</div>' +
            '<div class="k">状态</div><div>' + esc(({resolved:'已保存',open:'待处理',superseded:'已有更正',expired:'已到期',merged:'已合并'})[d.status] || d.status || '—') + '</div>' +
            '<div class="k">记录类型</div><div>' + esc(memoryKind(d.memory_type)) + '</div>' +
            '<div class="k">可信度</div><div>' + Math.round(num(d.confidence) * 100) + '%</div>' +
            '<div class="k">有效期</div><div>' + esc(d.valid_until ? (d.valid_from || d.t_created) + ' 至 ' + d.valid_until : '未设到期时间') + '</div>' +
            '<div class="k">来源 / 对象</div><div>' + esc(d.origin || '未记录') + ' / ' + esc(d.subject || '未标注') + '</div>' +
            '<div class="k">更正链</div><div class="mono tiny">' + esc(d.superseded_by ? '由 ' + d.superseded_by + ' 更正' : (d.supersedes ? '更正了 ' + d.supersedes : '无更正记录')) + '</div>' +
            '<div class="k">id</div><div class="mono tiny">' + esc(d.id) + '</div>' +
          '</div>' +
          '<div style="margin:10px 0 5px">' + concepts + '</div>' +
          '<div style="border-top:1px solid var(--line);margin-top:10px;padding-top:10px">' +
            esc(d.content || d.summary || '') + '</div>';
      }
      openModal(d.kind === 'knowledge' ? (d.name || '知识') : '记忆详情', body + (d.kind === 'knowledge' ? '' : '<div id="memoryEditorial"></div>'));
      if (d.kind !== 'knowledge') window.XiaoyuRoom.memory($('#memoryEditorial'), d, openDetail);
    } catch (e) {
      if (active() && $('#modalMask') === pendingModal) openModal('记忆详情', emptyBox('暂无数据', e.message));
    }
  }

  async function loadRetrieval() {
    try {
    const status = await api('/api/retrieval/status');
    if (!active()) return;
    const indexed = status.indexed || {};
    const local = String(status.backend || '').indexOf('multilingual-minilm') === 0;
    const lookup = status.lookup || {};
    retrievalEl.innerHTML = '<div class="card-title">记忆检索</div>' +
      '<div class="row">' + pill(status.offline ? '核心离线' : (local ? '本地语义 + 关键词' : '关键词降级检索'), 'primary') +
      pill(num(indexed.memory) + ' 条记忆') + pill(num(indexed.knowledge) + ' 条知识') + '</div>' +
      '<div class="tiny muted" style="margin-top:9px">' +
      esc(status.offline ? status.note : '过期状态和已被更正的记录保留原文，日常回忆优先使用当前有效的内容。') + '</div>' +
      (lookup.last_reason ? '<div class="tiny muted" style="margin-top:6px">联网查词：' + esc(lookup.last_reason) + '</div>' : '') +
      (status.reflection && status.reflection.at ? '<div class="tiny muted" style="margin-top:6px">最近本地整理：' + esc(shortTs(status.reflection.at)) + '</div>' : '');
  } catch (e) {
    if (active()) retrievalEl.innerHTML = '<div class="card-title">记忆检索</div>' + emptyBox('状态暂不可用', e.message);
  }
  }

  async function loadSections() {
    try {
      const data = await api('/api/memory/sections');
      if (!active()) return;
      (data.sections || []).forEach(section => {
        const count = $('[data-count="' + section.id + '"]', tabsEl);
        if (count) count.textContent = section.count;
      });
    } catch (_) { /* Lists remain available if counts are temporarily unavailable. */ }
  }

  async function loadContent() {
  if (selected === 'graph') {
   try {
    const g = await api('/api/memory/graph');
    if (!active()) return;
    const stats = g.stats || {};
    $('#gStats').textContent = stats.nodes + ' 节点 · ' + stats.edges + ' 边';
    if (!g.nodes || !g.nodes.length) {
      $('.graph-wrap').innerHTML = emptyBox('暂无数据', '没有带 concepts 的记忆/知识');
    } else {
      const canvas = $('#gCanvas');
      app.graph = createGraph(canvas, g, (node) => openDetail(node.id));
      $('#gRelayout').onclick = () => app.graph && app.graph.relayout();
      $('#gReset').onclick = () => app.graph && app.graph.resetView();
      const graph = app.graph;
      onDispose(() => { graph.destroy(); if (app.graph === graph) app.graph = null; });
    }
  } catch (e) {
    if (active()) $('.graph-wrap', root).innerHTML = emptyBox('暂无数据', e.message);
  }
  return;
  }

  if (selected === 'residual') {
    const el = $('#mResidual', root);
    try {
      const data = await api('/api/memory/residual');
      if (!active()) return;
      const residual = data.residual || {};
      const items = residual.top || [];
      el.innerHTML = '<div class="card-title">思维链 · 最近想法<span class="title-extra">' +
        (data.offline ? '核心离线' : num(residual.pool) + ' / ' + num(residual.capacity) + ' 条') + '</span></div>' +
        '<div class="tiny muted">' + (residual.enabled ? '残留通道已启用' : '残留通道未启用') + '</div>' +
        (items.length ? '<div class="list">' + items.map(item => '<div class="list-item">' +
          '<div class="mem-meta">' + pill(num(item.minutes) + ' 分钟前', 'primary') +
          pill('档 ' + num(item.tier)) + pill(item.is_open ? '未决' : '已想完') +
          pill(item.emotion_tag || '未分类', 'dim') + '</div>' +
          '<div class="memory-text">' + esc(item.text || '') + '</div></div>').join('') + '</div>' :
          emptyBox('暂时没有最近想法', data.note || '有新的残留节点时会显示在这里。'));
    } catch (e) { if (active()) el.innerHTML = errorCard(e); }
    return;
  }

  // 列表
  const listEl = $('#mList');
  const cntEl = $('#mCount');

  let items = [], offset = 0, requestId = 0;
  const more = $('#mMore', root);
  const groupEl = $('#mGroup', root);
  function groupName(m) {
    if (selected === 'long1') return subtypeLabels[m.subtype] || m.subtype || '其他';
    if (selected === 'long2') return m.emotion_tag || '未分类';
    if (selected === 'diary') return m.day || '日期未记录';
    if (selected === 'instant') return {hot: '热区', warm: '温区', retained: '更早的保留记录', unknown: '时间未记录'}[m.instant_band] || '';
    return '';
  }
  function renderItems() {
    let previous = '';
    const displayed = items.slice();
    if (['long1', 'long2', 'diary'].includes(selected)) {
      const order = selected === 'long1' ? ['性格设定', '朋友档案', '老大', '沉淀结论'] : ['开心', '难过', '生气', '创伤', '平静', '害羞'];
      displayed.sort((a, b) => {
        const an = groupName(a), bn = groupName(b);
        const ai = order.indexOf(an), bi = order.indexOf(bn);
        return (selected === 'diary' ? bn.localeCompare(an) :
          (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || an.localeCompare(bn)) || String(b.t_created).localeCompare(String(a.t_created));
      });
    }
    listEl.innerHTML = '<div class="list">' + displayed.map(m => {
      const name = groupName(m);
      const heading = name && name !== previous ? '<h3 class="memory-group">' + esc(name) + '</h3>' : '';
      previous = name;
      return heading + '<div class="list-item clickable mem-item" role="button" tabindex="0" data-id="' + esc(m.id) + '">' +
          '<div class="mem-summary">' + esc(m.summary || truncate(m.content, 120) || '（无摘要）') + '</div>' +
          '<div class="mem-meta">' +
            pill(m.layer === 'short' ? '短期记忆' : (views.find(v => v[0] === m.layer) || ['', m.layer])[1], 'primary') +
            pill(subtypeLabels[m.subtype] || m.subtype) + pill(memoryKind(m.memory_type), 'dim') +
            (m.status === 'expired' ? pill('已到期') : (m.superseded_by ? pill('已更正') : '')) +
            (m.emotion_tag ? pill(m.emotion_tag, 'dim') : '') +
            (selected === 'instant' && m.age_hours !== null ? pill(m.age_hours + ' 小时前') : '') +
            '<span class="tiny muted">' + esc(shortTs(m.t_created)) + '</span>' +
            '<span class="spacer"></span><span class="tiny muted num">显著 ' + num(m.salience).toFixed(2) + '</span>' +
          '</div></div>';
    }).join('') + '</div>';
    $$('.mem-item', listEl).forEach(el => {
      el.onclick = () => openDetail(el.dataset.id);
      el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(el.dataset.id); } };
    });
  }
  async function loadList(append = false) {
    const rid = ++requestId;
    if (!append) { items = []; offset = 0; listEl.innerHTML = skeleton(4); }
    more.disabled = true;
    try {
      const d = await api('/api/memory?limit=60&offset=' + offset + '&section=' + selected +
        '&group=' + encodeURIComponent(group) + '&q=' + encodeURIComponent(ui.memory.q));
      if (!active() || rid !== requestId) return;
      const rows = d.items || [];
      offset += rows.length;
      items = Array.from(new Map(items.concat(rows).map(m => [m.id, m])).values());
      cntEl.textContent = d.total + ' 条';
      if (selected === 'instant' && d.instant_windows) $('#mHint', root).textContent =
        '热区 ' + d.instant_windows.hot_hours + ' 小时内，温区至 ' + d.instant_windows.purge_hours + ' 小时。这里仅查看已有记录，不执行清理。';
      if ((d.groups || []).length) {
        groupEl.hidden = false;
        groupEl.innerHTML = '<option value="">全部分类</option>' + d.groups.map(g => {
          const value = g.name || '__empty__';
          const name = selected === 'long1' ? (subtypeLabels[g.name] || g.name || '其他') : (g.name || '未分类');
          return '<option value="' + esc(value) + '"' + (group === value ? ' selected' : '') + '>' + esc(name) + '（' + g.count + '）</option>';
        }).join('');
      }
      more.hidden = offset >= d.total || !rows.length;
      if (!items.length) {
        listEl.innerHTML = emptyBox('此分区暂无记录', ui.memory.q ? '没有匹配的记忆，可以调整搜索词。' : '已有记录仍保存在原来的记忆库中。');
        return;
      }
      renderItems();
    } catch (e) {
      if (active() && rid === requestId) {
        if (append) toast('加载失败：' + e.message, 'err');
        else listEl.innerHTML = emptyBox('读取失败', e.message);
      }
    } finally {
      if (rid === requestId) more.disabled = false;
    }
  }
  more.onclick = () => loadList(true);
  const memoryChanged = () => { if (active()) loadList(); };
  window.addEventListener('xiaoyu:memory-changed', memoryChanged);
  onDispose(() => window.removeEventListener('xiaoyu:memory-changed', memoryChanged));
  groupEl.onchange = () => { location.hash = '#/memory?view=' + selected + '&group=' + encodeURIComponent(groupEl.value); };

  let timer = null;
  const search = $('#mSearch');
  search.addEventListener('input', () => {
    ui.memory.q = search.value.trim();
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => loadList(), 300);
  });
  onDispose(() => { if (timer) clearTimeout(timer); });

  await loadList();
  }
  await Promise.all([loadRetrieval(), loadSections(), loadContent()]);
  if (active() && q.get('record')) openDetail(q.get('record'));
}

/* ────────────── 学习 ────────────── */

function learningTable(headers, rows) {
  return rows.length ? '<div class="table-wrap"><table class="tbl"><thead><tr>' +
    headers.map(h => '<th>' + esc(h) + '</th>').join('') + '</tr></thead><tbody>' +
    rows.map(row => '<tr>' + row.map(cell => '<td>' + esc(cell) + '</td>').join('') + '</tr>').join('') +
    '</tbody></table></div>' : emptyBox('暂无记录');
}

function learningReports(reports) {
  return reports.map(r => '<button class="report-item report-open" data-report="' + esc(r.name) + '">' +
    '<span class="report-head">' + esc(r.title || r.name) + '</span>' +
    '<span class="report-body">' + esc(r.head) + '</span>' +
    '<span class="tiny muted">' + esc(r.mtime) + ' · ' + num(r.chars) + ' 字 · 点击阅读全文</span></button>').join('') ||
    emptyBox('暂无学习报告');
}

function wireLearningReports(root) {
  const pageToken = app.token;
  $$('[data-report]', root).forEach(button => {
    button.onclick = async () => {
      openModal('学习报告', skeleton(4));
      try {
        const data = await api('/api/learning/report?name=' + encodeURIComponent(button.dataset.report));
        if (pageToken === app.token) openModal(data.title || '学习报告', '<div class="memory-text">' + esc(data.content) + '</div>');
      } catch (e) { if (pageToken === app.token) openModal('学习报告', emptyBox('读取失败', e.message)); }
    };
  });
}

function learningJobText(job) {
  if (!job || !job.id) return '';
  if (job.running) return job.action === 'engine' ? '正在运行好奇心引擎…' : '正在学习，完成后会自动刷新队列与报告…';
  if (job.error) return '执行失败：' + job.error;
  const out = job.result || {};
  if (out.skipped || out.reason || out.error) return out.skipped || out.reason || out.error;
  if (job.action === 'engine') return out.target ? '本轮选中：' + out.target + '（紧迫度 ' + num(out.urgency).toFixed(2) + '）' : '本轮没有领域超过学习阈值。';
  return out.ok ? '学习完成 · 关闭 ' + num(out.closed_open) + ' 个未决节点 · 花费 ¥' + fmtCost(out.cost) : '本轮未完成学习，请查看报告。';
}

async function viewLearning(root, q) {
  const tabs = [['intents', '意图队列与报告'], ['engine', '好奇心引擎'], ['quota', '上网学习配额']];
  const requested = q.get('tab') || ui.learning.tab;
  const tab = tabs.some(item => item[0] === requested) ? requested : 'intents';
  ui.learning.tab = tab;
  root.innerHTML = pageHead('学习', '学习意图、好奇心与上网配额') +
    '<div class="section-tabs" id="lTabs" role="tablist" aria-label="学习分区">' +
    tabs.map(item => '<button role="tab" aria-selected="' + (tab === item[0]) + '" data-tab="' + item[0] +
      '" class="' + (tab === item[0] ? 'active' : '') + '">' + item[1] + '</button>').join('') + '</div>' +
    '<div id="lContent">' + skeleton(5) + '</div>';
  $('#lTabs', root).onclick = e => {
    const button = e.target.closest('button[data-tab]');
    if (button) location.hash = '#/learning?tab=' + button.dataset.tab;
  };
  const content = $('#lContent', root);
  if (tab === 'quota') { await viewLearningQuota(content); return; }
  const pageToken = app.token;
  const [data, reports] = await Promise.all([
    api('/api/learning/dashboard'),
    tab === 'intents' ? api('/api/learning/reports?limit=12').catch(() => ({items: []})) : Promise.resolve({items: []}),
  ]);
  if (pageToken !== app.token) return;
  if (data.offline) { content.innerHTML = errorCard({message: data.note || '学习核心暂不可用'}); return; }
  const schedule = data.schedule || {};
  const engine = data.engine || {};
  const curiosity = data.curiosity || {};
  const signals = curiosity.signals || {};
  const names = {gap: '缺口', pred_err: '预测误差', progress: '进度', competence: '能力', social: '社会'};
  const signalEntries = Object.entries(signals).sort((a, b) => num(b[1]) - num(a[1]));
  const primarySignal = signalEntries[0] || ['gap', 0];
  const queue = curiosity.queue || [];
  const gaps = data.gaps || [];
  const weights = Object.entries(curiosity.domain_weights || {}).map(([k, v]) => k + ' ' + num(v).toFixed(2)).join(' · ') || '暂无';
  const fatigue = Object.entries(curiosity.fatigue || {}).map(([k, v]) => k + ' ' + num(v).toFixed(2)).join(' · ') || '无';
  if (tab === 'intents') {
    const statuses = {queued: '待学习', learning: '学习中', done: '已完成', abandoned: '已放弃', deferred: '已延后'};
    const sources = {autonomous: '好奇心引擎', conversation: '对话', manual: '手动', webstudy: '上网学习'};
    content.innerHTML = '<div class="card" id="lSchedule"><div class="card-title">学习安排</div>' +
      '<div class="row"><label class="check-label"><input type="checkbox" id="lScheduled"' +
      (schedule.enabled ? ' checked' : '') + '> 定时学习（' + esc(schedule.window_start) + '–' + esc(schedule.window_end) + '）</label>' +
      '<span class="tiny muted">' + esc(schedule.timezone) + '</span><button class="btn sm" id="lScheduleSave">保存开关</button>' +
      '<span class="spacer"></span><button class="btn primary sm" id="lRunOne">现在学一个</button>' +
      '<button class="btn sm ghost" id="lRefresh">刷新</button></div><div id="lJobStatus" class="tiny muted action-status"></div></div>' +
      '<div class="stat-grid learning-stats" id="lStats">' + [
        ['待学队列', num(curiosity.open_intents) + ' 条', queue.length ? '最急：' + queue[0].target : '队列是空的'],
        ['好奇心主信号', (names[primarySignal[0]] || primarySignal[0]) + ' ' + num(primarySignal[1]).toFixed(2),
          signalEntries.map(([k, v]) => (names[k] || k) + ' ' + num(v).toFixed(2)).join(' · ')],
        ['知识缺口', gaps.length + ' 处', gaps.length ? gaps[0].name : '暂无缺口'],
        ['联网预算 / 疲劳', curiosity.budget_ok ? '预算充足' : '预算已用完', '疲劳：' + fatigue],
      ].map(item => '<div class="stat"><div class="k">' + esc(item[0]) + '</div><div class="v">' + esc(item[1]) +
        '</div><div class="tiny muted stat-note">' + esc(item[2]) + '</div></div>').join('') + '</div>' +
      '<div class="card" id="lQueue"><div class="card-title">学习意图队列<span class="title-extra">按好奇心分排序</span></div>' +
      learningTable(['目标', '来源', '分数', '可学性', '状态'], queue.map(item => [item.target, sources[item.source] || item.source,
        num(item.score).toFixed(3), num(item.learnability).toFixed(2), statuses[item.status] || item.status])) + '</div>' +
      '<div class="card" id="lGaps"><div class="card-title">知识库缺口（元认知）</div>' +
      learningTable(['领域', '主题', '缺什么'], gaps.map(item => [item.domain, item.name, (item.missing || []).join('、') || '—'])) +
      '<p class="tiny muted">领域权重：' + esc(weights) + '</p></div>' +
      '<div class="card" id="lReports"><div class="card-title">最近学习报告</div>' + learningReports(reports.items || []) + '</div>';
    wireLearningReports(content);
    $('#lScheduleSave', root).onclick = () => saveLearning({scope: 'schedule', enabled: $('#lScheduled', root).checked});
    $('#lRunOne', root).onclick = () => runLearning('learn');
    $('#lRefresh', root).onclick = () => route();
  } else {
    content.innerHTML = '<div class="card" id="lEngine"><div class="card-title">好奇心引擎</div>' +
      '<div class="row"><label class="check-label"><input type="checkbox" id="lEngineEnabled"' + (engine.enabled ? ' checked' : '') + '> 总开关</label>' +
      '<label class="check-label"><input type="checkbox" id="lEngineAuto"' + (engine.auto_learn ? ' checked' : '') + '> 自动执行学习</label></div>' +
      '<div class="quota-form" style="margin-top:12px"><div class="field"><label for="lEngineInterval">调度间隔（分钟）</label>' +
      '<input type="number" id="lEngineInterval" min="5" max="720" value="' + num(engine.interval_minutes) + '"></div>' +
      '<div class="field"><label for="lEngineBudget">单日额度（元，0 为不限）</label>' +
      '<input type="number" id="lEngineBudget" min="0" max="100" step="0.01" value="' + num(engine.budget_limit) + '"></div></div>' +
      '<div class="row" style="margin-top:12px"><button class="btn primary sm" id="lEngineSave">保存设置</button>' +
      '<span class="spacer"></span><button class="btn sm" id="lRunEngine">立刻跑一轮</button><button class="btn sm ghost" id="lRefresh">刷新</button></div>' +
      '<p class="tiny muted">调度间隔在后台下次启动后生效；开关与额度保存后即按原规则读取。</p>' +
      '<div id="lJobStatus" class="tiny muted action-status"></div></div>' +
      '<div class="card"><div class="card-title">今日额度与上次调度</div>' +
      '<div class="row">' + pill('今日已用 ¥' + fmtCost(engine.budget_used), 'primary') +
      pill(engine.budget_limit > 0 ? '上限 ¥' + fmtCost(engine.budget_limit) : '单日额度不限') + '</div>' +
      '<p class="tiny muted">上次调度：' + esc(shortTs(engine.last_run) || '还没有') + ' · 目标：' + esc(engine.last_target || '无') +
      ' · 紧迫度 ' + num(engine.last_urgency).toFixed(2) + '</p>' +
      (engine.budget_blocked ? '<p class="tiny warning">' + esc(engine.budget_note) + '</p>' : '') + '</div>' +
      '<div class="card" id="lDomains"><div class="card-title">领域仪表盘</div>' +
      learningTable(['领域', '紧迫度', '进步', '无聊', '缺口', '探索次数'], (engine.domains || []).map(item => [item.name,
        num(item.urgency).toFixed(2), num(item.progress).toFixed(3), num(item.boredom).toFixed(2), num(item.gap).toFixed(2), num(item.explored)])) +
      '<p class="tiny muted">紧迫度综合缺口、进步、新颖与无聊；关掉「自动执行学习」后，引擎只排队。查看页面不会运行引擎。</p></div>';
    $('#lEngineSave', root).onclick = () => {
      const interval = $('#lEngineInterval', root), budget = $('#lEngineBudget', root);
      if (!interval.reportValidity() || !budget.reportValidity()) return;
      saveLearning({scope: 'engine', enabled: $('#lEngineEnabled', root).checked, auto_learn: $('#lEngineAuto', root).checked,
        interval_minutes: num(interval.value), budget_limit_per_day: num(budget.value)});
    };
    $('#lRunEngine', root).onclick = () => runLearning('engine');
    $('#lRefresh', root).onclick = () => route();
  }

  async function saveLearning(body) {
    const button = $(body.scope === 'schedule' ? '#lScheduleSave' : '#lEngineSave', root);
    button.disabled = true;
    try {
      await api('/api/learning/settings', body);
      toast('学习设置已保存', 'ok');
      if (pageToken === app.token) await route();
    } catch (e) { toast('保存失败：' + e.message, 'err'); }
    finally { button.disabled = false; }
  }
  async function runLearning(action) {
    if (action === 'learn' && !window.confirm('现在学习队列中分数最高的一项？\n将按现有预算联网并可能调用收费模型。')) return;
    const button = $(action === 'learn' ? '#lRunOne' : '#lRunEngine', root);
    button.disabled = true;
    try {
      await api('/api/learning/run', {action: action});
      if (pageToken === app.token) await route();
    } catch (e) { toast('启动失败：' + e.message, 'err'); button.disabled = false; }
  }
  const job = data.job || {};
  const statusEl = $('#lJobStatus', root);
  statusEl.textContent = learningJobText(job);
  if (job.running) {
    $$('#lRunOne, #lRunEngine', root).forEach(button => { button.disabled = true; });
    let polling = false;
    addTimer(async () => {
      if (polling || pageToken !== app.token) return;
      polling = true;
      try {
        const result = await api('/api/learning/job');
        if (pageToken !== app.token) return;
        if (result.offline) { statusEl.textContent = result.note || '核心暂不可用，正在等待任务状态。'; return; }
        statusEl.textContent = learningJobText(result.job);
        if (!result.job || !result.job.running) await route();
      } catch (e) { statusEl.textContent = '暂时无法读取学习状态：' + e.message; }
      finally { polling = false; }
    }, 2000);
  }
}

async function viewLearningQuota(root) {
  const pageToken = app.token;
  root.innerHTML = '<div id="lBody">' + skeleton(5) + '</div>';
  let d;
  try {
    d = await api('/api/learning');
    if (pageToken !== app.token) return;
  } catch (e) {
    $('#lBody').innerHTML = errorCard(e);
    return;
  }

  const qf = d.quota_file || {};
  const st = d.status || {};
  const today = st.today || {};
  const usedRounds = num(today.rounds);
  const usedItems = num(today.items);
  const maxRounds = Math.max(1, num(qf.rounds_per_day));
  const maxItems = Math.max(1, num(qf.rounds_per_day) * Math.max(1, num(qf.items_per_round)));

  const enabled = (st.enabled === undefined ? qf.enabled : st.enabled);

  const reports = learningReports(d.reports || []);

  $('#lBody').innerHTML =
    '<div class="card">' +
      '<div class="card-title">今日配额与使用量' +
        '<span class="title-extra">' + (enabled ? '已启用' : '已停用') + '</span></div>' +
      (st.last_error ? '<div class="row" style="margin-bottom:8px">' + pill('上次错误', 'bad') +
        '<span class="tiny" style="color:var(--bad);overflow-wrap:anywhere">' + esc(st.last_error) + '</span></div>' : '') +
      '<div class="bar-row" style="grid-template-columns:64px minmax(0,1fr) 72px">' +
        '<span class="bar-name">轮次</span>' +
        '<div class="bar-track"><i class="bar-fill" style="width:' +
          clamp((usedRounds / maxRounds) * 100, 0, 100) + '%"></i></div>' +
        '<span class="bar-val num">' + usedRounds + '/' + maxRounds + '</span>' +
      '</div>' +
      '<div class="bar-row" style="grid-template-columns:64px minmax(0,1fr) 72px">' +
        '<span class="bar-name">条目</span>' +
        '<div class="bar-track"><i class="bar-fill" style="width:' +
          clamp((usedItems / maxItems) * 100, 0, 100) + '%"></i></div>' +
        '<span class="bar-val num">' + usedItems + '/' + maxItems + '</span>' +
      '</div>' +
      '<div class="row tiny muted" style="margin-top:8px">' +
        '<span>上次运行 ' + esc(shortTs(st.last_run) || '—') + '</span>' +
        '<span class="spacer"></span>' +
        '<span>间隔 ' + num(qf.min_interval_minutes) + ' 分钟</span>' +
      '</div>' +
      (d.errors && d.errors.length
        ? '<div class="tiny" style="color:var(--warn);margin-top:6px">' + esc(d.errors.join('；')) + '</div>' : '') +
    '</div>' +

    '<div class="card">' +
      '<div class="card-title">每日配额<span class="title-extra">写回 webstudy.json</span></div>' +
      '<div class="quota-form">' +
        '<div class="field"><label>每日轮次</label>' +
          '<input type="number" id="qRounds" min="0" max="60" value="' + num(qf.rounds_per_day) + '"></div>' +
        '<div class="field"><label>每轮条目</label>' +
          '<input type="number" id="qItems" min="1" max="50" value="' + num(qf.items_per_round) + '"></div>' +
        '<div class="field"><label>最小间隔(分钟)</label>' +
          '<input type="number" id="qInterval" min="0" max="1440" value="' + num(qf.min_interval_minutes) + '"></div>' +
        '<div class="field"><label>启用</label>' +
          '<select id="qEnabled">' +
            '<option value="1"' + (qf.enabled ? ' selected' : '') + '>开</option>' +
            '<option value="0"' + (!qf.enabled ? ' selected' : '') + '>关</option>' +
          '</select></div>' +
      '</div>' +
      '<div class="row" style="margin-top:10px">' +
        '<button class="btn primary" id="qSave">保存配额</button>' +
        '<span class="spacer"></span>' +
        '<span class="tiny muted">其它键保持不变</span>' +
      '</div>' +
    '</div>' +

    '<div class="card">' +
      '<div class="card-title">最近学习报告<span class="title-extra">' +
        esc('当前实例的数据目录 / learning / reports') + '</span></div>' +
      (reports || emptyBox('暂无数据', 'reports 目录是空的')) +
    '</div>';

  wireLearningReports(root);
  $('#qSave').onclick = async () => {
    const body = {
      rounds_per_day: num($('#qRounds').value),
      items_per_round: num($('#qItems').value),
      min_interval_minutes: num($('#qInterval').value),
      enabled: $('#qEnabled').value === '1',
    };
    if (!window.confirm('写入 webstudy.json？\n轮次 ' + body.rounds_per_day +
        ' · 条目 ' + body.items_per_round + ' · 间隔 ' + body.min_interval_minutes + ' 分钟')) return;
    const btn = $('#qSave');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> 保存中';
    try {
      await api('/api/learning/quota', body);
      toast('配额已保存', 'ok');
      if (pageToken === app.token) viewLearningQuota(root);
    } catch (e) {
      toast('保存失败：' + e.message, 'err');
      btn.disabled = false;
      btn.textContent = '保存配额';
    }
  };
}

/* ────────────── 日记 ────────────── */

function localDayString(day) {
  return String(day.getFullYear()).padStart(4, '0') + '-' + pad2(day.getMonth() + 1) + '-' + pad2(day.getDate());
}

function parseLocalDay(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ''))) return null;
  const day = new Date(0);
  day.setHours(12, 0, 0, 0);
  day.setFullYear(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10)));
  return localDayString(day) === value ? day : null;
}

function recentDiaryDays(end = todayStr()) {
  const anchor = parseLocalDay(end) || new Date();
  return Array.from({length: 7}, (_, index) => {
    const day = new Date(anchor);
    day.setDate(anchor.getDate() - 6 + index);
    return localDayString(day);
  });
}

async function viewDiary(root, q) {
  const pageToken = app.token;
  const requestedKind = q.get('kind') || ui.diary.kind;
  const kind = ['daily', 'weekly', 'monthly'].includes(requestedKind) ? requestedKind : 'daily';
  const selDate = parseLocalDay(q.get('d')) ? q.get('d') : todayStr();
  const monthParam = q.get('m') || selDate.slice(0, 7);
  let year = Number(monthParam.slice(0, 4)), month = Number(monthParam.slice(5, 7));
  if (!/^\d{4}-\d{2}$/.test(monthParam) || year < 1 || month < 1 || month > 12) {
    year = new Date().getFullYear(); month = new Date().getMonth() + 1;
  }
  const stripDays = recentDiaryDays();
  let expanded = ui.diary.expanded;
  ui.diary.kind = kind;
  const kindLabels = {daily: '日', weekly: '周', monthly: '月'};
  const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
  let marks = new Set(), memMarks = new Set();

  function dayButton(ds, compact) {
    const day = parseLocalDay(ds);
    const classes = ['cal-cell'];
    if (marks.has(ds)) classes.push('has-diary');
    if (memMarks.has(ds)) classes.push('has-mem');
    if (ds === todayStr()) classes.push('today');
    if (ds === selDate) classes.push('sel');
    return '<button class="' + classes.join(' ') + '" data-date="' + ds + '" aria-label="' + ds +
      ' 星期' + weekdays[day.getDay()] + '" aria-pressed="' + (ds === selDate) + '">' +
      (compact ? '<span>' + Number(ds.slice(5, 7)) + '/' + Number(ds.slice(8, 10)) + '</span><small>周' + weekdays[day.getDay()] + '</small>' : Number(ds.slice(8, 10))) + '</button>';
  }
  root.innerHTML = pageHead('日记', '近七天 · 可展开日历选择日期') +
    '<div class="card" id="dCalendarCard"><div class="row diary-toolbar">' +
    '<div class="seg" id="dKind">' + ['daily', 'weekly', 'monthly'].map(k =>
      '<button data-k="' + k + '" class="' + (k === kind ? 'active' : '') + '">' + kindLabels[k] + '</button>').join('') + '</div>' +
    '<span class="tiny muted" id="dSelected">正在查看 ' + esc(selDate) + '</span><span class="spacer"></span><span class="tiny muted" id="dCount"></span></div>' +
    '<div class="diary-date-row"><div class="cal-grid cal-strip" id="dStrip">' + stripDays.map(ds => dayButton(ds, true)).join('') + '</div>' +
    '<button class="btn sm ghost calendar-expand" id="dExpand" aria-controls="dFullCalendar" aria-expanded="' + expanded + '">' +
    (expanded ? '收起日历 ▴' : '展开日历 ▾') + '</button></div>' +
    '<div id="dFullCalendar"' + (expanded ? '' : ' hidden') + '><div class="cal-head">' +
    '<button class="btn sm ghost" id="dPrev" aria-label="上个月">‹</button><span class="cal-month" id="dMonth">' + year + '年' + month + '月</span>' +
    '<button class="btn sm ghost" id="dNext" aria-label="下个月">›</button><span class="spacer"></span>' +
    '<input type="month" id="dMonthPick" aria-label="选择月份" min="0001-01" max="9999-12" value="' + year.toString().padStart(4, '0') + '-' + pad2(month) + '"></div>' +
    '<div class="cal-grid" id="dCal"></div><div class="row date-jump"><label class="tiny muted" for="dDatePick">跳到指定日期</label>' +
    '<input type="date" id="dDatePick" aria-label="选择任意日期" min="0001-01-01" max="9999-12-31" value="' + selDate + '"></div></div>' +
    '<div class="row diary-footer"><span class="tiny muted">' +
    '<i class="diary-dot"></i> 有日记 <i class="memory-dot"></i> 有记忆</span><span class="spacer"></span>' +
    '<button class="btn sm ghost" id="dToday">今天</button><button class="btn primary sm" id="dGen">补生成今天日记</button></div></div>' +
    '<div class="card" id="dBody">' + skeleton(4) + '</div>';

  const stripEl = $('#dStrip', root), calEl = $('#dCal', root), bodyEl = $('#dBody', root);
  function setQ(patch) {
    const params = new URLSearchParams(q.toString());
    Object.keys(patch).forEach(key => { if (patch[key] === null) params.delete(key); else params.set(key, patch[key]); });
    const nextHash = '#/diary?' + params.toString();
    if (location.hash !== nextHash) location.hash = nextHash;
  }
  function setExpanded(value) {
    expanded = value;
    ui.diary.expanded = value;
    $('#dFullCalendar', root).hidden = !value;
    const button = $('#dExpand', root);
    button.textContent = value ? '收起日历 ▴' : '展开日历 ▾';
    button.setAttribute('aria-expanded', String(value));
  }
  function selectDay(ds) {
    if (!parseLocalDay(ds)) return;
    setExpanded(false);
    setQ({d: ds, m: ds.slice(0, 7), kind: kind});
  }
  function wireDays(el) {
    $$('[data-date]', el).forEach(button => { button.onclick = () => selectDay(button.dataset.date); });
  }
  function renderCalendar() {
    stripEl.innerHTML = stripDays.map(ds => dayButton(ds, true)).join('');
    wireDays(stripEl);
    const first = parseLocalDay(year.toString().padStart(4, '0') + '-' + pad2(month) + '-01');
    const last = new Date(first); last.setMonth(month, 0);
    const offset = (first.getDay() + 6) % 7;
    let html = ['一', '二', '三', '四', '五', '六', '日'].map(d => '<div class="cal-dow">' + d + '</div>').join('');
    for (let index = 0; index < offset; index++) html += '<div class="cal-cell blank"></div>';
    for (let day = 1; day <= last.getDate(); day++) html += dayButton(year.toString().padStart(4, '0') + '-' + pad2(month) + '-' + pad2(day), false);
    calEl.innerHTML = html;
    wireDays(calEl);
  }
  renderCalendar();
  $('#dExpand', root).onclick = () => setExpanded(!expanded);
  $('#dKind', root).onclick = e => {
    const button = e.target.closest('button[data-k]');
    if (button) setQ({kind: button.dataset.k, d: selDate});
  };
  function moveMonth(delta) {
    let nextYear = year, nextMonth = month + delta;
    if (nextMonth < 1) { nextMonth = 12; nextYear--; }
    if (nextMonth > 12) { nextMonth = 1; nextYear++; }
    if (nextYear < 1 || nextYear > 9999) return;
    ui.diary.expanded = true;
    setQ({m: nextYear.toString().padStart(4, '0') + '-' + pad2(nextMonth), kind: kind});
  }
  $('#dPrev', root).onclick = () => moveMonth(-1);
  $('#dNext', root).onclick = () => moveMonth(1);
  $('#dMonthPick', root).onchange = e => {
    if (!/^\d{4}-\d{2}$/.test(e.target.value)) return;
    ui.diary.expanded = true;
    setQ({m: e.target.value, kind: kind});
  };
  $('#dDatePick', root).onchange = e => selectDay(e.target.value);
  $('#dToday', root).onclick = () => selectDay(todayStr());
  $('#dGen', root).onclick = async () => {
    if (!window.confirm('补生成今天的日记？\n会调用一次模型，约 ¥0.002–0.01。')) return;
    const button = $('#dGen', root);
    button.disabled = true; button.innerHTML = '<span class="spin"></span> 生成中';
    try {
      const result = await api('/api/diary/generate', {kind: 'daily', date: todayStr(), force: false});
      toast(result.skipped ? '今天已经有了，跳过生成' : '日记已生成', 'ok');
      if (pageToken !== app.token) return;
      ui.diary.expanded = false;
      const nextHash = '#/diary?kind=daily&d=' + todayStr();
      if (location.hash === nextHash) await route(); else location.hash = nextHash;
    } catch (e) { toast('生成失败：' + e.message, 'err'); }
    finally { button.disabled = false; button.textContent = '补生成今天日记'; }
  };

  async function loadCalendar() {
    const primaryMonth = year.toString().padStart(4, '0') + '-' + pad2(month);
    const months = Array.from(new Set([primaryMonth].concat(stripDays.map(ds => ds.slice(0, 7)))));
    const results = await Promise.all(months.map(async ym => {
      try {
        return {month: ym, data: await api('/api/diary/calendar?kind=' + kind + '&year=' + Number(ym.slice(0, 4)) + '&month=' + Number(ym.slice(5, 7)))};
      } catch (e) { return {month: ym, error: e.message}; }
    }));
    if (pageToken !== app.token) return;
    results.forEach(result => {
      if (result.data) {
        (result.data.marks || []).forEach(ds => marks.add(ds));
        (result.data.memory_marks || []).forEach(ds => memMarks.add(ds));
      }
    });
    const current = results.find(result => result.month === primaryMonth);
    $('#dCount', root).textContent = current.error ? '日期标记暂不可用' : current.data.offline ? '日记服务离线' :
      month + '月 · ' + num(current.data.count) + ' 篇';
    renderCalendar();
  }
  async function loadBody() {
    try {
      const data = await api('/api/diary/get?kind=' + kind + '&date=' + encodeURIComponent(selDate));
      if (pageToken !== app.token) return;
      if (!data.found) {
        bodyEl.innerHTML = '<div class="card-title">' + esc(selDate) + '</div>' + emptyBox('暂无日记', data.error || '这天还没有日记');
      } else {
        bodyEl.innerHTML = '<div class="card-title">' + esc(data.title || data.date) +
          '<span class="title-extra">' + num(data.chars) + ' 字</span></div><div class="row tiny muted" style="margin-bottom:9px">' +
          (data.mood ? pill(data.mood, 'primary') : '') + (data.source ? pill(data.source) : '') +
          '<span>' + esc(shortTs(data.generated_at)) + '</span></div><div class="diary-body">' + esc(data.body) + '</div>';
      }
    } catch (e) {
      if (pageToken === app.token) bodyEl.innerHTML = '<div class="card-title">' + esc(selDate) + '</div>' + emptyBox('读取失败', e.message);
    }
  }
  await Promise.all([loadCalendar(), loadBody()]);
}

/* ────────────── 情绪 ────────────── */

async function viewEmotion(root, q) {
  return viewEmotionDashboard(root, q);
}

/* ────────────── 花费 ────────────── */

async function viewCost(root, q) {
  const range = q.get('r') || ui.cost.range || '7d';
  ui.cost.range = range;
  const labels = { today: '今日', '7d': '近 7 天', '30d': '本月', all: '全部' };

  root.innerHTML =
    pageHead('花费', 'cost_event 聚合') +
    '<div class="row" style="margin-bottom:11px">' +
      '<div class="seg" id="cRange">' +
        ['today', '7d', '30d', 'all'].map((r) =>
          '<button data-r="' + r + '" class="' + (r === range ? 'active' : '') + '">' +
          labels[r] + '</button>').join('') +
      '</div>' +
    '</div>' +
    '<div id="cBody">' + skeleton(5) + '</div>';

  $('#cRange').onclick = (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const cur = parseHash();
    const nq = new URLSearchParams(cur.q.toString());
    nq.set('r', b.dataset.r);
    location.hash = '#/' + cur.name + '?' + nq.toString();
  };

  let d;
  try {
    d = await api('/api/cost?range=' + range);
  } catch (e) {
    $('#cBody').innerHTML = errorCard(e);
    return;
  }

  const byDay = d.by_day || [];
  const byTag = d.by_tag || [];

  // 曲线图补齐区间内没有花费的日期，避免只有一天数据时只剩一个点
  let chartByDay = byDay;
  if (byDay.length && range !== 'all') {
    const byKey = new Map(byDay.map((r) => [r.day, r]));
    const n = range === 'today' ? 1 : range === '7d' ? 7 : 30;
    const end = new Date();
    const days = [];
    for (let i = n - 1; i >= 0; i -= 1) {
      const dt = new Date(end.getFullYear(), end.getMonth(), end.getDate() - i);
      const key = dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
      days.push(byKey.get(key) || { day: key, events: 0, cost: 0, in_tokens: 0, out_tokens: 0 });
    }
    chartByDay = days;
  }

  const dayRows = byDay.slice().reverse().map((r) =>
    '<tr>' +
      '<td class="mono">' + esc(r.day) + '</td>' +
      '<td class="num">' + num(r.events) + '</td>' +
      '<td class="num">¥' + fmtCost(r.cost) + '</td>' +
      '<td class="num muted">' + fmtInt(num(r.in_tokens) + num(r.out_tokens)) + '</td>' +
    '</tr>').join('');

  const tagRows = byTag.map((r) =>
    '<tr>' +
      '<td>' + esc(r.tag) + '</td>' +
      '<td class="num">' + num(r.events) + '</td>' +
      '<td class="num">¥' + fmtCost(r.cost) + '</td>' +
    '</tr>').join('');

  $('#cBody').innerHTML =
    '<div class="card">' +
      '<div class="card-title">' + esc(labels[range]) + '合计' +
        '<span class="title-extra">' + num(d.events) + ' 次调用</span></div>' +
      '<div class="big-num">¥' + fmtCost(d.total) + '</div>' +
      '<div class="row tiny muted" style="margin-top:5px">' +
        '<span>' + (d.start ? ('起始 ' + esc(d.start)) : '全部时间') + '</span>' +
      '</div>' +
    '</div>' +
    '<div class="card" id="cDouyinUsage">' +
      '<div class="card-title">抖音专用模型<span class="title-extra">' + num(d.douyin?.calls) + ' 次请求</span></div>' +
      '<p>' + fmtInt(num(d.douyin?.tokens)) + ' tokens · 供应商 credit ' + Number(d.douyin?.credit || 0).toFixed(4) + '</p>' +
      '<p class="tiny muted">这是当前时间范围的专用接口用量，未知请求保留预算预留。credit 没有可靠的人民币换算，未并入上方人民币金额。</p>' +
      '<a href="#/douyin">查看小鱼的抖音日常与预算</a>' +
    '</div>' +
    '<div class="card">' +
      '<div class="card-title">按天<span class="title-extra">曲线图</span></div>' +
      '<canvas class="chart-canvas" id="cChart"></canvas>' +
    '</div>' +
    '<div class="card">' +
      '<div class="card-title">按天明细</div>' +
      (byDay.length
        ? '<div class="table-wrap"><table class="tbl"><thead><tr>' +
          '<th>日期</th><th class="num">次数</th><th class="num">金额</th><th class="num">tokens</th>' +
          '</tr></thead><tbody>' + dayRows + '</tbody></table></div>'
        : emptyBox('暂无数据', '这个区间没有花费记录')) +
    '</div>' +
    '<div class="card">' +
      '<div class="card-title">按功能<span class="title-extra">feature_tag</span></div>' +
      (byTag.length
        ? '<div class="table-wrap"><table class="tbl"><thead><tr>' +
          '<th>feature_tag</th><th class="num">次数</th><th class="num">金额</th>' +
          '</tr></thead><tbody>' + tagRows + '</tbody></table></div>'
        : emptyBox('暂无数据')) +
    '</div>';

  const canvas = $('#cChart');
  function redraw() {
    if (!canvas.isConnected) return;
    drawCurve(canvas, chartByDay.map((r) => ({ label: r.day, value: r.cost })),
      { labels: chartByDay.map((r) => String(r.day).slice(5)), fmt: (v) => '¥' + fmtCost(v) });
  }
  redraw();
  onWindowResize(redraw);
}

/* ────────────── 碎碎念 ────────────── */

async function viewChitchat(root) {
  root.innerHTML = pageHead('碎碎念', 'chitchat_log · 最近 30 条') + '<div class="card" id="ccBody">' +
    skeleton(5) + '</div>';
  try {
    const d = await api('/api/chitchat');
    const items = d.items || [];
    $('#ccBody').innerHTML = '<div class="card-title">碎碎念' +
      '<span class="title-extra">' + items.length + ' 条</span></div>' +
      (items.length ? '<div class="list">' + items.map((it) =>
        '<div class="list-item">' +
          '<div style="font-size:13px;overflow-wrap:anywhere">' + esc(it.content) + '</div>' +
          '<div class="mem-meta">' +
            '<span class="tiny muted">' + esc(shortTs(it.t)) + '</span>' +
            (it.emotion ? pill(it.emotion, 'dim') : '') +
            (it.replied ? pill('已回复', 'ok') : pill('未回复', 'warn')) +
          '</div>' +
        '</div>').join('') + '</div>' : emptyBox('暂无数据'));
  } catch (e) {
    $('#ccBody').innerHTML = '<div class="card-title">碎碎念</div>' + emptyBox('暂无数据', e.message);
  }
}

/* ────────────── 拒绝权 ────────────── */

async function viewRefusal(root) {
  root.innerHTML = pageHead('拒绝权', 'refusal_decision · 最近 30 条') + '<div class="card" id="rBody">' +
    skeleton(5) + '</div>';
  try {
    const d = await api('/api/refusal');
    const items = d.items || [];
    $('#rBody').innerHTML = '<div class="card-title">拒绝决策' +
      '<span class="title-extra">' + items.length + ' 条</span></div>' +
      (items.length ? '<div class="list">' + items.map((it) => {
        const sig = it.signals || {};
        const sigHtml = Object.keys(sig).map((k) => pill(k + ' ' + num(sig[k]).toFixed(2), 'dim')).join(' ');
        return '<div class="list-item">' +
          '<div class="row-between" style="gap:6px">' +
            '<span style="font-size:13px;overflow-wrap:anywhere">' + esc(it.request) + '</span>' +
            pill(it.decision || '—', it.decision === 'REFUSE' ? 'bad' : 'ok') +
          '</div>' +
          (it.reason ? '<div class="tiny muted" style="margin-top:3px">“' + esc(it.reason) + '”</div>' : '') +
          '<div class="mem-meta">' +
            '<span class="tiny muted">' + esc(shortTs(it.t)) + '</span>' +
            pill('倾向 ' + num(it.propensity).toFixed(2) + ' / 阈 ' + num(it.threshold).toFixed(2), 'dim') +
            (it.style ? pill(it.style, 'dim') : '') +
          '</div>' +
          '<div class="mem-meta">' + sigHtml + '</div>' +
        '</div>';
      }).join('') + '</div>' : emptyBox('暂无数据'));
  } catch (e) {
    $('#rBody').innerHTML = '<div class="card-title">拒绝决策</div>' + emptyBox('暂无数据', e.message);
  }
}

/* ────────────── 审计 ────────────── */

async function viewAudit(root) {
  root.innerHTML = pageHead('审计', 'audit_event · 最近 30 条') + '<div class="card" id="aBody">' +
    skeleton(5) + '</div>';
  try {
    const d = await api('/api/audit');
    const items = d.items || [];
    $('#aBody').innerHTML = '<div class="card-title">审计事件' +
      '<span class="title-extra">' + items.length + ' 条</span></div>' +
      (items.length ? '<div class="list">' + items.map((it) =>
        '<div class="list-item">' +
          '<div class="row-between" style="gap:6px">' +
            '<span class="tiny mono muted" style="overflow-wrap:anywhere">' + esc(it.entity_id || it.id) + '</span>' +
            pill(it.action || '—', 'primary') +
          '</div>' +
          '<div class="tiny muted" style="margin-top:2px;overflow-wrap:anywhere">' + esc(it.detail) + '</div>' +
          '<div class="mem-meta">' +
            '<span class="tiny muted">' + esc(shortTs(it.t)) + '</span>' +
            pill(it.entity_kind || '—', 'dim') +
          '</div>' +
        '</div>').join('') + '</div>' : emptyBox('暂无数据'));
  } catch (e) {
    $('#aBody').innerHTML = '<div class="card-title">审计事件</div>' + emptyBox('暂无数据', e.message);
  }
}

/* ────────────── QQ 接入 ────────────── */

async function viewQQ(root) {
  root.innerHTML = pageHead('QQ 接入', 'SnowLuma · 桥接控制台') + '<div id="qBody">' + skeleton(5) + '</div>';
  let d;
  try {
    d = await api('/api/qq');
  } catch (e) {
    $('#qBody').innerHTML = errorCard(e);
    return;
  }

  const login = d.login;
  const convs = d.states || [];
  const errs = d.errors || [];

  const loginHtml = login
    ? '<div class="row">' + pill('在线', 'ok') +
      '<span class="mono">' + esc(login.user_id) + '</span>' +
      '<span>' + esc(login.nickname || '') + '</span></div>'
    : emptyBox('暂无数据', '拿不到 get_login_info');

  const convHtml = convs.length ? convs.map((c) => {
    const tr = c.triggers || {};
    const kws = (tr.keywords || []).map((k) => '<span class="pill">' + esc(k) + '</span>').join('');
    return '<div class="conv">' +
      '<div class="row-between" style="gap:6px">' +
        '<span class="conv-key">' + esc(c.key) + '</span>' +
        pill(c.mode || '—', c.mode === 'diving' ? 'primary' : '') +
      '</div>' +
      '<div class="row tiny muted" style="margin-top:4px">' +
        '<span>唤醒 ' + num(c.wake_count) + ' 次</span>' +
        '<span>· 最近 ' + esc(c.last_ai_reply || '—') + '</span>' +
        '<span class="spacer"></span>' +
        (c.infinite ? pill('无限', 'warn') : '') +
        (num(c.unread) > 0 ? pill('未读 ' + c.unread, 'ok') : '') +
      '</div>' +
      '<div class="row tiny muted" style="margin-top:3px">' +
        '<span>触发：' +
          [tr.any_message ? '任意消息' : '', tr.at_mention ? '@' : '',
           tr.question ? '问句' : '', tr.poke ? '戳一戳' : '']
            .filter(Boolean).join(' / ') +
          ' · 概率 ' + num(tr.probability).toFixed(2) +
        '</span>' +
      '</div>' +
      (kws ? '<div class="kw-list">' + kws + '</div>' : '') +
    '</div>';
  }).join('') : emptyBox('暂无数据', 'conversations 是空的');

  $('#qBody').innerHTML =
    '<div class="card">' +
      '<div class="card-title">SnowLuma 登录<span class="title-extra">127.0.0.1:3000</span></div>' +
      loginHtml +
    '</div>' +
    '<div class="card">' +
      '<div class="card-title">会话与唤醒<span class="title-extra">' + convs.length + ' 个</span></div>' +
      convHtml +
    '</div>' +
    (errs.length ? '<div class="card"><div class="card-title">提示</div>' +
      '<div class="list">' + errs.map((e) => '<div class="list-item tiny muted">' +
        esc(e) + '</div>').join('') + '</div></div>' : '');
}

/* ══════════════════════════════════════════════════════════
   路由
   ══════════════════════════════════════════════════════════ */

const NAV = [
  { id: 'welcome', label: '入住与模型', render: root => window.XiaoyuRelease.welcome(root) },
  { id: 'chat', label: '聊天', render: root => window.XiaoyuRelease.chat(root) },
  { id: 'migration', label: '迁入与备份', render: root => window.XiaoyuRelease.migration(root) },
  { id: 'connections', label: '接入与组件', render: root => window.XiaoyuRelease.connections(root) },
  { id: 'models', label: '模型与费用', render: root => window.XiaoyuRelease.models(root) },
  { id: 'home', label: '首页', render: viewHome },
  { id: 'thinking', label: '思考', render: viewThinking },
  { id: 'persona', label: '人设', render: viewPersona },
  { id: 'voice', label: '语音', render: viewVoice },
  { id: 'contacts', label: '联系人', render: viewContacts },
  { id: 'qzone', label: '空间', render: viewQzone },
  { id: 'douyin', label: '抖音', render: (root, q) => window.XiaoyuDouyin.mount(root, q) },
  { id: 'memory', label: '记忆', render: viewMemory },
  { id: 'learning', label: '学习', render: viewLearning },
  { id: 'diary', label: '日记', render: viewDiary },
  { id: 'emotion', label: '情绪', render: viewEmotion },
  { id: 'cost', label: '花费', render: viewCost },
  { id: 'chitchat', label: '碎碎念', render: viewChitchat },
  { id: 'refusal', label: '拒绝权', render: viewRefusal },
  { id: 'qq', label: 'QQ 接入', render: viewQQ },
  { id: 'settings', label: '设置', render: (root, q) => window.XiaoyuSettings.mount(root, q), bottom: true },
];

function parseHash() {
  const raw = (location.hash || '').replace(/^#\/?/, '');
  const qi = raw.indexOf('?');
  const name = (qi < 0 ? raw : raw.slice(0, qi)) || 'home';
  const qs = qi < 0 ? '' : raw.slice(qi + 1);
  return { name: name, q: new URLSearchParams(qs) };
}

function buildNav() {
  const list = $('#navList');
  const groups = { home: '日常与陪伴', memory: '记忆与成长', cost: '小屋里的其他事' };
  list.innerHTML = NAV.filter(n => !n.bottom).map((n) =>
    (groups[n.id] ? '<div class="nav-group" aria-hidden="true">' + groups[n.id] + '</div>' : '') +
    '<button class="nav-item" data-id="' + n.id + '">' + roomIcon(n.id) + '<span class="nav-label">' + esc(n.label) + '</span></button>').join('');
  $('#settingsNav').innerHTML = '<button class="nav-item" data-id="settings">' + roomIcon('settings') + '<span class="nav-label">设置</span></button>';
  $$('.nav-item', $('#nav')).forEach((b) => {
    b.onclick = () => { location.hash = '#/' + b.dataset.id; };
  });
}

async function route() {
  if (app.cancelledHash === location.hash) { app.cancelledHash = null; return; }
  const { name, q } = parseHash();
  const item = NAV.find((n) => n.id === name) || NAV[0];

  if (app.leave) {
    const leave = app.leave;
    let ok = true;
    try { ok = app.leave(); } catch (_) { ok = true; }
    app.leave = null;
    if (ok === false) {
      app.leave = leave;
      // 用户取消：把 hash 还原到上一个视图
      if (app.prevHash && location.hash !== app.prevHash) {
        app.cancelledHash = app.prevHash;
        location.hash = app.prevHash;
      }
      return;
    }
  }

  app.prevHash = location.hash || '#/home';
  const token = ++app.token;

  clearTimers();
  disposeAll();
  closeModal();

  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.id === item.id));
  // 窄屏顶部导航是横向滚动的：只滚 nav-list 自己。
  // （不要用 scrollIntoView —— 它会把整个文档也横向推偏，页面就多出一条横向溢出。）
  const activeBtn = $('.nav-item.active');
  const navList = $('#navList');
  if (activeBtn && navList && navList.contains(activeBtn) && navList.scrollWidth > navList.clientWidth + 1) {
    const lr = navList.getBoundingClientRect();
    const br = activeBtn.getBoundingClientRect();
    const delta = (br.left + br.width / 2) - (lr.left + lr.width / 2);
    const target = Math.max(0, navList.scrollLeft + delta);
    if (navList.scrollTo) {
      try { navList.scrollTo({ left: target, behavior: 'smooth' }); }
      catch (_) { navList.scrollLeft = target; }
    } else {
      navList.scrollLeft = target;
    }
  }
  const root = $('#view');
  root.innerHTML = skeleton(3);
  document.title = '小鱼之家 · ' + item.label;

  try {
    await item.render(root, q);
  } catch (e) {
    if (token === app.token) {
      root.innerHTML = pageHead(item.label) + errorCard(e);
    }
  }
  window.scrollTo(0, 0);
}

/* ────────────────────── 启动 ────────────────────── */

function startClock() {
  const el = $('#brandSub');
  function tick() {
    const d = new Date();
    if (el) el.textContent = pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }
  tick();
  setInterval(tick, 20000);
}

function boot() {
  buildNav();
  window.XiaoyuSettings.start();
  window.XiaoyuRoom.start();
  startClock();
  window.addEventListener('hashchange', route);
  if (!location.hash) location.hash = '#/welcome';
  else route();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
