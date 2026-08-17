// Presentation-only state helper for the Shopee confirmation form.
// Business state remains server-side; this module only makes helper timeout /
// failure explicit while preserving the always-available manual inputs.
(function () {
  "use strict";

  function boot() {
    var button = document.getElementById("helper-open-btn");
    var note = document.getElementById("helper-status-note");
    if (!button || !note) return;

    var initialText = (note.textContent || "").trim();
    var state = "HELPER_AVAILABLE";
    var badge = document.createElement("span");
    badge.className = "status-badge";
    badge.id = "helper-state-badge";
    badge.setAttribute("aria-live", "polite");
    note.parentNode.insertBefore(badge, note);

    function setState(next, label, tone) {
      state = next;
      badge.dataset.helperState = next;
      badge.className = "status-badge" + (tone ? " status-badge--" + tone : "");
      badge.textContent = label;
    }

    setState("HELPER_AVAILABLE", "Chrome Helper sẵn sàng", "accent");

    // Capture phase runs before the legacy inline click handler starts the
    // pairing request. We do not prevent/replace that handler.
    button.addEventListener("click", function () {
      setState("HELPER_WAITING", "Đang chờ Chrome Helper", "accent");
    }, true);

    function syncFromNote() {
      var text = (note.textContent || "").trim();
      if (text.indexOf("✓ Đã nhận thông tin từ Chrome") !== -1) {
        setState("HELPER_COMPLETE", "✓ Đã nhận từ Chrome", "success");
        return;
      }
      if (text.indexOf("Hết thời gian chờ") !== -1) {
        setState("MANUAL_REQUIRED", "Nhập thủ công", "warning");
        return;
      }
      if (state === "HELPER_WAITING" && text === initialText) {
        // Token request / helper startup failed and the legacy script restored
        // its original note. Manual fields below remain untouched and enabled.
        setState("MANUAL_REQUIRED", "Helper không khả dụng · nhập thủ công", "warning");
        return;
      }
      if (text.indexOf("Đang chờ") !== -1 || text.indexOf("Đã mở tab Shopee") !== -1) {
        setState("HELPER_WAITING", "Đang chờ Chrome Helper", "accent");
      }
    }

    new MutationObserver(syncFromNote).observe(note, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
