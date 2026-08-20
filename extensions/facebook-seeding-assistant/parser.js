(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ACPSeedingParser = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const FACEBOOK_HOSTS = new Set(['facebook.com', 'www.facebook.com', 'm.facebook.com']);

  function normalizeText(value) {
    return String(value || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function isVisible(element) {
    if (!element || element.hidden) return false;
    if (typeof element.getAttribute === 'function' && element.getAttribute('aria-hidden') === 'true') {
      return false;
    }
    const style = element.style;
    if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    if (typeof element.getBoundingClientRect === 'function') {
      const rect = element.getBoundingClientRect();
      if (rect && rect.width === 0 && rect.height === 0) return false;
    }
    return true;
  }

  function visibleCandidates(rootNode, selector) {
    if (!rootNode || typeof rootNode.querySelectorAll !== 'function') return [];
    return Array.from(rootNode.querySelectorAll(selector)).filter(isVisible);
  }

  function facebookResourceKey(value) {
    try {
      const url = new URL(String(value || ''));
      if (url.protocol !== 'https:' || !FACEBOOK_HOSTS.has(url.hostname.toLowerCase())) return null;
      const path = url.pathname.replace(/\/+$/, '') || '/';
      if (/\/(?:posts|permalink)\/[^/]+$/i.test(path)) return path;
      return `${path}${url.search}`;
    } catch (_) {
      return null;
    }
  }

  function articleLinksToTarget(article, targetUrl) {
    const targetKey = facebookResourceKey(targetUrl);
    if (!targetKey || !article || typeof article.querySelectorAll !== 'function') return false;
    return Array.from(article.querySelectorAll('a[href]')).some((anchor) => {
      const href = anchor && (anchor.href || (typeof anchor.getAttribute === 'function' && anchor.getAttribute('href')));
      return facebookResourceKey(href) === targetKey;
    });
  }

  function findTargetArticle(rootNode, targetUrl) {
    const articles = visibleCandidates(rootNode, '[role="article"]')
      .filter((node) => normalizeText(node.innerText || node.textContent).length > 0);
    if (!articles.length) return null;

    if (targetUrl) {
      const permalinkMatches = articles.filter((node) => articleLinksToTarget(node, targetUrl));
      if (permalinkMatches.length === 1) return permalinkMatches[0];
      if (permalinkMatches.length > 1) return null;
    }

    if (articles.length === 1) return articles[0];

    const feedUnits = articles.filter((node) => {
      if (typeof node.getAttribute !== 'function') return false;
      return /feedunit/i.test(String(node.getAttribute('data-pagelet') || ''));
    });
    return feedUnits.length === 1 ? feedUnits[0] : null;
  }

  function extractPostContext(rootNode, url) {
    const article = findTargetArticle(rootNode, url);
    if (!article) return { ok: false, error: 'ambiguous_or_missing_article' };
    const postText = normalizeText(article.innerText || article.textContent);
    if (!postText) return { ok: false, error: 'empty_article' };
    const pagelet = typeof article.getAttribute === 'function'
      ? normalizeText(article.getAttribute('data-pagelet'))
      : '';
    return {
      ok: true,
      article,
      context: {
        url: String(url || ''),
        post_text: postText.slice(0, 12000),
        surface_name: '',
        post_ref: pagelet || '',
      },
    };
  }

  function findCommentComposer(rootNode) {
    const candidates = visibleCandidates(
      rootNode,
      '[contenteditable="true"][role="textbox"], [contenteditable="true"][aria-label*="comment" i], [contenteditable="true"][aria-label*="bình luận" i]',
    );
    return candidates.length === 1 ? candidates[0] : null;
  }

  function findFocusedComposer(rootNode) {
    const element = rootNode && rootNode.activeElement;
    if (!element || !isVisible(element)) return null;
    const contenteditable = typeof element.getAttribute === 'function'
      ? element.getAttribute('contenteditable')
      : null;
    if (element.isContentEditable === true || String(contenteditable).toLowerCase() === 'true') {
      return element;
    }
    return null;
  }

  function findSubmitControl(composer) {
    if (!composer) return null;
    const scope = typeof composer.closest === 'function' ? composer.closest('form') : null;
    const rootNode = scope || composer.parentElement;
    if (!rootNode || typeof rootNode.querySelectorAll !== 'function') return null;
    const buttons = Array.from(rootNode.querySelectorAll('button, [role="button"]'))
      .filter(isVisible)
      .filter((button) => !button.disabled)
      .filter((button) => {
        const label = normalizeText(
          (typeof button.getAttribute === 'function' && button.getAttribute('aria-label'))
          || button.innerText
          || button.textContent,
        ).toLowerCase();
        const type = typeof button.getAttribute === 'function' ? button.getAttribute('type') : '';
        return type === 'submit' || /^(post|đăng|comment|bình luận)$/.test(label);
      });
    return buttons.length === 1 ? buttons[0] : null;
  }

  return {
    normalizeText,
    isVisible,
    findTargetArticle,
    extractPostContext,
    findCommentComposer,
    findFocusedComposer,
    findSubmitControl,
  };
});
