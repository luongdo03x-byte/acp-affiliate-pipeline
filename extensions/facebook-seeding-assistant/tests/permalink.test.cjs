const test = require('node:test');
const assert = require('node:assert/strict');
const parser = require('../parser.js');
const runner = require('../runner.js');

const TARGET = 'https://www.facebook.com/groups/467062964514242/permalink/1737872480766611/?rdid=S5xWfZe7yEwTRsQl';
const CANONICAL = 'https://www.facebook.com/groups/467062964514242/permalink/1737872480766611/';

test('group permalink ignores volatile tracking query for the same post id', () => {
  assert.equal(runner.isSameFacebookTarget(TARGET, CANONICAL), true);
  assert.equal(
    runner.isSameFacebookTarget(
      CANONICAL,
      'https://www.facebook.com/groups/467062964514242/permalink/9999999999999999/',
    ),
    false,
  );
});

test('article matching ignores tracking query on group permalink', () => {
  const article = (text, href) => ({
    hidden: false,
    innerText: text,
    textContent: text,
    getAttribute: () => null,
    querySelectorAll: (selector) => selector === 'a[href]' ? [{ href }] : [],
  });
  const other = article(
    'Bài khác',
    'https://www.facebook.com/groups/467062964514242/permalink/9999999999999999/',
  );
  const target = article('Đúng bài cần xử lý', CANONICAL);
  const root = { querySelectorAll: () => [other, target] };

  const result = parser.extractPostContext(root, TARGET);
  assert.equal(result.ok, true);
  assert.equal(result.article, target);
  assert.equal(result.context.post_text, 'Đúng bài cần xử lý');
});
