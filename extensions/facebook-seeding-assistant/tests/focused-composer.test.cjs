const test = require('node:test');
const assert = require('node:assert/strict');
const parser = require('../parser.js');

test('findFocusedComposer returns only a visible editable active element', () => {
  const editable = {
    hidden: false,
    isContentEditable: true,
    getAttribute: (name) => name === 'contenteditable' ? 'true' : null,
  };
  const doc = { activeElement: editable };
  assert.equal(parser.findFocusedComposer(doc), editable);

  const plain = {
    hidden: false,
    isContentEditable: false,
    getAttribute: () => null,
  };
  assert.equal(parser.findFocusedComposer({ activeElement: plain }), null);
});
