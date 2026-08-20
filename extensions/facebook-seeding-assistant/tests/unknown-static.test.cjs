const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'content.js'), 'utf8');

test('operator can terminally mark a clicked-but-unverified comment UNKNOWN', () => {
  assert.match(source, /acp-seed-unknown/);
  assert.match(source, /result:\s*'UNKNOWN'/);
  assert.match(source, /clicked:unverified/);
});

test('ordinary stop still leaves work retryable instead of recording a result', () => {
  const stopStart = source.indexOf("#acp-seed-stop-work");
  assert.notEqual(stopStart, -1);
  const tail = source.slice(stopStart, stopStart + 700);
  assert.doesNotMatch(tail, /work-result/);
});
