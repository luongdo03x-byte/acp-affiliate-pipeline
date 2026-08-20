// Phase 4 UI polish. This file never submits create/approve forms automatically.
(function () {
  "use strict";

  function copyButton(value, label) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn--small btn--ghost";
    button.textContent = label || "Copy";
    button.addEventListener("click", function () {
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(value).then(function () {
        var old = button.textContent;
        button.textContent = "Đã copy";
        setTimeout(function () { button.textContent = old; }, 1200);
      }).catch(function () {});
    });
    return button;
  }

  function safeLink(url, label) {
    var a = document.createElement("a");
    a.className = "shopee-link";
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = label;
    return a;
  }

  function setupConfirmationPreview() {
    var form = document.querySelector('form[action="/sanpham/affiliate/create"]');
    if (!form) return;
    var actions = form.querySelector(".form-actions");
    if (!actions) return;

    var previewButton = document.createElement("button");
    previewButton.type = "button";
    previewButton.className = "btn btn--ghost";
    previewButton.textContent = "Xem trước bài";
    actions.insertBefore(previewButton, actions.lastElementChild || null);

    var panel = document.createElement("section");
    panel.className = "shopee-preview";
    panel.hidden = true;
    panel.setAttribute("aria-live", "polite");
    actions.parentNode.insertBefore(panel, actions);

    function renderPreview(data) {
      panel.innerHTML = "";
      panel.hidden = false;

      var heading = document.createElement("div");
      heading.className = "shopee-preview__heading";
      heading.innerHTML = "<div><span class='status-badge status-badge--accent'>Preview sơ bộ</span>" +
        "<h3>Xem trước trước khi tạo draft</h3></div>";
      panel.appendChild(heading);

      var grid = document.createElement("div");
      grid.className = "shopee-preview__grid";
      var image = document.createElement("img");
      image.className = "shopee-preview__image";
      image.src = data.image_url;
      image.alt = "Ảnh sản phẩm preview";
      grid.appendChild(image);

      var info = document.createElement("div");
      info.className = "shopee-preview__info";
      var name = document.createElement("strong");
      name.textContent = data.name;
      var price = document.createElement("div");
      price.className = "shopee-preview__price";
      price.textContent = Number(data.current_price || 0).toLocaleString("vi-VN") + "đ";
      var source = document.createElement("div");
      source.className = "dim";
      source.textContent = "Nguồn metadata: " + (data.metadata_source || "manual");
      info.append(name, price, source);
      grid.appendChild(info);
      panel.appendChild(grid);

      var caption = document.createElement("pre");
      caption.className = "shopee-preview__caption";
      caption.textContent = data.caption;
      panel.appendChild(caption);

      var disclosure = document.createElement("div");
      disclosure.className = "shopee-preview__disclosure";
      disclosure.textContent = "Disclosure: " + data.disclosure;
      panel.appendChild(disclosure);

      var links = document.createElement("div");
      links.className = "shopee-preview__links";
      links.append(
        safeLink(data.product_url, "Mở sản phẩm"),
        copyButton(data.product_url, "Copy product link"),
        safeLink(data.affiliate_url, "Mở affiliate link"),
        copyButton(data.affiliate_url, "Copy affiliate link")
      );
      panel.appendChild(links);

      var channels = document.createElement("div");
      channels.className = "review-tags";
      (data.channels || []).forEach(function (channel) {
        var chip = document.createElement("span");
        chip.className = "tag";
        chip.textContent = "[" + channel.platform + "] " + channel.handle;
        channels.appendChild(chip);
      });
      panel.appendChild(channels);

      (data.warnings || []).forEach(function (warning) {
        var warn = document.createElement("p");
        warn.className = "note";
        warn.textContent = warning;
        panel.appendChild(warn);
      });
    }

    previewButton.addEventListener("click", function () {
      previewButton.disabled = true;
      var payload = new FormData(form);
      fetch("/sanpham/affiliate/preview", {
        method: "POST",
        credentials: "same-origin",
        body: payload,
      }).then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.error || "Không tạo được preview");
          return data;
        });
      }).then(renderPreview).catch(function (error) {
        panel.hidden = false;
        panel.innerHTML = "";
        var alert = document.createElement("div");
        alert.className = "alert alert--warning";
        alert.textContent = error.message || "Không tạo được preview.";
        panel.appendChild(alert);
      }).finally(function () {
        previewButton.disabled = false;
      });
    });
  }

  function setupReviewPolish() {
    document.querySelectorAll('form[action^="/duyet/"][action$="/approve"]').forEach(function (form) {
      var match = form.getAttribute("action").match(/^\/duyet\/([^/]+)\/approve$/);
      if (!match) return;
      var postId = match[1];
      var card = form.closest(".review-card");
      if (!card) return;
      card.classList.add("review-card--polished");

      var caption = form.querySelector('textarea[name="caption"]');
      var counter = form.querySelector(".review-actions .dim.mono");
      if (caption && counter) {
        function updateCount() {
          counter.textContent = caption.value.length + "/500 ký tự";
        }
        caption.addEventListener("input", updateCount);
        updateCount();

        var preview = document.createElement("div");
        preview.className = "threads-caption-preview";
        preview.textContent = caption.value;
        caption.parentNode.appendChild(preview);
        caption.addEventListener("input", function () {
          preview.textContent = caption.value;
        });
      }

      fetch("/api/review/shopee-context?post_id=" + encodeURIComponent(postId), {
        credentials: "same-origin",
      }).then(function (response) {
        return response.ok ? response.json() : null;
      }).then(function (data) {
        if (!data || !data.shopee) return;
        var head = card.querySelector(".review-card__head") || card;
        var badges = document.createElement("div");
        badges.className = "review-tags shopee-review-context";
        var direct = document.createElement("span");
        direct.className = "tag ok";
        direct.textContent = "Shopee Direct";
        var source = document.createElement("span");
        source.className = "tag";
        source.textContent = data.source || "shopee_direct";
        badges.append(direct, source);
        head.parentNode.insertBefore(badges, head.nextSibling);

        if (!data.safe_links) return;
        var links = document.createElement("div");
        links.className = "shopee-review-links";
        links.append(
          safeLink(data.product_url, "Product link"),
          copyButton(data.product_url, "Copy product"),
          safeLink(data.affiliate_url, "Affiliate link"),
          copyButton(data.affiliate_url, "Copy affiliate")
        );
        badges.parentNode.insertBefore(links, badges.nextSibling);
      }).catch(function () {});
    });
  }

  function boot() {
    setupConfirmationPreview();
    setupReviewPolish();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
