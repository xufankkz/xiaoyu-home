/* Presentation preferences only. The server remains the source of saved settings. */
(function () {
  'use strict';
  const catalog = Object.freeze([
    { id: 'xiaoyu-room', name: '日常小屋', note: '熟悉的浅蓝房间，清爽舒展', layout: '经典侧栏', scheme: 'light', color: '#f6f8fc' },
    { id: 'sunlit-journal', name: '晴日手账', note: '奶油纸色与暖棕，像翻开她的手账', layout: '手账双列', scheme: 'light', color: '#f6f1e8' },
    { id: 'forest-cottage', name: '林间小屋', note: '鼠尾草绿与自然留白，安静地生活', layout: '小屋分栏', scheme: 'light', color: '#f1f5f0' },
    { id: 'deepsea-night', name: '窗边小屋', note: '窗户、书桌和一盏小灯，小鱼就在自己的房间里', layout: '角色房间 · 书桌与日常', scheme: 'light', color: '#f5f7fc' }
  ].map(Object.freeze));
  const resolve = id => catalog.find(theme => theme.id === id) || catalog[0];
  const root = document.documentElement;
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)');
  const colorModes = Object.freeze({ light: '浅色', dark: '夜间', system: '跟随系统' });
  const nightPalettes = Object.freeze({ charcoal: '深灰＋原淡紫', 'sky-blue': '深灰＋晴蓝 · #4ea2f7', 'slate-blue': '深灰＋暮蓝 · #293044' });
  const nightColors = Object.freeze({ charcoal: '#222222', 'sky-blue': '#222222', 'slate-blue': '#222222' });
  function apply(id, remember = false, mode = root.dataset.colorMode || 'light', palette = root.dataset.nightPalette || 'charcoal') {
    const theme = resolve(id);
    const colorMode = Object.hasOwn(colorModes, mode) ? mode : 'light';
    const nightPalette = Object.hasOwn(nightPalettes, palette) ? palette : 'charcoal';
    const scheme = colorMode === 'system' ? (systemDark.matches ? 'dark' : 'light') : colorMode;
    const previous = [root.dataset.theme, root.dataset.colorMode, root.dataset.scheme, root.dataset.nightPalette].join(':');
    root.dataset.theme = theme.id;
    root.dataset.colorMode = colorMode;
    root.dataset.scheme = scheme;
    root.dataset.nightPalette = nightPalette;
    root.style.colorScheme = scheme;
    if (document.body) Object.assign(document.body.dataset, { theme: theme.id, colorMode, scheme, nightPalette });
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', scheme === 'dark' ? nightColors[nightPalette] : theme.color);
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', scheme);
    if (remember) {
      try {
        localStorage.setItem('xiaoyu.saved-theme', theme.id);
        localStorage.setItem('xiaoyu.saved-color-mode', colorMode);
        localStorage.setItem('xiaoyu.saved-night-palette', nightPalette);
      } catch (_) { /* Storage may be unavailable. */ }
    }
    if (previous !== [theme.id, colorMode, scheme, nightPalette].join(':'))
      window.dispatchEvent(new CustomEvent('xiaoyu:appearance', { detail: { theme: theme.id, colorMode, scheme, nightPalette } }));
    return theme.id;
  }
  window.XiaoyuThemes = Object.freeze({ catalog, resolve, apply, colorModes, nightPalettes });
  // The server embeds saved preferences before the first paint. Cached values are
  // only a fallback when opening a static preview without server placeholders.
  let initial = root.dataset.theme, mode = root.dataset.colorMode, palette = root.dataset.nightPalette;
  try {
    if (!catalog.some(theme => theme.id === initial)) initial = localStorage.getItem('xiaoyu.saved-theme');
    if (!Object.hasOwn(colorModes, mode)) mode = localStorage.getItem('xiaoyu.saved-color-mode');
    if (!Object.hasOwn(nightPalettes, palette)) palette = localStorage.getItem('xiaoyu.saved-night-palette');
  } catch (_) {}
  apply(initial, false, mode, palette);
  document.addEventListener('DOMContentLoaded', () => apply(root.dataset.theme, false, root.dataset.colorMode), { once: true });
  systemDark.addEventListener('change', () => {
    if (root.dataset.colorMode === 'system') apply(root.dataset.theme, false, 'system');
  });
})();
