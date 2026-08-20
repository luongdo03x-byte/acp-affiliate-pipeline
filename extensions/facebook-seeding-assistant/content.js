(() => {
  const parser = globalThis.ACPSeedingParser;
  const runner = globalThis.ACPSeedingRunner;
  if (!parser || !runner) return;

  const PANEL_ID = 'acp-facebook-seeding-panel';
  let running = false;
  let idleTimer = null;
  let lastFacebookComposer = null;

  document.addEventListener('focusin', (event) => {
    const target = event && event.target;
    const currentPanel = document.getElementById(PANEL_ID);
    if (!target || (currentPanel && currentPanel.contains(target))) return;
    const candidate = parser.findFocusedComposer({ activeElement: target });
    if (candidate) lastFacebookComposer = candidate;
  }, true);

  function bridge(message) {
    return new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));
  }

  async function api(path, { method = 'GET', body } = {}) {
    const response = await bridge({ type: 'ACP_API', path, method, body });
    if (!response || !response.ok) {
      const error = response && response.data && response.data.error
        ? response.data.error
        : 'ACP request failed';
      throw new Error(error);
    }
    return response.data;
  }

  function panel() {
    let node = document.getElementById(PANEL_ID);
    if (node) return node;
    node = document.createElement('aside');
    node.id = PANEL_ID;
    node.style.cssText = [
      'position:fixed', 'right:16px', 'bottom:16px', 'z-index:2147483647',
      'width:380px', 'max-height:78vh', 'overflow:auto', 'padding:14px',
      'border-radius:12px', 'background:#0d1b2a', 'color:#f8fafc',
      'border:1px solid rgba(148,163,184,.28)', 'box-shadow:0 18px 48px rgba(0,0,0,.35)',
      'font:13px/1.45 system-ui,sans-serif',
    ].join(';');
    document.documentElement.appendChild(node);
    return node;
  }

  function escapeHtml(value) {
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }

  function setPanel(title, bodyHtml, tone = '') {
    const node = panel();
    const border = tone === 'danger' ? '#ef4444' : tone === 'ok' ? '#22c55e' : '#8b5cf6';
    node.style.borderColor = border;
    node.innerHTML = `<div style="font-weight:800;margin-bottom:8px">ACP · ${escapeHtml(title)}</div>${bodyHtml}`;
    return node;
  }

  function button(label, id, danger = false) {
    return `<button id="${id}" style="margin:6px 6px 0 0;padding:7px 10px;border-radius:8px;border:1px solid ${danger ? '#ef4444' : '#64748b'};background:${danger ? '#3f1118' : '#162a40'};color:#fff;cursor:pointer">${escapeHtml(label)}</button>`;
  }

  function scheduleIdlePoll() {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      idleTimer = null;
      running = false;
      run().catch(showFatal);
    }, 15000);
  }

  async function showConfig() {
    const configResult = await bridge({ type: 'ACP_GET_CONFIG' });
    const config = (configResult && configResult.data) || {};
    const node = setPanel('Kết nối tài khoản', `
      <div style="margin-bottom:6px">Tên/nhãn account Facebook</div>
      <input id="acp-seed-account-label" value="${escapeHtml(config.accountLabel || '')}" placeholder="FB01 hoặc tên dễ nhận biết" style="width:100%;box-sizing:border-box;margin-bottom:8px;padding:7px">
      <div style="margin-bottom:6px">ACP local URL</div>
      <input id="acp-seed-base" value="${escapeHtml(config.acpBaseUrl || 'http://127.0.0.1:5000')}" style="width:100%;box-sizing:border-box;margin-bottom:8px;padding:7px">
      <div style="margin-bottom:6px">ACP_SEEDING_EXTENSION_TOKEN</div>
      <input id="acp-seed-token" type="password" value="${escapeHtml(config.seedingToken || '')}" style="width:100%;box-sizing:border-box;padding:7px">
      <div style="margin-top:8px;color:#94a3b8">Profile ID: ${escapeHtml(config.extensionInstanceId || 'sẽ tạo khi lưu')}</div>
      ${button('Lưu & kết nối', 'acp-seed-save')}
    `);
    node.querySelector('#acp-seed-save').addEventListener('click', async () => {
      const response = await bridge({
        type: 'ACP_SET_CONFIG',
        config: {
          acpBaseUrl: node.querySelector('#acp-seed-base').value,
          seedingToken: node.querySelector('#acp-seed-token').value,
          accountLabel: node.querySelector('#acp-seed-account-label').value,
        },
      });
      if (!response || !response.ok || (response.pairing && !response.pairing.ok)) {
        const error = response && response.pairing && response.pairing.data
          ? response.pairing.data.error
          : 'Không kết nối được account với ACP';
        return showFatal(new Error(error));
      }
      running = false;
      run().catch(showFatal);
    });
  }

  function showFatal(error) {
    running = false;
    const node = setPanel(
      'DỪNG',
      `<div>${escapeHtml(error && error.message ? error.message : error)}</div>${button('Cấu hình', 'acp-seed-config')}`,
      'danger',
    );
    node.querySelector('#acp-seed-config').addEventListener('click', showConfig);
  }

  function setComposerText(composer, text) {
    composer.focus();
    try {
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, text);
    } catch (_) {
      composer.textContent = text;
    }
    if (parser.normalizeText(composer.textContent) !== parser.normalizeText(text)) {
      composer.textContent = text;
    }
    composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
  }

  function ensureTarget(work) {
    if (runner.isSameFacebookTarget(location.href, work.target.url)) return true;
    setPanel(
      'OPENING',
      `<div><strong>${escapeHtml(work.campaign_name)}</strong></div><div style="margin-top:6px">${escapeHtml(work.target.url)}</div>`,
    );
    location.assign(work.target.url);
    return false;
  }

  async function fetchNextWork(config) {
    const response = await api('/api/seeding/account/next-work', {
      method: 'POST',
      body: { instance_id: config.extensionInstanceId },
    });
    return response.work;
  }

  async function prepareContext(work, config) {
    const extracted = parser.extractPostContext(document, location.href);
    if (!extracted.ok) {
      throw new Error(`Không đọc chắc chắn được bài Facebook: ${extracted.error}`);
    }
    setPanel('ĐANG SINH NỘI DUNG', `<div>${escapeHtml(work.campaign_name)} · đọc bài xong, đang tạo bộ comment khác nhau cho các account đã chọn.</div>`);
    await api('/api/seeding/account/prepare', {
      method: 'POST',
      body: {
        instance_id: config.extensionInstanceId,
        campaign_id: work.campaign_id,
        target_id: work.target.id,
        context: extracted.context,
      },
    });
  }

  function showLike(work, config) {
    const node = setPanel('LIKE · xác nhận thủ công', `
      <div><strong>${escapeHtml(work.campaign_name)}</strong> · ${escapeHtml(config.accountLabel)}</div>
      <div style="margin-top:8px">Hãy LIKE bài bằng đúng profile này. Extension không tự click Like.</div>
      <div id="acp-seed-like-status" style="margin-top:8px;color:#94a3b8">Sau khi Facebook đã hiện trạng thái Like, bấm nút xác nhận.</div>
      ${button('Đã LIKE', 'acp-seed-like-done')}
      ${button('Dừng', 'acp-seed-like-stop', true)}
    `);
    node.querySelector('#acp-seed-like-done').addEventListener('click', async () => {
      try {
        await api('/api/seeding/account/like-result', {
          method: 'POST',
          body: {
            instance_id: config.extensionInstanceId,
            campaign_id: work.campaign_id,
            done: true,
          },
        });
        running = false;
        run().catch(showFatal);
      } catch (error) { showFatal(error); }
    });
    node.querySelector('#acp-seed-like-stop').addEventListener('click', () => {
      running = false;
      setPanel('PAUSED', '<div>Profile này đã dừng tại bước LIKE. Reload/mở lại trang để tiếp tục.</div>', 'danger');
    });
  }

  function composerForWork(work) {
    if (work.slot.comment_type === 'REPLY') {
      return lastFacebookComposer && parser.isVisible(lastFacebookComposer)
        ? lastFacebookComposer
        : null;
    }
    const extracted = parser.extractPostContext(document, location.href);
    const scope = extracted.ok && extracted.article ? extracted.article : document;
    return parser.findCommentComposer(scope) || parser.findCommentComposer(document);
  }

  function showComment(work, config) {
    const isReply = work.slot.comment_type === 'REPLY';
    const kind = isReply ? `Reply ${work.slot.item_index}` : `CMT chính ${work.slot.item_index}`;
    const initial = String(work.slot.final_text || work.slot.generated_text || '').trim();
    let lastFilledComposer = null;
    if (isReply) lastFacebookComposer = null;
    const instruction = isReply
      ? 'Bấm Reply dưới comment phù hợp trên Facebook. ACP nhớ ô reply đó ngay cả khi bạn quay lại panel để bấm “Điền vào ô đã chọn”. Mỗi reply mới phải chọn lại comment cần trả lời.'
      : 'Bấm “Điền CMT chính”; extension chỉ điền nội dung, không tự bấm Đăng.';
    const node = setPanel(`${kind} · ${config.accountLabel}`, `
      <div><strong>${escapeHtml(work.campaign_name)}</strong> · Account slot ${escapeHtml(work.account_slot)}</div>
      <div style="margin-top:8px;color:#cbd5e1">${escapeHtml(instruction)}</div>
      <textarea id="acp-seed-work-text" style="width:100%;box-sizing:border-box;min-height:100px;margin-top:10px;padding:8px">${escapeHtml(initial)}</textarea>
      <div id="acp-seed-work-status" style="margin-top:8px;color:#94a3b8">Bạn có thể sửa câu trước khi điền.</div>
      ${button(isReply ? 'Điền vào ô đã chọn' : 'Điền CMT chính', 'acp-seed-fill')}
      ${button('Đã đăng · xác nhận', 'acp-seed-confirm')}
      ${button('Đã bấm Đăng nhưng chưa verify', 'acp-seed-unknown', true)}
      ${button('Dừng · làm tiếp sau', 'acp-seed-stop-work', true)}
    `);

    const textArea = node.querySelector('#acp-seed-work-text');
    const status = node.querySelector('#acp-seed-work-status');
    node.querySelector('#acp-seed-fill').addEventListener('click', () => {
      const text = textArea.value.trim();
      if (!text) {
        status.textContent = 'Nội dung đang rỗng.';
        return;
      }
      const composer = composerForWork(work);
      if (!composer) {
        status.textContent = isReply
          ? 'Chưa ghi nhận ô reply Facebook. Hãy bấm Reply dưới đúng comment rồi thử lại.'
          : 'Không xác định duy nhất ô comment chính; không điền để tránh nhầm.';
        return;
      }
      setComposerText(composer, text);
      lastFilledComposer = composer;
      status.textContent = 'Đã điền. Hãy kiểm tra trên Facebook và tự bấm Đăng.';
    });

    node.querySelector('#acp-seed-confirm').addEventListener('click', async () => {
      const text = textArea.value.trim();
      if (!text) return;
      if (!lastFilledComposer) {
        status.textContent = 'Hãy dùng nút Điền trước để ACP theo dõi đúng ô composer.';
        return;
      }
      const extracted = parser.extractPostContext(document, location.href);
      if (!extracted.ok || !runner.verifyManualSubmission(extracted.article, lastFilledComposer, text)) {
        status.textContent = 'Chưa xác minh được comment đã đăng. Nếu bạn CHẮC CHẮN đã bấm Đăng, dùng nút UNKNOWN để khóa slot và tránh đăng trùng; nếu chưa đăng thì bấm Dừng.';
        return;
      }
      try {
        const result = await api('/api/seeding/account/work-result', {
          method: 'POST',
          body: {
            instance_id: config.extensionInstanceId,
            slot_id: work.slot.id,
            result: 'DONE',
            final_text: text,
            proof_ref: `observed:${Date.now()}`,
          },
        });
        if (result.report && result.report.status === 'FAILED') {
          status.textContent = `Comment đã ghi DONE nhưng Sheet lỗi: ${result.report.error || 'unknown'}`;
        }
        lastFacebookComposer = null;
        running = false;
        run().catch(showFatal);
      } catch (error) { showFatal(error); }
    });

    node.querySelector('#acp-seed-unknown').addEventListener('click', async () => {
      const text = textArea.value.trim();
      if (!text) return;
      if (!lastFilledComposer) {
        status.textContent = 'Chỉ dùng UNKNOWN sau khi đã Điền và bạn đã bấm Đăng trên Facebook.';
        return;
      }
      try {
        await api('/api/seeding/account/work-result', {
          method: 'POST',
          body: {
            instance_id: config.extensionInstanceId,
            slot_id: work.slot.id,
            result: 'UNKNOWN',
            final_text: text,
            proof_ref: `clicked:unverified:${Date.now()}`,
          },
        });
        lastFacebookComposer = null;
        running = false;
        setPanel('UNKNOWN', '<div>Slot đã khóa để tránh tự đăng lại. Kiểm tra thủ công trên Facebook rồi reset UNKNOWN từ ACP nếu thật sự cần làm lại.</div>', 'danger');
      } catch (error) { showFatal(error); }
    });

    node.querySelector('#acp-seed-stop-work').addEventListener('click', () => {
      lastFacebookComposer = null;
      running = false;
      setPanel('PAUSED', '<div>Slot chưa thay đổi. Reload/mở lại trang để tiếp tục đúng comment này sau.</div>', 'danger');
    });
  }

  async function run() {
    if (running) return;
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
    running = true;
    try {
      const configResult = await bridge({ type: 'ACP_GET_CONFIG' });
      const config = (configResult && configResult.data) || {};
      if (!config.seedingToken || !config.accountLabel || !config.extensionInstanceId) {
        running = false;
        return showConfig();
      }
      const pairing = await bridge({ type: 'ACP_REGISTER_ACCOUNT' });
      if (!pairing || !pairing.ok) {
        running = false;
        return showConfig();
      }
      if (runner.hasFacebookSafetyBlock(document.body && document.body.innerText)) {
        throw new Error('Facebook đang hiển thị checkpoint/rate restriction. Tool dừng; không bypass.');
      }

      const work = await fetchNextWork(config);
      if (!work || work.done) {
        running = false;
        setPanel(
          'IDLE',
          `<div><strong>${escapeHtml(config.accountLabel)}</strong> đang kết nối.</div><div style="margin-top:6px">Không còn phần việc được gán. ACP sẽ kiểm tra lại sau 15 giây.</div>`,
          'ok',
        );
        scheduleIdlePoll();
        return;
      }
      if (!ensureTarget(work)) return;

      if (work.action === 'NEEDS_CONTEXT') {
        await prepareContext(work, config);
        running = false;
        return run();
      }
      if (work.action === 'LIKE') {
        running = false;
        return showLike(work, config);
      }
      if (work.action === 'COMMENT') {
        running = false;
        return showComment(work, config);
      }
      throw new Error(`Action không hỗ trợ: ${work.action}`);
    } catch (error) {
      return showFatal(error);
    }
  }

  run().catch(showFatal);
})();
