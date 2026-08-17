// content_acp.js — runs only on ACP loopback pages allowed by manifest.json.
// It relays the one-time helper pairing token that /sanpham writes into a meta
// element. It never reads cookies, password fields or application data.

function isAllowedAcpOrigin(loc) {
  if (!loc || loc.protocol !== "http:" || loc.port !== "5000") return false;
  return loc.hostname === "127.0.0.1" || loc.hostname === "localhost";
}

function relayPairingToken() {
  if (!isAllowedAcpOrigin(location)) return;

  var meta = document.querySelector('meta[name="acp-helper-pairing"]');
  if (!meta || !meta.content) return;
  var data;
  try {
    data = JSON.parse(meta.content);
  } catch (e) {
    return;
  }
  if (data && typeof data.token === "string" && data.token &&
      typeof data.product_url === "string" && data.product_url) {
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
