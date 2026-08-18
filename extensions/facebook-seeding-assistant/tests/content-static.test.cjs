const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');

test('content script uses server decisions and single-submit helper', () => {
  const source = fs.readFileSync(path.join(root, 'content.js'), 'utf8');
  assert.equal(source.includes('/api/seeding/analyze'), true);
  assert.equal(source.includes('/api/seeding/status'), true);
  assert.equal(source.includes('performSingleSubmit'), true);
  assert.equal(source.includes('UNKNOWN'), true);
  assert.equal(source.includes('REVIEW_REQUIRED'), true);
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
