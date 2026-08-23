# Reviewer Caption Engine v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay caption Shopee Auto dạng quảng cáo dài bằng caption Threads ngắn, có hook mạnh, giọng reviewer tự nhiên và chỉ dùng fact thật từ Product/CSV.

**Architecture:** Giữ nguyên scheduler, queue, publisher và `content.validate()`. Tạo `core/reviewer_caption.py` làm lớp sinh caption chuyên cho `SHOPEE_AFFILIATE`; `core/content.py::generate()` chỉ route Shopee sang engine mới, provider khác giữ nguyên hành vi. Engine chọn một selling angle duy nhất từ dữ liệu thật (sold count, price, size/feature trong title), dựng 3–5 dòng và dùng LLM hiện có chỉ như lớp rewrite an toàn có fallback deterministic.

**Tech Stack:** Python 3.12, unittest, SQLite rows/dicts, existing ACP content validation/LLM adapter.

**Spec:** Quy tắc đã duyệt trong cuộc hội thoại ngày 2026-08-22, dựa trên ebook Threads: hook ngắn, dòng ngắn, giọng đối thoại, giá trị trước bán hàng, một CTA.

## Global Constraints

- Shopee caption tổng ưu tiên 250–420 ký tự và không vượt giới hạn Threads 500 ký tự.
- Hook mục tiêu tối đa 12 từ; không chép nguyên title sản phẩm.
- Mỗi caption chỉ tập trung một angle chính.
- Chỉ dùng dữ liệu có thật: giá, sold count, shop, title/description, discount đã tính từ lịch sử giá.
- Không bịa trải nghiệm cá nhân, công dụng, urgency hay social proof.
- Không dùng các cụm quảng cáo sáo rỗng như “hoàn hảo”, “tuyệt vời”, “không thể bỏ lỡ”, “sự lựa chọn lý tưởng”.
- Một CTA mềm; giữ nguyên affiliate URL.
- Không thay đổi publisher, preflight, quota, scheduler hoặc logic chọn sản phẩm.

---

### Task 1: Characterize reviewer caption behavior

**Files:**
- Create: `tests/test_reviewer_caption_v2.py`

**Interfaces:**
- Consumes: `core.content.generate(product, template_code, affiliate_link, ...)`
- Produces: behavioral contract for Shopee captions.

- [ ] **Step 1: Write failing tests**

Test a Shopee fashion product with `sold_count=40000` and verify first line is short, contains a real hook signal, caption keeps URL, does not repeat full title, does not contain fabricated-experience phrases, and stays <= 500 chars. Test a non-Shopee product to confirm legacy path remains available.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_reviewer_caption_v2 -v`
Expected: at least one Shopee reviewer-style assertion FAIL against current `content.generate()`.

- [ ] **Step 3: Commit tests only**

Commit message: `test: define reviewer caption v2 behavior`.

### Task 2: Build deterministic reviewer engine

**Files:**
- Create: `core/reviewer_caption.py`

**Interfaces:**
- Produces: `generate(product, affiliate_link, *, discount_pct=0.0, hook_code=None, llm_fn=None) -> str`

- [ ] **Step 1: Implement minimal signal extraction**

Normalize sqlite Row/dict; clean title; extract safe feature phrases from title; format sold count compactly (`40k+`, `7k+`, etc. only from real `sold_count`).

- [ ] **Step 2: Implement angle selection**

Priority: audience/pain-point token (bigsize/size range) -> strong social proof (`sold_count >= 1000`) -> distinctive feature -> price/deal fallback. Use one angle only.

- [ ] **Step 3: Implement 3–5 line deterministic caption**

Structure: hook -> one observation -> one supporting fact -> soft CTA -> URL. Keep prose conversational and never claim first-hand use.

- [ ] **Step 4: Run GREEN for deterministic tests**

Run: `python -m unittest tests.test_reviewer_caption_v2 -v`
Expected: PASS.

### Task 3: Safe LLM rewrite and active Shopee integration

**Files:**
- Modify: `core/content.py`
- Modify: `core/reviewer_caption.py`
- Test: `tests/test_reviewer_caption_v2.py`

**Interfaces:**
- `content.set_llm()` remains the existing configuration surface.
- `content.generate()` routes only `provider='SHOPEE_AFFILIATE'` through Reviewer Caption Engine v2.

- [ ] **Step 1: Add failing LLM safety tests**

Verify rewritten output is rejected/falls back if URL is removed, fabricated personal experience is introduced, output is too long, or unsupported numeric claims appear.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_reviewer_caption_v2 -v`
Expected: new LLM safety tests FAIL before implementation.

- [ ] **Step 3: Add reviewer rewrite prompt**

Prompt instructs: Threads reviewer voice, 3–5 short lines, hook <=12 words, one selling point, 0–2 emoji, no markdown/hashtag spam, no first-hand-use claim, preserve URL and all numbers exactly from allowed facts.

- [ ] **Step 4: Add output guard**

Use existing `content.validate()` plus reviewer-specific URL/length/fabricated-experience/numeric-fact checks; fallback to deterministic draft on failure.

- [ ] **Step 5: Run GREEN**

Run focused reviewer tests, then Shopee auto pipeline tests.

### Task 4: Regression gate

**Files:**
- Modify: `.github/workflows/shopee-auto-pipeline-ci.yml` only if needed to include reviewer files/tests.

- [ ] **Step 1: Run focused tests in mock mode**

```bash
export ACP_ENV=test ACP_ADAPTER=mock ACP_SOURCE=mock
python -m unittest tests.test_reviewer_caption_v2 tests.test_shopee_auto_pipeline tests.test_shopee_product_pool_v2 -v
```

- [ ] **Step 2: Run compile/diff gate**

```bash
python -m compileall core tests >/dev/null
git diff --check
```

- [ ] **Step 3: Review generated examples**

Generate sample captions for several products from the supplied Shopee CSV and manually inspect hook brevity, single-angle focus and absence of fabricated experience. Do not publish live Threads posts.
