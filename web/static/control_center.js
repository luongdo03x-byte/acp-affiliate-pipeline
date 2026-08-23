(() => {
  const root = document.querySelector('[data-enrich-all-progress]');
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  if (!statusUrl) return;

  const text = (selector, value) => {
    const node = root.querySelector(selector);
    if (node) node.textContent = String(value ?? '');
  };

  const apply = (payload) => {
    const percent = Math.max(0, Math.min(100, Number(payload.percent || 0)));
    const bar = root.querySelector('[data-enrich-bar]');
    if (bar) bar.style.width = `${percent}%`;
    const progress = root.querySelector('[role="progressbar"]');
    if (progress) progress.setAttribute('aria-valuenow', String(percent));

    text('[data-enrich-state]', payload.state || 'IDLE');
    text('[data-enrich-processed]', payload.processed || 0);
    text('[data-enrich-total]', payload.total || 0);
    text('[data-enrich-percent]', `${percent}%`);
    text('[data-enrich-ready]', payload.ready || 0);
    text('[data-enrich-pending]', payload.pending || 0);
    text('[data-enrich-working]', payload.working || 0);
    text('[data-enrich-helper]', payload.needs_helper || 0);
    text('[data-enrich-failed]', payload.failed || 0);
  };

  let timer = null;
  const poll = async () => {
    try {
      const response = await fetch(statusUrl, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      apply(await response.json());
    } catch (_) {
      // Progress polling is best-effort. Normal form actions and page refresh
      // remain authoritative if the browser is offline or the worker restarts.
    }
  };

  poll();
  timer = window.setInterval(poll, 5000);
  window.addEventListener('pagehide', () => {
    if (timer !== null) window.clearInterval(timer);
  }, { once: true });
})();
