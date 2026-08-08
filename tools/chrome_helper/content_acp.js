// content_acp.js — chạy TRONG trang ACP (chỉ localhost/127.0.0.1:5000, xem
// host_permissions trong manifest.json).
//
// Chỉ đọc thẻ <meta name="acp-helper-pairing"> mà chính trang /sanpham tự đặt
// khi người vận hành bấm "Mở Shopee & lấy thông tin" (xem
// web/templates/products.html). Không đọc gì khác trên trang -- không đọc
// session cookie, không đọc dữ liệu bài đăng, không đọc mật khẩu.
//
// Nội dung thẻ chỉ là {token, product_url} -- một token dùng-một-lần do ACP
// phát ra, không phải bí mật đăng nhập.

function relayPairingToken() {
  var meta = document.querySelector('meta[name="acp-helper-pairing"]');
  if (!meta || !meta.content) return;
  var data;
  try {
    data = JSON.parse(meta.content);
  } catch (e) {
    return;
  }
  if (data && data.token && data.product_url) {
    chrome.runtime.sendMessage({
      type: "ACP_PAIRING",
      token: data.token,
      productUrl: data.product_url,
      origin: location.origin,
    });
  }
}

relayPairingToken();
window.addEventListener("acp:helper-token", relayPairingToken);
