const test = require('node:test');
const assert = require('node:assert/strict');
const parser = require('../parser.js');
const runner = require('../runner.js');

test('normalizeText collapses whitespace and NBSP', () => {
  assert.equal(parser.normalizeText('  xin\u00a0 chào\n bạn  '), 'xin chào bạn');
});

test('composer selection returns one visible candidate and rejects ambiguity', () => {
  const visible = { hidden: false };
  const rootOne = { querySelectorAll: () => [visible] };
  const rootMany = { querySelectorAll: () => [visible, { hidden: false }] };
  assert.equal(parser.findCommentComposer(rootOne), visible);
  assert.equal(parser.findCommentComposer(rootMany), null);
});

test('context extraction returns normalized visible article text', () => {
  const article = {
    hidden: false,
    innerText: '  Xin chỗ\n tư vấn uy tín  ',
    textContent: '  Xin chỗ\n tư vấn uy tín  ',
    getAttribute: (name) => (name === 'data-pagelet' ? 'FeedUnit_123' : null),
    querySelectorAll: () => [],
  };
  const root = { querySelectorAll: () => [article] };
  const result = parser.extractPostContext(
    root,
    'https://www.facebook.com/groups/demo/posts/123/',
  );
  assert.equal(result.ok, true);
  assert.equal(result.context.post_text, 'Xin chỗ tư vấn uy tín');
  assert.equal(result.context.url.endsWith('/posts/123/'), true);
});

test('context extraction prefers the only article containing a matching target permalink', () => {
  const article = (text, href) => ({
    hidden: false,
    innerText: text,
    textContent: text,
    getAttribute: () => null,
    querySelectorAll: (selector) => selector === 'a[href]' ? [{ href }] : [],
  });
  const other = article(
    'Bài khác',
    'https://www.facebook.com/groups/demo/posts/999/',
  );
  const target = article(
    'Đúng bài cần xử lý',
    'https://www.facebook.com/groups/demo/posts/123/?__cft__=abc',
  );
  const root = { querySelectorAll: () => [other, target] };
  const result = parser.extractPostContext(
    root,
    'https://www.facebook.com/groups/demo/posts/123/',
  );
  assert.equal(result.ok, true);
  assert.equal(result.article, target);
  assert.equal(result.context.post_text, 'Đúng bài cần xử lý');
});

test('auto submit requires AUTO_READY, unpaused status, and matching active shift', () => {
  assert.equal(
    runner.shouldAttemptAutoSubmit(
      { decision: 'AUTO_READY' },
      { paused: false, active_shift_id: 'SHIFT1' },
      'SHIFT1',
    ),
    true,
  );
  assert.equal(
    runner.shouldAttemptAutoSubmit(
      { decision: 'REVIEW_REQUIRED' },
      { paused: false, active_shift_id: 'SHIFT1' },
      'SHIFT1',
    ),
    false,
  );
  assert.equal(
    runner.shouldAttemptAutoSubmit(
      { decision: 'AUTO_READY' },
      { paused: true, active_shift_id: 'SHIFT1' },
      'SHIFT1',
    ),
    false,
  );
  assert.equal(
    runner.shouldAttemptAutoSubmit(
      { decision: 'AUTO_READY' },
      { paused: false, active_shift_id: null },
      'SHIFT1',
    ),
    false,
  );
});

test('single submit returns UNKNOWN after one click when verification fails', async () => {
  let clicks = 0;
  let statusChecks = 0;
  const result = await runner.performSingleSubmit({
    decision: { decision: 'AUTO_READY' },
    expectedShiftId: 'SHIFT1',
    getStatus: async () => {
      statusChecks += 1;
      return { paused: false, active_shift_id: 'SHIFT1' };
    },
    submit: async () => {
      clicks += 1;
    },
    verify: async () => false,
  });
  assert.equal(statusChecks, 1);
  assert.equal(clicks, 1);
  assert.equal(result, 'UNKNOWN');
});

test('single submit rechecks pause before click and does not submit while paused', async () => {
  let clicks = 0;
  const result = await runner.performSingleSubmit({
    decision: { decision: 'AUTO_READY' },
    expectedShiftId: 'SHIFT1',
    getStatus: async () => ({ paused: true, active_shift_id: 'SHIFT1' }),
    submit: async () => {
      clicks += 1;
    },
    verify: async () => true,
  });
  assert.equal(clicks, 0);
  assert.equal(result, 'PAUSED');
});

test('single submit stops if the expected shift is no longer active', async () => {
  let clicks = 0;
  const result = await runner.performSingleSubmit({
    decision: { decision: 'AUTO_READY' },
    expectedShiftId: 'SHIFT1',
    getStatus: async () => ({ paused: false, active_shift_id: null }),
    submit: async () => {
      clicks += 1;
    },
    verify: async () => true,
  });
  assert.equal(clicks, 0);
  assert.equal(result, 'PAUSED');
});

test('verification compares normalized visible text', () => {
  const root = {
    textContent: 'Khác\nBạn có thể tham khảo Brand; hiện có tư vấn miễn phí.',
  };
  assert.equal(
    runner.verifyObservedComment(
      root,
      'Bạn có thể tham khảo Brand; hiện có tư vấn miễn phí.',
    ),
    true,
  );
  assert.equal(runner.verifyObservedComment(root, 'không tồn tại'), false);
});

test('facebook safety/trust UI is detected and forces a stop', () => {
  assert.equal(runner.hasFacebookSafetyBlock('Xác nhận danh tính của bạn'), true);
  assert.equal(runner.hasFacebookSafetyBlock('We limit how often you can post'), true);
  assert.equal(runner.hasFacebookSafetyBlock('Bài viết bình thường'), false);
});

test('target URLs compare across supported Facebook host aliases', () => {
  assert.equal(
    runner.isSameFacebookTarget(
      'https://www.facebook.com/groups/demo/posts/123/',
      'https://m.facebook.com/groups/demo/posts/123/',
    ),
    true,
  );
  assert.equal(
    runner.isSameFacebookTarget(
      'https://www.facebook.com/groups/demo/posts/123/',
      'https://www.facebook.com/groups/demo/posts/999/',
    ),
    false,
  );
});
