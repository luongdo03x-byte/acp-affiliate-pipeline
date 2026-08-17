// Phase 3 Shopee product-intelligence UI. Presentation/orchestration only:
// no crawler, no automatic Chrome Helper click, no post/publish action.
(function () {
  "use strict";

  function boot() {
    var form = document.querySelector('form[action="/sanpham/affiliate/create"]');
    if (!form) return;

    var productUrlInput = form.querySelector('input[name="product_url"]');
    var csrfInput = form.querySelector('input[name="_csrf"]');
    var priceInput = document.getElementById("current_price");
    if (!productUrlInput || !csrfInput || !priceInput) return;

    var sourceInput = form.querySelector('input[name="metadata_source"]');
    if (!sourceInput) {
      sourceInput = document.createElement("input");
      sourceInput.type = "hidden";
      sourceInput.name = "metadata_source";
      sourceInput.value = "server";
      form.appendChild(sourceInput);
    }

    var provenance = document.createElement("div");
    provenance.className = "note";
    provenance.id = "shopee-metadata-provenance";
    provenance.setAttribute("aria-live", "polite");
    priceInput.parentNode.appendChild(provenance);

    var refreshButton = document.createElement("button");
    refreshButton.type = "button";
    refreshButton.className = "btn btn--small btn--ghost";
    refreshButton.id = "shopee-refresh-price";
    refreshButton.textContent = "Làm mới giá";
    priceInput.parentNode.appendChild(refreshButton);

    function formatObserved(value) {
      if (!value) return "";
      var parsed = new Date(value);
      return isNaN(parsed.getTime()) ? value : parsed.toLocaleString("vi-VN");
    }

    function showSource(source, observedAt, cached) {
      var sourceLabel = source || "manual";
      var suffix = observedAt ? " · cập nhật " + formatObserved(observedAt) : "";
      provenance.textContent = cached
        ? "Nguồn metadata: " + sourceLabel + suffix + " · dữ liệu cache, không phải realtime"
        : "Nguồn metadata: " + sourceLabel + suffix;
    }

    function fillEmpty(meta) {
      if (!meta) return false;
      var used = false;
      ["name", "current_price", "original_price", "shop", "image_url"].forEach(function (name) {
        var field = document.getElementById(name);
        if (field && !field.value && meta[name] !== null && meta[name] !== undefined && meta[name] !== "") {
          field.value = meta[name];
          used = true;
        }
      });
      return used;
    }

    function fillPrice(meta) {
      if (!meta) return;
      if (meta.current_price !== null && meta.current_price !== undefined && meta.current_price !== "") {
        priceInput.value = meta.current_price;
      }
      var original = document.getElementById("original_price");
      if (original && meta.original_price !== null && meta.original_price !== undefined && meta.original_price !== "") {
        original.value = meta.original_price;
      }
    }

    // Real operator edits turn provenance into manual. Programmatic helper/cache
    // fills do not dispatch trusted input events, so their source is preserved.
    ["name", "current_price", "original_price", "shop", "image_url"].forEach(function (name) {
      var field = document.getElementById(name);
      if (!field) return;
      field.addEventListener("input", function (event) {
        if (event.isTrusted) {
          sourceInput.value = "manual";
          showSource("manual", null, false);
        }
      });
    });

    var helperNote = document.getElementById("helper-status-note");
    if (helperNote) {
      new MutationObserver(function () {
        var text = helperNote.textContent || "";
        if (text.indexOf("✓ Đã nhận thông tin từ Chrome") !== -1) {
          sourceInput.value = "helper";
          showSource("helper", new Date().toISOString(), false);
        }
      }).observe(helperNote, { childList: true, characterData: true, subtree: true });
    }

    function loadCacheProvenance() {
      fetch("/sanpham/affiliate/cache?product_url=" + encodeURIComponent(productUrlInput.value), {
        credentials: "same-origin",
      }).then(function (response) {
        return response.ok ? response.json() : null;
      }).then(function (data) {
        if (!data || data.status !== "fresh") return;
        var used = fillEmpty(data.metadata);
        if (used) sourceInput.value = data.source || "manual";
        showSource(data.source, data.observed_at, true);
      }).catch(function () {
        // Cache is an optional fallback; manual/server flow stays usable.
      });
    }

    refreshButton.addEventListener("click", function () {
      refreshButton.disabled = true;
      provenance.textContent = "Đang kiểm tra giá mới...";
      var body = new URLSearchParams();
      body.set("product_url", productUrlInput.value);
      body.set("_csrf", csrfInput.value);
      fetch("/sanpham/affiliate/refresh-price", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      }).then(function (response) {
        if (!response.ok) throw new Error("refresh");
        return response.json();
      }).then(function (data) {
        if (data.status === "server" || data.status === "cache") {
          fillPrice(data.metadata);
          sourceInput.value = data.source || (data.status === "server" ? "server" : "manual");
          showSource(sourceInput.value, data.observed_at, data.status === "cache");
          return;
        }
        if (data.status === "helper_required") {
          provenance.textContent = data.message || "Cần Chrome Helper hoặc nhập giá thủ công.";
          var helperButton = document.getElementById("helper-open-btn");
          if (helperButton) helperButton.focus();
          return;
        }
        provenance.textContent = "Không thể làm mới giá. Bạn vẫn có thể nhập thủ công.";
      }).catch(function () {
        provenance.textContent = "Không thể làm mới giá. Bạn vẫn có thể nhập thủ công.";
      }).finally(function () {
        refreshButton.disabled = false;
      });
    });

    showSource(sourceInput.value, null, false);
    loadCacheProvenance();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
