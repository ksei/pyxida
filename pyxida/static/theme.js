// Theme: applies the saved choice before the page paints (loaded without defer),
// then wires the top-bar switch. "auto" follows the operating system.
(function () {
  const KEY = 'pyxida-theme';
  const MODES = ['auto', 'light', 'dark'];
  const LABELS = { auto: '◐ Auto', light: '☀ Light', dark: '☾ Dark' };
  const TITLES = {
    auto: 'Theme follows your system. Click for light.',
    light: 'Light theme. Click for dark.',
    dark: 'Dark theme. Click to follow your system.',
  };

  function read() {
    try { return MODES.includes(localStorage.getItem(KEY)) ? localStorage.getItem(KEY) : 'auto'; } catch { return 'auto'; }
  }
  function apply(mode) {
    if (mode === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = mode;
  }

  apply(read());

  document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('theme-toggle');
    if (!button) return;
    const show = (mode) => {
      button.textContent = LABELS[mode];
      button.title = TITLES[mode];
      button.setAttribute('aria-label', TITLES[mode]);
    };
    show(read());
    button.addEventListener('click', () => {
      const next = MODES[(MODES.indexOf(read()) + 1) % MODES.length];
      try { localStorage.setItem(KEY, next); } catch { /* private mode: applies to this page only */ }
      apply(next);
      show(next);
    });
  });
})();
