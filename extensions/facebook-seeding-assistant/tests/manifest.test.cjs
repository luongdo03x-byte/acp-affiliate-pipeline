const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');

test('manifest uses MV3 and minimal permissions', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ['storage', 'alarms']);
  assert.equal(manifest.host_permissions.includes('<all_urls>'), false);
  assert.equal(manifest.permissions.includes('debugger'), false);
  assert.equal(manifest.permissions.includes('cookies'), false);
  assert.equal(manifest.host_permissions.some((url) => url.includes('facebook.com')), true);
  assert.equal(manifest.host_permissions.some((url) => url.includes('127.0.0.1')), true);
});

test('background does not request Facebook cookies or debugger APIs', () => {
  const source = fs.readFileSync(path.join(root, 'background.js'), 'utf8');
  assert.equal(source.includes('chrome.cookies'), false);
  assert.equal(source.includes('chrome.debugger'), false);
  assert.equal(source.includes('X-ACP-Seeding-Token'), true);
});
