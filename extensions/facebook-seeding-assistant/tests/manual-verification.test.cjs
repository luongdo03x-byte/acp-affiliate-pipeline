const test = require('node:test');
const assert = require('node:assert/strict');
const runner = require('../runner.js');

test('manual completion does not verify while expected text remains in composer', () => {
  const root = { textContent: 'Bài viết Nội dung cần đăng' };
  const composer = { textContent: 'Nội dung cần đăng' };
  assert.equal(
    runner.verifyManualSubmission(root, composer, 'Nội dung cần đăng'),
    false,
  );
});

test('manual completion verifies after composer clears and text remains rendered', () => {
  const root = { textContent: 'Bài viết\nNội dung cần đăng\nCác bình luận khác' };
  const composer = { textContent: '' };
  assert.equal(
    runner.verifyManualSubmission(root, composer, 'Nội dung cần đăng'),
    true,
  );
});
