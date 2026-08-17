// background.js — ACP Shopee Helper (service worker, Manifest V3)
//
// Security rules:
//   1. Never read cookies, localStorage, sessionStorage or Shopee auth state.
//   2. Never automate Shopee. Read the rendered DOM only after the operator
//      explicitly clicks the extension on the active tab.
//   3. Never bypass CAPTCHA/anti-bot checks.
//   4. Submit only product metadata plus the active tab URL used to prove the
//      observed product identity server-side.
//   5. Send only to ACP loopback origin relayed by the ACP content script.

var pairing = null; // { token, productUrl, origin }

function isAllowedAcpOrigin(origin) {
  return origin === "http://127.0.0.1:5000" || origin === "http://localhost:5000";
}

chrome.runtime.onMessage.addListener(function (msg) {
  if (msg && msg.type === "ACP_PAIRING" && msg.token && msg.productUrl &&
      isAllowedAcpOrigin(msg.origin)) {
    pairing = { token: msg.token, productUrl: msg.productUrl, origin: msg.origin };
  }
});

function flashBadge(text, color, tabId) {
  chrome.action.setBadgeText({ text: text, tabId: tabId });
  chrome.action.setBadgeBackgroundColor({ color: color });
  setTimeout(function () {
    chrome.action.setBadgeText({ text: "", tabId: tabId });
  }, 2500);
}

chrome.action.onClicked.addListener(function (tab) {
  if (!tab.url || !/^https:\/\/shopee\.vn\//.test(tab.url)) {
    flashBadge("!", "#EF4444", tab.id);
    return;
  }
  if (!pairing || !isAllowedAcpOrigin(pairing.origin)) {
    flashBadge("?", "#F59E0B", tab.id);
    return;
  }

  chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractShopeeMetadata })
    .then(function (results) {
      var observed = results && results[0] && results[0].result;
      if (!observed || !observed.observed_url || !observed.metadata ||
          !/^https:\/\/shopee\.vn\//.test(observed.observed_url)) {
        flashBadge("×", "#EF4444", tab.id);
        return null;
      }

      return fetch(pairing.origin + "/api/helper/shopee-product", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: pairing.token,
          product_url: pairing.productUrl,
          observed_url: observed.observed_url,
          metadata: observed.metadata,
        }),
      }).then(function (resp) {
        if (resp.ok) {
          pairing = null; // one-time pairing completed successfully
          flashBadge("✓", "#22C55E", tab.id);
        } else {
          // Keep pairing on failure so a user who clicked the wrong Shopee tab
          // can switch to the intended product and retry before the 5-minute TTL.
          flashBadge("×", "#EF4444", tab.id);
        }
      });
    })
    .catch(function () {
      // Keep the pairing for a retry; server-side expiry still caps its life.
      flashBadge("×", "#EF4444", tab.id);
    });
});

// Runs inside the rendered Shopee tab through chrome.scripting.executeScript.
// It reads only public DOM/metadata already present on the page. The observed
// URL is returned separately and is NOT trusted as product metadata; ACP uses
// it only to verify this tab matches the token-bound canonical product.
function extractShopeeMetadata() {
  function fromJsonLd() {
    var nodes = document.querySelectorAll('script[type="application/ld+json"]');
    for (var i = 0; i < nodes.length; i++) {
      var data;
      try {
        data = JSON.parse(nodes[i].textContent);
      } catch (e) {
        continue;
      }
      var items = Array.isArray(data) ? data : [data];
      for (var j = 0; j < items.length; j++) {
        var item = items[j];
        var type = item && item["@type"];
        var isProduct = type === "Product" || (Array.isArray(type) && type.indexOf("Product") !== -1);
        if (!isProduct) continue;
        var offers = Array.isArray(item.offers) ? item.offers[0] : (item.offers || {});
        var image = Array.isArray(item.image) ? item.image[0] : item.image;
        return {
          name: item.name || null,
          current_price: offers.price ? Math.round(Number(offers.price)) : null,
          original_price: offers.highPrice ? Math.round(Number(offers.highPrice)) : null,
          image_url: image || null,
          shop: (item.brand && item.brand.name) || (item.seller && item.seller.name) || null,
        };
      }
    }
    return null;
  }

  function metaContent(name) {
    var el = document.querySelector('meta[property="' + name + '"], meta[name="' + name + '"]');
    return el ? el.getAttribute("content") : null;
  }

  function parsePrice(text) {
    if (!text) return null;
    var digits = String(text).replace(/[^0-9]/g, "");
    return digits ? parseInt(digits, 10) : null;
  }

  var jsonld = fromJsonLd() || {};
  return {
    observed_url: location.href,
    metadata: {
      name: jsonld.name || metaContent("og:title") || null,
      current_price: jsonld.current_price || parsePrice(metaContent("product:price:amount")) || null,
      original_price: jsonld.original_price || parsePrice(metaContent("product:original_price:amount")) || null,
      image_url: jsonld.image_url || metaContent("og:image") || null,
      shop: jsonld.shop || null,
    },
  };
}
