// background.js — ACP Shopee Helper (service worker, Manifest V3)
//
// Nguyên tắc bảo mật (đừng phá khi sửa file này):
//   1. Không đọc cookie, không đọc localStorage, không đọc session Shopee.
//   2. Không tự động hoá thao tác trên Shopee -- extension chỉ đọc DOM đã
//      render sẵn của tab người dùng đang xem, đúng lúc người dùng bấm icon
//      (activeTab chỉ cấp quyền cho tab đó, đúng thời điểm đó).
//   3. Không cố bypass CAPTCHA/anti-bot của Shopee.
//   4. Chỉ gửi 5 trường: name, current_price, original_price, image_url, shop.
//   5. Chỉ gửi về localhost/127.0.0.1:5000 -- xem host_permissions.
//   6. Token một lần dùng -- xoá khỏi bộ nhớ ngay sau khi gửi, không giữ lại.

var pairing = null; // { token, productUrl, origin }

chrome.runtime.onMessage.addListener(function (msg) {
  if (msg && msg.type === "ACP_PAIRING") {
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
    flashBadge("!", "#EF4444", tab.id); // không phải trang Shopee
    return;
  }
  if (!pairing) {
    flashBadge("?", "#F59E0B", tab.id); // chưa mở tab ACP / chưa bấm nút bên đó
    return;
  }

  chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractShopeeMetadata })
    .then(function (results) {
      var metadata = results && results[0] && results[0].result;
      if (!metadata) {
        flashBadge("×", "#EF4444", tab.id);
        return;
      }
      return fetch(pairing.origin + "/api/helper/shopee-product", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: pairing.token,
          product_url: pairing.productUrl,
          metadata: metadata,
        }),
      }).then(function (resp) {
        flashBadge(resp.ok ? "✓" : "×", resp.ok ? "#22C55E" : "#EF4444", tab.id);
      });
    })
    .catch(function () {
      flashBadge("×", "#EF4444", tab.id);
    })
    .finally(function () {
      pairing = null; // một lần dùng
    });
});

// Chạy TRONG trang Shopee đã render (chrome.scripting.executeScript tiêm hàm
// này vào tab, không phải fetch từ background). Chỉ đọc DOM công khai của
// trang -- JSON-LD rồi tới thẻ meta OpenGraph -- không đọc cookie, không đọc
// localStorage, không gọi request nào ra ngoài từ trang Shopee.
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
    name: jsonld.name || metaContent("og:title") || null,
    current_price: jsonld.current_price || parsePrice(metaContent("product:price:amount")) || null,
    original_price: jsonld.original_price || parsePrice(metaContent("product:original_price:amount")) || null,
    image_url: jsonld.image_url || metaContent("og:image") || null,
    shop: jsonld.shop || null,
  };
}
