#!/usr/bin/env python3
"""Temporary TDD applier for Shopee bulk affiliate Phase 1.

This file is committed only to bootstrap the feature on GitHub Actions because
this execution environment cannot clone github.com.  The workflow removes this
file before committing the final feature tree.
"""
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]

TEST_FILE = r'''"""Focused tests for Shopee bulk affiliate Phase 1."""
import os
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_tmp = tempfile.mkdtemp()
os.environ["ACP_DB"] = os.path.join(_tmp, "shopee_bulk.db")
os.environ.pop("ACP_ADMIN_PASSWORD", None)
os.environ.pop("ACP_ENV", None)

from acp.core import db  # noqa: E402
db.DB_PATH = os.environ["ACP_DB"]
from acp.core.db import connect, init_db, now  # noqa: E402
from acp.core.shopee_bulk_affiliate import (  # noqa: E402
    BulkAffiliateError,
    MAX_BULK_URLS,
    generate_bulk_links,
)


class ShopeeBulkAffiliateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        os.environ["SHOPEE_AFFILIATE_ID"] = "14354840000"

    def test_builds_official_redirect_shape(self):
        result = generate_bulk_links(
            "https://shopee.vn/Tai-nghe-i.12345.67890?sp_atk=tracking",
            affiliate_id="14354840000",
            sub_tag="threads",
        )[0]
        self.assertEqual(result.status, "CREATED")
        self.assertEqual(result.product_url, "https://shopee.vn/product/12345/67890")
        parsed = urlsplit(result.affiliate_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "s.shopee.vn")
        self.assertEqual(parsed.path, "/an_redir")
        query = parse_qs(parsed.query)
        self.assertEqual(query["origin_link"], ["https://shopee.vn/product/12345/67890"])
        self.assertEqual(query["affiliate_id"], ["14354840000"])
        self.assertEqual(len(query["sub_id"][0].split("-")), 5)
        self.assertEqual(query["sub_id"][0].split("-")[-1], "threads")

    def test_rejects_short_affiliate_and_external_urls_per_row(self):
        results = generate_bulk_links(
            "https://s.shopee.vn/abc\nhttps://example.com/product/1/2",
            affiliate_id="14354840000",
        )
        self.assertEqual([row.status for row in results], ["ERROR", "ERROR"])
        self.assertTrue(all(row.error for row in results))

    def test_rejects_missing_affiliate_id(self):
        with self.assertRaises(BulkAffiliateError):
            generate_bulk_links("https://shopee.vn/product/1/2", affiliate_id="")

    def test_enforces_500_url_limit_before_dedup(self):
        body = "\n".join(["https://shopee.vn/product/1/2"] * (MAX_BULK_URLS + 1))
        with self.assertRaises(BulkAffiliateError):
            generate_bulk_links(body, affiliate_id="14354840000")

    def test_deduplicates_same_product_in_one_batch(self):
        results = generate_bulk_links(
            "https://shopee.vn/A-i.123.456\nhttps://shopee.vn/product/123/456",
            affiliate_id="14354840000",
        )
        self.assertEqual(results[0].status, "CREATED")
        self.assertEqual(results[1].status, "DUPLICATE")
        self.assertEqual(results[0].affiliate_url, results[1].affiliate_url)

    def test_links_matching_existing_product_row(self):
        conn = connect()
        conn.execute("DELETE FROM product WHERE id='bulk-p1'")
        ts = now()
        conn.execute(
            """INSERT INTO product (
                   id, source, merchant, external_product_id, name, description,
                   current_price, original_price, commission_value, commission_rate,
                   category_code, rating, review_count, sold_count, image_url_original,
                   image_path_local, product_url, is_available, last_seen_at, created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
            ("bulk-p1", "manual_shopee", "shopee.vn", "998877", "Test product", "",
             100000, None, 0, None, "khac", None, 0, 0, None, None,
             "https://shopee.vn/product/123/998877", ts, ts, ts),
        )
        result = generate_bulk_links(
            "https://shopee.vn/product/123/998877",
            affiliate_id="14354840000",
            conn=conn,
        )[0]
        row = conn.execute(
            "SELECT affiliate_url, affiliate_link_status, affiliate_link_created_at FROM product WHERE id='bulk-p1'"
        ).fetchone()
        conn.close()
        self.assertEqual(result.status, "LINKED")
        self.assertEqual(result.product_id, "bulk-p1")
        self.assertEqual(row["affiliate_url"], result.affiliate_url)
        self.assertEqual(row["affiliate_link_status"], "READY")
        self.assertTrue(row["affiliate_link_created_at"])

    def test_bulk_page_and_post_are_registered(self):
        from acp.web.server import create_app
        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        page = client.get("/sanpham/shopee-bulk")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Tạo link Shopee hàng loạt", page.get_data(as_text=True))
        response = client.post(
            "/sanpham/shopee-bulk/generate",
            data={"product_urls": "https://shopee.vn/product/123/456", "sub_tag": "web"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("s.shopee.vn/an_redir", body)
        self.assertNotIn("SHOPEE_AFFILIATE_ID=14354840000", body)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ShopeeBulkAffiliateTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
'''

CORE_FILE = r'''"""Bulk Shopee Affiliate link generation for ACP.

Uses Shopee's documented redirect shape:
    https://s.shopee.vn/an_redir?origin_link=...&affiliate_id=...&sub_id=...

The module deliberately does not log in to Shopee, scrape pages, bypass bot
protection, or call a private Shopee API.  It only converts canonical Shopee
product URLs using an operator-configured Affiliate ID.
"""
from dataclasses import dataclass
import re
from typing import List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from ..adapters.shopee_affiliate import canonical_product_url, external_product_id
from .db import now

MAX_BULK_URLS = 500
SHOPEE_REDIRECT_URL = "https://s.shopee.vn/an_redir"
_ALLOWED_PRODUCT_HOSTS = {"shopee.vn", "www.shopee.vn"}


class BulkAffiliateError(ValueError):
    """Batch-level configuration or validation error safe to show to operator."""


@dataclass(frozen=True)
class BulkAffiliateResult:
    input_url: str
    status: str
    product_url: str = ""
    external_product_id: str = ""
    affiliate_url: str = ""
    sub_id: str = ""
    product_id: Optional[str] = None
    error: Optional[str] = None


def _validate_affiliate_id(value: str) -> str:
    affiliate_id = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,32}", affiliate_id):
        raise BulkAffiliateError(
            "Thiếu hoặc sai SHOPEE_AFFILIATE_ID. Hãy cấu hình Affiliate ID dạng số ở backend."
        )
    return affiliate_id


def _segment(value: str, fallback: str, max_len: int = 24) -> str:
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or fallback)[:max_len]


def _normalize_product_url(value: str) -> Tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise BulkAffiliateError("URL sản phẩm đang trống.")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise BulkAffiliateError("URL Shopee không hợp lệ.") from exc

    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or host not in _ALLOWED_PRODUCT_HOSTS:
        raise BulkAffiliateError("Chỉ nhận URL sản phẩm https://shopee.vn/...")
    if parsed.username is not None or parsed.password is not None or port not in (None, 443):
        raise BulkAffiliateError("URL Shopee chứa thành phần không được hỗ trợ.")
    if parsed.path.startswith("/an_redir"):
        raise BulkAffiliateError("Hãy nhập link sản phẩm gốc, không nhập link affiliate đã tạo.")

    product_url = canonical_product_url(raw)
    item_id = external_product_id(product_url)
    if not item_id or item_id.startswith("url_"):
        raise BulkAffiliateError(
            "Không nhận diện được mã sản phẩm. Dùng URL có dạng /product/<shop>/<item> hoặc -i.<shop>.<item>."
        )
    return product_url, item_id


def _build_sub_id(item_id: str, sub_tag: str) -> str:
    # Shopee documents five '-' separated values for sub_id.  Keep every
    # generated segment hyphen-free so the result is always exactly 5 parts.
    return "-".join((
        "acp",
        "bulk",
        "web",
        _segment(item_id, "product", 24),
        _segment(sub_tag, "default", 24),
    ))


def build_affiliate_url(product_url: str, affiliate_id: str, sub_tag: str = "default") -> Tuple[str, str]:
    affiliate_id = _validate_affiliate_id(affiliate_id)
    canonical, item_id = _normalize_product_url(product_url)
    sub_id = _build_sub_id(item_id, sub_tag)
    query = urlencode({
        "origin_link": canonical,
        "affiliate_id": affiliate_id,
        "sub_id": sub_id,
    })
    return f"{SHOPEE_REDIRECT_URL}?{query}", sub_id


def _link_existing_product(conn, item_id: str, affiliate_url: str) -> Optional[str]:
    if conn is None:
        return None
    row = conn.execute(
        """SELECT id
             FROM product
            WHERE lower(merchant)='shopee.vn' AND external_product_id=?
            ORDER BY CASE WHEN source='manual_shopee' THEN 0 ELSE 1 END,
                     updated_at DESC
            LIMIT 1""",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    product_id = row["id"] if hasattr(row, "keys") else row[0]
    timestamp = now()
    conn.execute(
        """UPDATE product
              SET affiliate_url=?, affiliate_short_url=NULL,
                  affiliate_link_status='READY', affiliate_link_error=NULL,
                  affiliate_link_created_at=?, updated_at=?
            WHERE id=?""",
        (affiliate_url, timestamp, timestamp, product_id),
    )
    return product_id


def generate_bulk_links(raw_text: str, affiliate_id: str, sub_tag: str = "default", conn=None) -> List[BulkAffiliateResult]:
    """Generate up to 500 links; invalid rows do not fail the whole batch."""
    affiliate_id = _validate_affiliate_id(affiliate_id)
    rows = [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]
    if not rows:
        raise BulkAffiliateError("Dán ít nhất 1 URL sản phẩm Shopee.")
    if len(rows) > MAX_BULK_URLS:
        raise BulkAffiliateError(f"Mỗi lần chỉ xử lý tối đa {MAX_BULK_URLS} URL.")

    results: List[BulkAffiliateResult] = []
    seen = {}
    for input_url in rows:
        try:
            product_url, item_id = _normalize_product_url(input_url)
            previous = seen.get(product_url)
            if previous is not None:
                results.append(BulkAffiliateResult(
                    input_url=input_url,
                    status="DUPLICATE",
                    product_url=product_url,
                    external_product_id=item_id,
                    affiliate_url=previous.affiliate_url,
                    sub_id=previous.sub_id,
                    product_id=previous.product_id,
                ))
                continue

            affiliate_url, sub_id = build_affiliate_url(product_url, affiliate_id, sub_tag)
            product_id = _link_existing_product(conn, item_id, affiliate_url)
            result = BulkAffiliateResult(
                input_url=input_url,
                status="LINKED" if product_id else "CREATED",
                product_url=product_url,
                external_product_id=item_id,
                affiliate_url=affiliate_url,
                sub_id=sub_id,
                product_id=product_id,
            )
            seen[product_url] = result
            results.append(result)
        except BulkAffiliateError as exc:
            results.append(BulkAffiliateResult(input_url=input_url, status="ERROR", error=str(exc)))
    return results
'''

WEB_FILE = r'''"""Server-rendered Shopee bulk affiliate workspace."""
import os

from flask import Blueprint, render_template, request

from ..core.db import connect
from ..core.shopee_bulk_affiliate import BulkAffiliateError, MAX_BULK_URLS, generate_bulk_links

bp = Blueprint("shopee_bulk", __name__)


def _pending_review_count():
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')"
        ).fetchone()[0]
    finally:
        conn.close()


def _summary(results):
    counts = {"created": 0, "linked": 0, "duplicate": 0, "error": 0}
    for row in results or []:
        key = row.status.lower()
        if key in counts:
            counts[key] += 1
    counts["total"] = sum(counts.values())
    return counts


def _render(*, raw_urls="", sub_tag="default", results=None, err=None, status=200):
    return render_template(
        "shopee_bulk_affiliate.html",
        page="san-pham",
        mode="bulk-affiliate",
        raw_urls=raw_urls,
        sub_tag=sub_tag,
        results=results or [],
        summary=_summary(results),
        err=err,
        max_urls=MAX_BULK_URLS,
        affiliate_configured=bool(os.environ.get("SHOPEE_AFFILIATE_ID", "").strip()),
        pending_review=_pending_review_count(),
    ), status


@bp.get("/sanpham/shopee-bulk")
def page():
    return _render()


@bp.post("/sanpham/shopee-bulk/generate")
def generate():
    raw_urls = request.form.get("product_urls", "")
    sub_tag = request.form.get("sub_tag", "default")
    affiliate_id = os.environ.get("SHOPEE_AFFILIATE_ID", "")
    conn = connect()
    try:
        results = generate_bulk_links(raw_urls, affiliate_id, sub_tag=sub_tag, conn=conn)
    except BulkAffiliateError as exc:
        return _render(raw_urls=raw_urls, sub_tag=sub_tag, err=str(exc), status=400)
    finally:
        conn.close()
    return _render(raw_urls=raw_urls, sub_tag=sub_tag, results=results)


def register_shopee_bulk_routes(app):
    app.register_blueprint(bp)
'''

TEMPLATE_FILE = r'''{% extends "base.html" %}
{% block title %}Shopee Bulk Affiliate — ACP{% endblock %}
{% block content %}
<div class="page-header">
  <div>
    <div class="eyebrow">Shopee Affiliate Factory</div>
    <h1>Tạo link Shopee hàng loạt</h1>
    <p class="lede">Dán tối đa {{ max_urls }} link sản phẩm Shopee. ACP tạo affiliate URL trực tiếp theo Affiliate ID cấu hình ở backend.</p>
  </div>
  <a class="btn btn--ghost" href="/duyet">Chờ duyệt{% if pending_review %} · {{ pending_review }}{% endif %}</a>
</div>

<div class="tabs" role="navigation" aria-label="Chế độ sản phẩm">
  <a class="tab" href="{{ url_for('products') }}">Catalog sản phẩm</a>
  <a class="tab" href="{{ url_for('products', mode='affiliate') }}">Nhập link affiliate</a>
  <a class="tab tab--active" href="{{ url_for('shopee_bulk.page') }}">Tạo link Shopee hàng loạt</a>
</div>

{% if err %}<div class="alert alert--error"><strong>Không thể tiếp tục.</strong><span>{{ err }}</span></div>{% endif %}
{% if not affiliate_configured %}
<div class="alert alert--warning"><strong>Chưa cấu hình Affiliate ID.</strong><span>Thêm <code>SHOPEE_AFFILIATE_ID</code> vào <code>shared/.env.local</code>. Giá trị này chỉ được đọc ở backend.</span></div>
{% endif %}

<section class="card card--elevated">
  <div class="section-heading">
    <div>
      <span class="status-badge status-badge--accent">Shopee Direct</span>
      <h2>Product URL → Affiliate URL</h2>
      <p class="note">Mỗi dòng 1 link sản phẩm. Không dùng link <code>s.shopee.vn</code> đã rút gọn và không cần đăng nhập Shopee trong ACP.</p>
    </div>
  </div>
  <form method="post" action="{{ url_for('shopee_bulk.generate') }}">
    <input type="hidden" name="_csrf" value="{{ csrf_token }}">
    <div class="form-grid">
      <div class="field field--full">
        <label for="product_urls">URL sản phẩm Shopee</label>
        <textarea id="product_urls" name="product_urls" rows="12" required
                  placeholder="https://shopee.vn/product/123/456&#10;https://shopee.vn/Ten-san-pham-i.123.789">{{ raw_urls }}</textarea>
      </div>
      <div class="field">
        <label for="sub_tag">Nhãn tracking</label>
        <input id="sub_tag" name="sub_tag" value="{{ sub_tag or 'default' }}" maxlength="24"
               placeholder="threads, facebook, campaign01...">
      </div>
      <div class="field">
        <label>Giới hạn</label>
        <input value="{{ max_urls }} URL / lần" disabled>
      </div>
    </div>
    <div class="form-actions">
      <button class="btn btn--primary" type="submit" {% if not affiliate_configured %}disabled{% endif %}>Tạo affiliate link</button>
    </div>
  </form>
</section>

{% if results %}
<section class="card card--elevated" style="margin-top:18px">
  <div class="section-heading">
    <div>
      <div class="eyebrow">Kết quả</div>
      <h2>{{ summary.total }} URL đã xử lý</h2>
      <p class="note">Tạo mới: {{ summary.created }} · Gắn Product DB: {{ summary.linked }} · Trùng: {{ summary.duplicate }} · Lỗi: {{ summary.error }}</p>
    </div>
  </div>
  <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Trạng thái</th><th>Sản phẩm</th><th>Sub ID</th><th>Affiliate link</th></tr></thead>
      <tbody>
      {% for r in results %}
        <tr>
          <td>
            {% if r.status == 'ERROR' %}<span class="status-badge status-badge--danger">Lỗi</span>
            {% elif r.status == 'LINKED' %}<span class="status-badge status-badge--success">Đã gắn DB</span>
            {% elif r.status == 'DUPLICATE' %}<span class="status-badge">Trùng</span>
            {% else %}<span class="status-badge status-badge--accent">Đã tạo</span>{% endif %}
          </td>
          <td>
            {% if r.product_url %}<code title="{{ r.product_url }}">{{ r.product_url }}</code>
            {% else %}<span>{{ r.input_url }}</span>{% endif %}
            {% if r.error %}<div class="note">{{ r.error }}</div>{% endif %}
          </td>
          <td><code>{{ r.sub_id or '—' }}</code></td>
          <td>
            {% if r.affiliate_url %}
              <code title="{{ r.affiliate_url }}">{{ r.affiliate_url }}</code>
              <button class="btn btn--small js-copy-affiliate" type="button" data-url="{{ r.affiliate_url }}">Copy</button>
            {% else %}—{% endif %}
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>
<script>
document.querySelectorAll('.js-copy-affiliate').forEach(function (button) {
  button.addEventListener('click', function () {
    var value = button.dataset.url || '';
    navigator.clipboard.writeText(value).then(function () {
      var old = button.textContent;
      button.textContent = 'Đã copy';
      setTimeout(function () { button.textContent = old; }, 1200);
    });
  });
});
</script>
{% endif %}
{% endblock %}
'''

DOC_FILE = r'''# Shopee Bulk Affiliate — Phase 1

Phase 1 bổ sung một workspace để chuyển tối đa 500 URL sản phẩm Shopee thành
Affiliate URL trong một lần, dùng đúng Affiliate ID của tài khoản Shopee Affiliate.

## Cấu hình

Thêm vào `~/Downloads/ACP/shared/.env.local`:

```env
SHOPEE_AFFILIATE_ID=14354840000
```

Không commit giá trị thật vào Git. ACP chỉ đọc cấu hình này ở backend.
Affiliate URL đầu ra đương nhiên chứa `affiliate_id` theo định dạng link Shopee công bố.

## Sử dụng

1. Mở `/sanpham`.
2. Chọn **Tạo link Shopee hàng loạt**.
3. Dán mỗi dòng một URL sản phẩm Shopee, tối đa 500 URL.
4. Điền nhãn tracking nếu cần (ví dụ `threads`, `facebook`, `campaign01`).
5. Bấm **Tạo affiliate link**.

ACP chuẩn hoá URL về dạng `https://shopee.vn/product/<shop>/<item>`, tạo `sub_id`
5 phần dạng `acp-bulk-web-<item>-<tag>`, rồi tạo URL:

```text
https://s.shopee.vn/an_redir?origin_link=...&affiliate_id=...&sub_id=...
```

Nếu Product DB đã có một sản phẩm Shopee trùng `external_product_id`, ACP cập nhật
các field affiliate hiện có của row đó (`affiliate_url`, `affiliate_link_status`,
`affiliate_link_created_at`). URL chưa có Product DB vẫn được trả về giao diện để copy,
nhưng Phase 1 không tạo Product giả chỉ từ URL.

## Giới hạn chủ đích

- Không đăng nhập Shopee.
- Không browser/headless automation.
- Không bypass CAPTCHA/anti-bot.
- Không gọi private Shopee API.
- Không tự publish nội dung.
- Không nhận short link `s.shopee.vn/...` làm đầu vào vì cần network redirect để xác định product ID.
'''


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def install_tests():
    _write(ROOT / "tests" / "test_shopee_bulk_affiliate.py", TEST_FILE)


def install_implementation():
    _write(ROOT / "core" / "shopee_bulk_affiliate.py", CORE_FILE)
    _write(ROOT / "web" / "shopee_bulk.py", WEB_FILE)
    _write(ROOT / "web" / "templates" / "shopee_bulk_affiliate.html", TEMPLATE_FILE)
    _write(ROOT / "docs" / "SHOPEE_BULK_AFFILIATE.md", DOC_FILE)

    server_path = ROOT / "web" / "server.py"
    server = server_path.read_text(encoding="utf-8")
    import_anchor = "from ..core.products import ProductFilters, ProductService, SyncAlreadyRunning\n"
    import_line = "from .shopee_bulk import register_shopee_bulk_routes\n"
    if import_line not in server:
        if import_anchor not in server:
            raise RuntimeError("server.py import anchor changed")
        server = server.replace(import_anchor, import_anchor + import_line, 1)

    config_anchor = '    app.config["SHOPEE_SOURCE_FACTORY"] = ManualShopeeSource\n'
    register_line = "    register_shopee_bulk_routes(app)\n"
    if register_line not in server:
        if config_anchor not in server:
            raise RuntimeError("server.py config anchor changed")
        server = server.replace(config_anchor, config_anchor + register_line, 1)
    server_path.write_text(server, encoding="utf-8")

    products_path = ROOT / "web" / "templates" / "products.html"
    products = products_path.read_text(encoding="utf-8")
    if "shopee_bulk.page" not in products:
        old_tabs = '''<div class="tabs" role="navigation" aria-label="Chế độ sản phẩm">\n  <a class="tab {{ 'tab--active' if mode != 'affiliate' }}" href="{{ url_for('products') }}">Catalog sản phẩm</a>\n  <a class="tab {{ 'tab--active' if mode == 'affiliate' }}" href="{{ url_for('products', mode='affiliate') }}">Nhập link affiliate</a>\n</div>'''
        new_tabs = '''<div class="tabs" role="navigation" aria-label="Chế độ sản phẩm">\n  <a class="tab {{ 'tab--active' if mode in ('catalog', 'search') }}" href="{{ url_for('products') }}">Catalog sản phẩm</a>\n  <a class="tab {{ 'tab--active' if mode == 'affiliate' }}" href="{{ url_for('products', mode='affiliate') }}">Nhập link affiliate</a>\n  <a class="tab" href="{{ url_for('shopee_bulk.page') }}">Tạo link Shopee hàng loạt</a>\n</div>'''
        if old_tabs not in products:
            raise RuntimeError("products.html tabs anchor changed")
        products = products.replace(old_tabs, new_tabs, 1)
        products_path.write_text(products, encoding="utf-8")

    env_path = ROOT / ".env.example"
    env = env_path.read_text(encoding="utf-8")
    if "SHOPEE_AFFILIATE_ID=" not in env:
        env = env.rstrip() + "\n\n# Shopee Affiliate — bulk product URL converter\nSHOPEE_AFFILIATE_ID=\n"
        env_path.write_text(env, encoding="utf-8")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode not in {"tests", "implementation", "all"}:
        raise SystemExit("usage: apply_shopee_bulk_phase1.py [tests|implementation|all]")
    if mode in {"tests", "all"}:
        install_tests()
    if mode in {"implementation", "all"}:
        install_implementation()


if __name__ == "__main__":
    main()
