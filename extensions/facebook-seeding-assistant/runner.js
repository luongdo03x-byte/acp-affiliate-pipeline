(function (root, factory) {
  const parser = typeof module === 'object' && module.exports
    ? require('./parser.js')
    : root.ACPSeedingParser;
  const api = factory(parser);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ACPSeedingRunner = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (parser) {
  const FACEBOOK_HOSTS = new Set(['facebook.com', 'www.facebook.com', 'm.facebook.com']);

  function shouldAttemptAutoSubmit(decision, status, expectedShiftId) {
    return Boolean(
      decision
      && decision.decision === 'AUTO_READY'
      && status
      && status.paused === false
      && expectedShiftId
      && status.active_shift_id === expectedShiftId,
    );
  }

  function verifyObservedComment(rootNode, expectedText) {
    const haystack = parser.normalizeText(rootNode && rootNode.textContent).toLowerCase();
    const needle = parser.normalizeText(expectedText).toLowerCase();
    return Boolean(needle && haystack.includes(needle));
  }

  function verifyManualSubmission(rootNode, composer, expectedText) {
    const needle = parser.normalizeText(expectedText).toLowerCase();
    if (!needle) return false;
    if (composer && composer.isConnected !== false) {
      const draft = parser.normalizeText(composer.textContent).toLowerCase();
      if (draft && draft.includes(needle)) return false;
    }
    return verifyObservedComment(rootNode, expectedText);
  }

  function hasFacebookSafetyBlock(text) {
    const value = parser.normalizeText(text).toLowerCase();
    return [
      'xác nhận danh tính',
      'confirm your identity',
      'bạn tạm thời bị chặn',
      'temporarily blocked',
      'we limit how often',
      'chúng tôi giới hạn tần suất',
      'try again later',
      'hãy thử lại sau',
    ].some((term) => value.includes(term));
  }

  function normalizedTarget(value) {
    try {
      const url = new URL(String(value || ''));
      if (url.protocol !== 'https:' || !FACEBOOK_HOSTS.has(url.hostname.toLowerCase())) return null;
      const path = url.pathname.replace(/\/+$/, '') || '/';
      return {
        path,
        query: /\/(?:posts|permalink)\/[^/]+$/i.test(path) ? '' : url.search,
      };
    } catch (_) {
      return null;
    }
  }

  function isSameFacebookTarget(left, right) {
    const a = normalizedTarget(left);
    const b = normalizedTarget(right);
    return Boolean(a && b && a.path === b.path && a.query === b.query);
  }

  async function performSingleSubmit({ decision, expectedShiftId, getStatus, submit, verify }) {
    if (!decision || decision.decision !== 'AUTO_READY') return 'REVIEW_REQUIRED';
    const status = await getStatus();
    if (!shouldAttemptAutoSubmit(decision, status, expectedShiftId)) return 'PAUSED';
    await submit();
    const verified = await verify();
    return verified ? 'POSTED' : 'UNKNOWN';
  }

  return {
    shouldAttemptAutoSubmit,
    verifyObservedComment,
    verifyManualSubmission,
    hasFacebookSafetyBlock,
    isSameFacebookTarget,
    performSingleSubmit,
  };
});
