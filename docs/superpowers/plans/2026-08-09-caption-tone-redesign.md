# Caption Tone Redesign ("phát hiện & chia sẻ") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the 9 opening hooks and 4 body templates that generate Threads
post captions so they read like a person who noticed a good deal and is
sharing it, instead of a data report ("Trang bán ghi nhận...", "Đang bán
100.250đ." standing alone).

**Architecture:** No structural change. `core/playbook.py` still exposes 9
`HOOKS` render functions and a `CTA_LIBRARY`; `core/content.py` still has 4
`TEMPLATES` and calls `playbook.render_hook()` + `TEMPLATES[code].format()` to
assemble `hook\n\nbody\n\ncta\nlink` before `_fit()` appends the disclosure.
Only the literal Vietnamese strings inside these functions/dicts change.

**Tech Stack:** Python 3, existing test harness (`python3 -m acp.tests.test_pipeline`,
`python3 -m acp.tests.test_pilot`, both invoked by `./manage.sh test`).

## Global Constraints

- Do not touch `DISCLOSURE_DEFAULT`, `CTA_LIBRARY`, `BANNED_SUPERLATIVES`,
  `FABRICATED_EXPERIENCE`, `EFFICACY_CLAIMS`, or any `validate()` logic.
- No hook or template string may claim first-person product usage (no
  "mình đã dùng", "mình xài", "sau khi dùng", etc.) — `content.validate()`
  must return `[]` for every caption generated from the new copy.
- `spec_highlight` must keep an explicit attribution phrase before the
  product-description excerpt (currently "Thông tin từ trang bán:") — wording
  can change, the attribution itself cannot be dropped.
- Keep `core/valuepost.py` untouched — out of scope for this plan.
- Keep the hook × template combinatorial structure (do not merge hooks and
  templates into one function per hook).
- `./manage.sh test` must print `TEST_OK` at the end after each task that
  touches test files.

---

### Task 1: Rewrite hooks + `_social_bits` in `core/playbook.py`

**Files:**
- Modify: `core/playbook.py:28-92` (functions `_social_bits`, `_h_gia_giam`,
  `_h_so_sanh`, `_h_khan_hiem`, `_h_xa_hoi`, `_h_hang_moi`, `_h_tiet_kiem`,
  `_h_truc_tiep`; `_h_cau_hoi` and `_h_canh_bao` are unchanged)
- Test: `tests/test_pilot.py:274-290` (function `test_playbook_hooks_and_cta`)

**Interfaces:**
- Consumes: nothing new — same `product` dict/Row access pattern via `_get()`,
  same `_fmt_vnd()` helper already in the file.
- Produces: `playbook.render_hook(code, product, discount_pct) -> str` keeps
  its exact signature and return type (str) — `core/content.py` Task 2 calls
  this unchanged.

- [ ] **Step 1: Write the failing regression test**

Edit `tests/test_pilot.py`, inside `test_playbook_hooks_and_cta()` (after the
existing CTA checks, before the function ends), add:

```python
    banned_phrases = ["trang bán ghi nhận", "có số liệu đáng chú ý"]
    social_product = {"name": "Sản phẩm test", "current_price": 100000,
                       "sold_count": 512, "rating": 4.8, "review_count": 200,
                       "category_code": "gia-dung"}
    no_social_product = {"name": "Sản phẩm test", "current_price": 100000,
                          "sold_count": 0, "rating": None, "review_count": 0,
                          "category_code": "gia-dung"}
    for code in playbook.hook_codes():
        for product in (social_product, no_social_product):
            text = playbook.render_hook(code, product, 0.15).lower()
            check(f"hook {code} không còn giọng báo cáo số liệu",
                  all(p not in text for p in banned_phrases), text)
            check(f"hook {code} không bịa trải nghiệm sử dụng",
                  content.validate(f"{playbook.render_hook(code, product, 0.15)}\n\n"
                                    f"{playbook.CTA_LIBRARY[0]}\nhttps://x.test/y\n\n"
                                    f"{content.DISCLOSURE_DEFAULT}") == [],
                  playbook.render_hook(code, product, 0.15))
    check("H5 dùng số liệu kiểu 'người mua rồi' chứ không phải 'đã bán ... lượt'",
          "người mua rồi" in playbook.render_hook("H5_XAHOI", social_product, 0).lower())
```

`test_pilot.py` already imports `content` at the top (check with
`grep -n "^from acp.core import\|^from acp\.core\.content\|import content"
tests/test_pilot.py` — it imports `from acp.core import content` alongside
`playbook`, so no new import is needed).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Downloads/ACP && ACP_ADAPTER=mock ACP_SOURCE=mock releases/2.0/acp/.venv/bin/python -m acp.tests.test_pilot 2>&1 | grep -A2 "hook H"`
Expected: FAIL — several `check(...)` lines print `✗` for
"không còn giọng báo cáo số liệu" (H2, H5) and "người mua rồi" (H5), because
the old strings still contain "có số liệu đáng chú ý" / "Trang bán ghi
nhận ... đã bán ... lượt".

- [ ] **Step 3: Rewrite the hook functions and `_social_bits`**

Replace lines 28-92 of `core/playbook.py` with:

```python
def _social_bits(product) -> str:
    sold = _get(product, "sold_count", 0) or 0
    rating = _get(product, "rating", 0) or 0
    reviews = _get(product, "review_count", 0) or 0
    bits = []
    if sold >= 100:
        bits.append(f"{sold:,}".replace(",", ".") + " người mua rồi")
    if rating and reviews >= 20:
        bits.append(f"đánh giá {rating:g}/5")
    return ", ".join(bits)


def _h_gia_giam(product, discount_pct):
    pct = max(1, round((discount_pct or 0) * 100))
    return (f"Giá đang treo {_fmt_vnd(_get(product, 'current_price', 0))}, mềm hơn tầm {pct}% "
            "so với bình thường -- thấy hời nên để lại đây.")


def _h_so_sanh(product, discount_pct):
    return "So mấy món cùng tầm giá thì cái này có vẻ đáng tiền hơn hẳn."


def _h_khan_hiem(product, discount_pct):
    return "Nhìn số lượng còn lại thì chắc không trụ lâu, ai cần thì cân nhắc sớm nhé."


def _h_cau_hoi(product, discount_pct):
    return "Có ai đang tìm món kiểu này không?"


def _h_xa_hoi(product, discount_pct):
    bits = _social_bits(product)
    if bits:
        return f"Thấy {bits}, để lại đây cho ai đang cần."
    return "Lướt thấy món này trông ổn, để lại đây cho ai quan tâm."


def _h_hang_moi(product, discount_pct):
    return "Mới thấy món này, để lại thông tin cơ bản cho ai đang cần."


def _h_tiet_kiem(product, discount_pct):
    return "Mua đúng lúc này thì đỡ được một khoản, để lại thông tin cho ai cần."


def _h_canh_bao(product, discount_pct):
    return "Giá đang thấp hơn mức thường thấy, sợ lên lại nên chia sẻ luôn."


def _h_truc_tiep(product, discount_pct):
    return f"{_get(product, 'name', '')[:100]} — đang có giá {_fmt_vnd(_get(product, 'current_price', 0))}."
```

Do not touch `HOOKS`, `CTA_LIBRARY`, `hook_codes()`, `pick_hook()`,
`render_hook()`, `pick_cta()`, `contains_multiple_cta()` — only the function
bodies above change.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Downloads/ACP && ACP_ADAPTER=mock ACP_SOURCE=mock releases/2.0/acp/.venv/bin/python -m acp.tests.test_pilot 2>&1 | tail -5`
Expected: last line `NNN đạt, 0 hỏng` (no `✗`).

- [ ] **Step 5: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add core/playbook.py tests/test_pilot.py
git commit -m "feat: viết lại giọng 9 hook mở đầu theo hướng phát hiện & chia sẻ"
```

---

### Task 2: Rewrite `TEMPLATES` + `_social_proof` in `core/content.py`

**Files:**
- Modify: `core/content.py:39-57` (`TEMPLATES` dict), `core/content.py:78-85`
  (`_social_proof`)
- Test: `tests/test_pipeline.py` — new function `test_caption_tone()`

**Interfaces:**
- Consumes: `playbook.render_hook()` from Task 1 (unchanged signature).
- Produces: `content.generate(product, template_code, affiliate_link,
  discount_pct=0.0, disclosure=DISCLOSURE_DEFAULT, hook_code=None,
  rng=None) -> str` keeps its exact signature — `core/pipeline.py` calls this
  unchanged, no other file needs edits.

- [ ] **Step 1: Write the failing regression test**

Add to `tests/test_pipeline.py`, right after `test_content_guards()` (before
`def test_scoring():`):

```python
def test_caption_tone():
    print("\nGiọng văn caption (phát hiện & chia sẻ)")
    banned_phrases = ["trang bán ghi nhận", "có số liệu đáng chú ý",
                       "thông tin từ trang bán"]
    product_no_social = {"name": "Quần linen giả váy chất đũi tơ", "current_price": 100250,
                          "original_price": None, "sold_count": 0, "rating": None,
                          "review_count": 0, "category_code": "thoi-trang",
                          "description": "Chất đũi tơ, thiết kế cạp nhúm."}
    product_with_social = dict(product_no_social, sold_count=512, rating=4.8, review_count=200)
    for code in content.TEMPLATES:
        for product in (product_no_social, product_with_social):
            caption = content.generate(product, code, "https://go.isclix.com/x?sub1=abc",
                                        discount_pct=0.1, hook_code="H4_CAUHOI")
            low = caption.lower()
            check(f"template {code} không còn giọng báo cáo số liệu",
                  all(p not in low for p in banned_phrases), caption)
            check(f"template {code} không để câu giá đứng riêng một đoạn",
                  "\n\nđang bán" not in low, caption)
            check(f"template {code} qua được validate()",
                  content.validate(caption) == [], content.validate(caption))
    check("_social_proof dùng 'người mua rồi' chứ không phải 'đã bán ... lượt'",
          "người mua rồi" in content._social_proof(product_with_social).lower())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Downloads/ACP && ACP_ADAPTER=mock ACP_SOURCE=mock releases/2.0/acp/.venv/bin/python -m acp.tests.test_pipeline 2>&1 | grep -B1 -A1 "✗\|template "`
Expected: FAIL — `comparison` template trips "không để câu giá đứng riêng một
đoạn" (contains `"\n\nĐang bán"`), and `deal_roundup`/`spec_highlight`/
`comparison` trip the mechanical-phrase check.

- [ ] **Step 3: Rewrite `TEMPLATES` and `_social_proof`**

Replace `core/content.py:39-57`:

```python
TEMPLATES = {
    "price_drop": (
        "{name}\n\n"
        "Giá hiện {price}, mềm hơn khoảng {discount}% so với 30 ngày qua. "
        "{social}"
    ),
    "spec_highlight": (
        "{name}\n\n"
        "Giá {price}. {social} Bên bán mô tả: {highlight}."
    ),
    "deal_roundup": (
        "Lướt nhóm {category} hôm nay thấy món này giá khá hời:\n\n"
        "{name} — {price}. {social}"
    ),
    "comparison": (
        "Trong tầm giá {price_band}, {name} là món khá ổn — giá đang {price}. "
        "{social}"
    ),
}
```

Replace `core/content.py:78-85`:

```python
def _social_proof(product) -> str:
    sold, rating, reviews = product["sold_count"] or 0, product["rating"] or 0, product["review_count"] or 0
    bits = []
    if sold >= 100:
        bits.append(f"{sold:,}".replace(",", ".") + " người mua rồi")
    if rating and reviews >= 20:
        bits.append(f"đánh giá {rating:g}/5")
    return ("Cũng " + ", ".join(bits) + ".") if bits else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Downloads/ACP && ACP_ADAPTER=mock ACP_SOURCE=mock releases/2.0/acp/.venv/bin/python -m acp.tests.test_pipeline 2>&1 | tail -5`
Expected: last line `NNN đạt, 0 hỏng`.

- [ ] **Step 5: Add the new test to the `__main__` call list**

In `tests/test_pipeline.py`, find the `if __name__ == "__main__":` block
(around line 249) and add `test_caption_tone()` right after
`test_content_guards()` in the call sequence, before `test_scoring()`.

- [ ] **Step 6: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add core/content.py tests/test_pipeline.py
git commit -m "feat: viết lại 4 template thân bài, bỏ câu giá đứng riêng một đoạn"
```

---

### Task 3: Full verification and visual confirmation

**Files:** none modified — verification only.

**Interfaces:** none — this task only runs commands and reads output.

- [ ] **Step 1: Run the full release test suite**

```bash
cd ~/Downloads/ACP && ./manage.sh test 2>&1 | tail -20
```
Expected: ends with `TEST_OK` (this also runs `run.py doctor`, which must
still report "Sẵn sàng.").

- [ ] **Step 2: Print sample captions for the exact case from the bug report**

```bash
cd ~/Downloads/ACP/releases/2.0/acp && ACP_ADAPTER=mock ACP_SOURCE=mock .venv/bin/python - <<'PY'
from core import content, playbook
product = {"name": "Quần linen giả váy chất đũi tơ thiết kế cạp nhúm hàng 2 lớp_Linhchi.studio",
           "current_price": 100250, "original_price": None, "sold_count": 0,
           "rating": None, "review_count": 0, "category_code": "thoi-trang",
           "description": ""}
for code in content.TEMPLATES:
    for hook in ("H4_CAUHOI", "H1_GIAGIAM", "H5_XAHOI"):
        cap = content.generate(product, code, "https://go.isclix.com/x?sub1=abc",
                                discount_pct=0.0, hook_code=hook)
        print(f"--- {code} / {hook} ---")
        print(cap)
        print()
PY
```

Read the output. Confirm by eye: no line is a standalone "Đang bán ...đ."
paragraph, no "trang bán ghi nhận" / "có số liệu đáng chú ý" text appears.
This does not touch the live database — it only calls the pure caption
functions with an in-memory product dict.

- [ ] **Step 3: Restart the running release so the web app serves the new copy**

```bash
cd ~/Downloads/ACP && ./manage.sh restart 2>&1
```
Expected: `ACP_STARTED pid=... url=http://127.0.0.1:5000` then
`NGROK_STARTED ...`.

- [ ] **Step 4: Report to the user**

Tell the user the new caption copy is live and ask them to create one fresh
post from `/sanpham` (affiliate import, same flow as before) to see the new
tone on `/duyet` with a real product. Do not create that post automatically —
per `AGENTS.md`, do not run demo/seed content against the live database
without the operator driving it.

- [ ] **Step 5: Final commit (if Step 3/4 produced no file changes, skip)**

No files change in this task, so there is nothing to commit here — Task 1
and Task 2 commits already cover all code changes.
