const test = require('node:test');
const assert = require('node:assert/strict');
const runner = require('../runner.js');

test('group permalink ignores volatile tracking query for the same post id', () => {
  assert.equal(
    runner.isSameFacebookTarget(
      'https://www.facebook.com/groups/467062964514242/permalink/1737872480766611/?rdid=S5xWfZe7yEwTRsQl',
      'https://www.facebook.com/groups/467062964514242/permalink/1737872480766611/',
    ),
    true,
  );
  assert.equal(
    runner.isSameFacebookTarget(
      'https://www.facebook.com/groups/467062964514242/permalink/1737872480766611/',
      'https://www.facebook.com/groups/467062964514242/permalink/9999999999999999/',
    ),
    false,
  );
});
