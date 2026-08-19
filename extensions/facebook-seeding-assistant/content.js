(() => {
  const parser = globalThis.ACPSeedingParser;
  const runner = globalThis.ACPSeedingRunner;
  if (!parser || !runner) return;

  const PANEL_ID = 'acp-facebook-seeding-panel';
  let running = false;

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

  async function getAssignment() {
    const response = await bridge({ type: 'ACP_GET_ASSIGNMENT' });
    return response && response.data ? response.data : null;
  }

  async function setAssignment(assignment) {
    await bridge({ type: 'ACP_SET_ASSIGNMENT', assignment: assignment || null });
  }

  function panel() {
    let node = document.getElementById(PANEL_ID);
    if (node) return node;
    node = document.createElement('aside');
    node.id = PANEL_ID;
    node.style.cssText = [
      'position:fixed', 'right:16px', 'bottom:16px', 'z-index:2147483647',
      'width:360px', 'max-height:75vh', 'overflow:auto', 'padding:14px',
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
      if (!response || !response.ok) {
        showFatal(new Error('Không lưu được cấu hình extension'));
        return;
      }
      if (response.pairing && !response.pairing.ok) {
        const error = response.pairing.data && response.pairing.data.error
          ? response.pairing.data.error
          : 'Không kết nối được account với ACP';
        showFatal(new Error(error));
        return;
      }
      running = false;
      run().catch(showFatal);
    });
  }

  function showFatal(error) {
    running = false;
    setPanel(
      'DỪNG',
      `<div>${escapeHtml(error && error.message ? error.message : error)}</div>${button('Cấu hình', 'acp-seed-config')}`,
      'danger',
    ).querySelector('#acp-seed-config').addEventListener('click', showConfig);
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

  async function waitForVerification(expectedText, composer) {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const composerText = parser.normalizeText(composer && composer.textContent);
      if (!composerText && runner.verifyObservedComment(document.body, expectedText)) return true;
    }
    return false;
  }

  async function clearAndAdvance() {
    await setAssignment(null);
    await new Promise((resolve) => setTimeout(resolve, 300));
    running = false;
    return run();
  }

  async function recordResult(assignment, result, mode, finalText, proofRef = null, errorDetail = null) {
    const path = mode === 'reviewed' ? '/api/seeding/review-result' : '/api/seeding/result';
    return api(path, {
      method: 'POST',
      body: {
        shift_id: assignment.shift_id,
        target_id: assignment.target.id,
        result,
        mode,
        final_text: finalText || null,
        proof_ref: proofRef,
        error_detail: errorDetail,
      },
    });
  }

  async function submitPrepared(assignment, decision, finalText, mode) {
    const extracted = parser.extractPostContext(document, location.href);
    const scope = extracted.ok && extracted.article ? extracted.article : document;
    const composer = parser.findCommentComposer(scope) || parser.findCommentComposer(document);
    if (!composer) throw new Error('REVIEW_REQUIRED: không xác định duy nhất ô comment');
    const submitControl = parser.findSubmitControl(composer);
    if (!submitControl) throw new Error('REVIEW_REQUIRED: không xác định duy nhất nút đăng');

    setComposerText(composer, finalText);
    setPanel('Sẵn sàng gửi', `<div>${escapeHtml(finalText)}</div>`, 'ok');

    const result = await runner.performSingleSubmit({
      decision: { decision: 'AUTO_READY' },
      expectedShiftId: assignment.shift_id,
      getStatus: async () => api(`/api/seeding/status?shift_id=${encodeURIComponent(assignment.shift_id)}`),
      submit: async () => submitControl.click(),
      verify: async () => waitForVerification(finalText, composer),
    });

    if (result === 'PAUSED') {
      setPanel('PAUSED', '<div>Global pause hoặc shift pause đang bật. Không submit.</div>', 'danger');
      return;
    }
    if (result === 'UNKNOWN') {
      await recordResult(assignment, 'UNKNOWN', mode, finalText, null, 'Không verify được comment sau một lần submit');
      await setAssignment(null);
      setPanel('UNKNOWN', '<div>Đã click đúng một lần nhưng không verify được. Tool dừng và không thử lại để tránh duplicate.</div>', 'danger');
      return;
    }
    await recordResult(assignment, 'POSTED', mode, finalText, `observed:${Date.now()}`);
    setPanel('POSTED', '<div>Đã verify comment và ghi KPI.</div>', 'ok');
    return clearAndAdvance();
  }

  async function showReview(assignment, decision, extraReason = '') {
    const drafts = Array.isArray(decision.drafts) ? decision.drafts : [];
    const risk = Array.isArray(decision.risk_labels) ? decision.risk_labels.join(', ') : '';
    const first = drafts[0] || '';
    const node = setPanel('REVIEW_REQUIRED', `
      <div style="color:#fca5a5">${escapeHtml(extraReason || risk || 'Cần operator kiểm tra')}</div>
      <div style="margin-top:8px">Confidence: ${escapeHtml(decision.confidence ?? '—')}</div>
      <textarea id="acp-seed-review-text" style="width:100%;box-sizing:border-box;min-height:96px;margin-top:8px;padding:8px">${escapeHtml(first)}</textarea>
      ${button('Đăng bản đã duyệt', 'acp-seed-review-post')}
      ${button('Skip', 'acp-seed-review-skip')}
      ${button('Pause shift', 'acp-seed-review-pause', true)}
    `, 'danger');

    node.querySelector('#acp-seed-review-post').addEventListener('click', async () => {
      const text = node.querySelector('#acp-seed-review-text').value.trim();
      if (!text) return;
      try { await submitPrepared(assignment, decision, text, 'reviewed'); } catch (error) { showFatal(error); }
    });
    node.querySelector('#acp-seed-review-skip').addEventListener('click', async () => {
      try {
        await recordResult(assignment, 'SKIPPED', 'reviewed', null, null, 'Operator skipped');
        await clearAndAdvance();
      } catch (error) { showFatal(error); }
    });
    node.querySelector('#acp-seed-review-pause').addEventListener('click', async () => {
      try {
        await api('/api/seeding/pause-shift', { method: 'POST', body: { shift_id: assignment.shift_id } });
        setPanel('PAUSED', '<div>Shift đã pause trên ACP.</div>', 'danger');
      } catch (error) { showFatal(error); }
    });
  }

  async function acquireAssignment() {
    let assignment = await getAssignment();
    if (assignment && assignment.target && assignment.shift_id) return assignment;
    const next = await api('/api/seeding/next-target', { method: 'POST', body: {} });
    if (next.done || !next.target) return null;
    assignment = { shift_id: next.shift_id, target: next.target };
    await setAssignment(assignment);
    return assignment;
  }

  async function run() {
    if (running) return;
    running = true;
    try {
      const configResult = await bridge({ type: 'ACP_GET_CONFIG' });
      const config = (configResult && configResult.data) || {};
      if (!config.seedingToken || !config.accountLabel) {
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
      const status = await api('/api/seeding/status');
      if (status.paused) {
        running = false;
        setPanel('PAUSED', '<div>Global pause đang bật.</div>', 'danger');
        return;
      }
      const assignment = await acquireAssignment();
      if (!assignment) {
        running = false;
        setPanel('DONE', `<div>${escapeHtml(config.accountLabel)} đã kết nối. Không còn target READY.</div>`, 'ok');
        return;
      }
      if (!runner.isSameFacebookTarget(location.href, assignment.target.url)) {
        setPanel('OPENING', `<div>${escapeHtml(assignment.target.url)}</div>`);
        location.assign(assignment.target.url);
        return;
      }

      const extracted = parser.extractPostContext(document, location.href);
      const context = extracted.ok
        ? extracted.context
        : { url: location.href, post_text: '', surface_name: '', post_ref: '' };
      const decision = await api('/api/seeding/analyze', {
        method: 'POST',
        body: {
          shift_id: assignment.shift_id,
          target_id: assignment.target.id,
          context,
        },
      });

      if (decision.decision !== 'AUTO_READY') {
        running = false;
        return showReview(assignment, decision, extracted.ok ? '' : extracted.error);
      }

      const draft = Array.isArray(decision.drafts) ? String(decision.drafts[0] || '').trim() : '';
      if (!draft) {
        running = false;
        return showReview(assignment, { ...decision, decision: 'REVIEW_REQUIRED' }, 'Draft rỗng');
      }
      try {
        await submitPrepared(assignment, decision, draft, 'auto');
      } catch (error) {
        running = false;
        return showReview(assignment, { ...decision, decision: 'REVIEW_REQUIRED' }, error.message);
      }
    } catch (error) {
      return showFatal(error);
    }
  }

  run().catch(showFatal);
})();
