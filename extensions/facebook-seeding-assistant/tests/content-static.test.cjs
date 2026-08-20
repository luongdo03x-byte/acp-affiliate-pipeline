const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');

test('content script uses account-scoped work APIs and keeps Facebook actions manual', () => {
  const source = fs.readFileSync(path.join(root, 'content.js'), 'utf8');
  for (const path of [
    '/api/seeding/account/next-work',
    '/api/seeding/account/prepare',
    '/api/seeding/account/like-result',
    '/api/seeding/account/work-result',
  ]) assert.equal(source.includes(path), true, `missing account API: ${path}`);
  assert.equal(source.includes('/api/seeding/next-target'), false);
  assert.equal(source.includes('Extension không tự click Like.'), true);
  assert.equal(source.includes('tự bấm Đăng'), true);
  assert.equal(source.includes('UNKNOWN'), true);
});

test('content script has no anti-detection or bypass mechanisms', () => {
  const source = fs.readFileSync(path.join(root, 'content.js'), 'utf8').toLowerCase();
  for (const forbidden of [
    'captcha solver',
    'fingerprint spoof',
    'proxy rotation',
    'webdriver',
    'random delay',
  ]) {
    assert.equal(source.includes(forbidden), false, `forbidden marker: ${forbidden}`);
  }
});
