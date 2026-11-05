/* Local native bridge. Ordinary browser previews keep the isolated demo. */
(() => {
  const enabled = new URLSearchParams(location.search).get('native') === '1';
  let timer = null;
  let inFlight = false;
  let signature = '';
  const bridge = { enabled, ready: false, info: null, error: '' };
  window.HYOL_NATIVE = bridge;
  bridge.call = async (method, ...args) => {
    const api = window.pywebview?.api;
    if (!api || typeof api[method] !== 'function') throw new Error('本地运行层尚未连接，请稍后重试。');
    return await api[method](...args);
  };
  bridge.refresh = async (force = false) => {
    if (!enabled || inFlight) return;
    inFlight = true;
    try {
      const info = await bridge.call('get_state');
      bridge.ready = true;
      bridge.info = info;
      bridge.error = '';
      const next = JSON.stringify(info);
      if (force || next !== signature) {
        signature = next;
        window.dispatchEvent(new CustomEvent('hyol-native-state', { detail: info }));
      }
    } catch (error) {
      bridge.error = error.message || String(error);
      window.dispatchEvent(new CustomEvent('hyol-native-error', { detail: bridge.error }));
    } finally {
      inFlight = false;
    }
  };
  if (!enabled) return;
  const poll = async () => {
    await bridge.refresh();
    timer = setTimeout(poll, 900);
  };
  window.addEventListener('pywebviewready', () => bridge.refresh(true));
  window.addEventListener('pagehide', () => clearTimeout(timer), { once: true });
  document.addEventListener('DOMContentLoaded', () => {
    document.title = '火影 OL · 本轮任务控制台';
    document.documentElement.dataset.runtime = 'native';
    const actions = document.querySelector('.demo-actions');
    if (actions) actions.style.display = 'none';
    const badge = document.querySelector('.demo-badge');
    if (badge) badge.textContent = '本地任务运行';
    const note = document.querySelector('.demo-notice-text');
    if (note) note.textContent = '正在连接本地运行层；点击开始后才执行所选任务';
    const connection = document.querySelector('.connection-card');
    if (connection) connection.innerHTML = '<span class="connection-dot"></span><div><strong>本地桌面客户端</strong><span>等待运行层连接</span></div>';
    poll();
  });
})();
