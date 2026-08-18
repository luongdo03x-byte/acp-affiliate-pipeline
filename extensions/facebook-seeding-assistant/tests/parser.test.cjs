const test = require('node:test');
const assert = require('node:assert/strict');
const parser = require('../parser.js');
const runner = require('../runner.js');

test('normalizeText collapses whitespace and NBSP', () => {
  assert.equal(parser.normalizeText('  xin\u00a0 chào\n bạn  '), 'xin chào bạn');
});

test('auto submit requires AUTO_READY and an unpaused server status', () => {
  assert.equal(
    runner.shouldAttemptAutoSubmit({ decision: 'AUTO_READY' }, { paused: false }),
    true,
  );
  assert.equal(
    runner.shouldAttemptAutoSubmit({ decision: 'REVIEW_REQUIRED' }, { paused: false }),
    false,
  );
  assert.equal(
    runner.shouldAttemptAutoSubmit({ decision: 'AUTO_READY' }, { paused: true }),
    false,
  );
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
