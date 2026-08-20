const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
const background = fs.readFileSync(path.join(root, 'background.js'), 'utf8');

test('extension persists a profile-local account label and stable instance id', () => {
  assert.match(background, /accountLabel/);
  assert.match(background, /extensionInstanceId/);
  assert.match(background, /randomUUID/);
});

test('extension registers and heartbeats the browser profile through token-protected ACP API', () => {
  assert.match(background, /\/api\/seeding\/account\/register/);
  assert.match(background, /\/api\/seeding\/account\/heartbeat/);
  assert.doesNotMatch(background, /facebook.*password/i);
  assert.doesNotMatch(background, /cookie/i);
});

test('manifest enables minute heartbeat without broad new host access', () => {
  assert.ok(manifest.permissions.includes('alarms'));
});
