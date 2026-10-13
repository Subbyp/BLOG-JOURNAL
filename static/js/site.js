// Theme toggle and navigation
(function () {
  const root = document.documentElement;
  const toggle = document.querySelector('[data-theme-toggle]');
  const navToggle = document.querySelector('[data-nav-toggle]');
  const nav = document.getElementById('primary-nav');

  function getTheme() {
    return root.getAttribute('data-theme') || 'light';
  }
  function setTheme(theme) {
    root.setAttribute('data-theme', theme);
    try { localStorage.setItem('theme', theme); } catch (e) {}
    if (toggle) {
      toggle.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
    }
  }
  if (toggle) {
    toggle.addEventListener('click', () => {
      const next = getTheme() === 'dark' ? 'light' : 'dark';
      setTheme(next);
    });
  }
  setTheme(getTheme());

  if (navToggle && nav) {
    navToggle.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }
})();

// Destructive actions ask first. Implemented here rather than with inline
// onsubmit handlers so the page needs no inline script.
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const message = form.getAttribute('data-confirm');
  if (message && !window.confirm(message)) event.preventDefault();
});

// Copy helper
document.querySelectorAll('[data-copy]').forEach((el) => {
  el.addEventListener('click', async () => {
    const text = el.dataset.copy || '';
    try {
      await navigator.clipboard.writeText(text);
      const old = el.textContent;
      el.textContent = 'Copied!';
      setTimeout(() => (el.textContent = old), 1200);
    } catch (e) {}
  });
});
