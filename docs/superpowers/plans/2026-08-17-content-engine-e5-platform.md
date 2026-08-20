# Platform Adaptation (Content Engine v2, E5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ghép `ContentVariant` (E3) thành caption hoàn chỉnh riêng cho Threads/Facebook/Instagram — thêm affiliate link + disclosure, giới hạn đúng ký tự từng platform.

**Architecture:** 1 module thuần function mới `core/content_platform.py`, không LLM (nội dung đã cố định từ E2/E3, chỉ khác cách ghép chuỗi). Import 2 hằng số read-only từ `core/content.py` (`PLATFORM_MAX_LEN`, `DISCLOSURE_DEFAULT`), không sửa file đó.

**Tech Stack:** Python 3, không thêm dependency mới.

**Spec:** `docs/superpowers/specs/2026-08-17-content-engine-e5-platform-design.md`

## Global Constraints

- Không đụng `core/pipeline.py`, không đụng `core/content.py` (chỉ import 2 hằng số `PLATFORM_MAX_LEN`/`DISCLOSURE_DEFAULT`, không gọi hàm, không sửa file).
- **TUYỆT ĐỐI không sửa `core/content_variant.py`** (E3, đã merge+review) — chỉ import `ContentVariant` field, không sửa.
- **Không import `content._fit()`** (hàm private) — viết `_fit_to_length()` riêng trong module mới.
- `affiliate_link` là tham số riêng cho mọi hàm adapt — không lẫn trong `body`/`cta` của `ContentVariant`.
- Không cài logic hashtag nào (Threads §24 cấm tự thêm hashtag hàng loạt — đơn giản nhất là không có logic hashtag nào cả).
- Test dùng bộ harness sẵn có của repo (`check(name, cond, detail)`, list `PASS`/`FAIL` toàn cục, đăng ký tường minh trong `if __name__ == "__main__":`) — thêm vào `tests/test_pipeline.py`, không tạo file test mới, không dùng pytest. Tái dùng helper `_mk_test_variant()` đã có sẵn (từ E3).
- Chạy test bằng: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline` (venv riêng của repo).
- Baseline trước E5: 475 PASS, 0 FAIL (`test_pipeline.py`), 340 PASS/0 FAIL (`test_pilot.py`).
- Commit message tiếng Việt CÓ DẤU ĐẦY ĐỦ.

---

### Task 1: `_fit_to_length()` + 3 platform adapter

**Files:**
- Create: `core/content_platform.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ContentVariant` (E3, chỉ đọc field), `content.PLATFORM_MAX_LEN`, `content.DISCLOSURE_DEFAULT` (đọc read-only, import trực tiếp).
- Produces: `_fit_to_length(body, affiliate_link, disclosure, max_len)`, `adapt_for_threads(variant, affiliate_link, disclosure=None)`, `adapt_for_facebook(...)`, `adapt_for_instagram(...)`. Task 2 mở rộng thêm dispatcher + batch.

- [ ] **Step 1: Viết 7 test (sẽ fail vì module chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_select_best_variant_repetition_penalty_affects_choice()` (test cuối cùng hiện có của E4):

```python
def test_adapt_for_threads_includes_link_and_disclosure():
    print("\nadapt_for_threads() có affiliate_link + disclosure, giới hạn <=500 ký tự")
    from acp.core import content_platform, content
    v = _mk_test_variant()
    link = "https://go.isclix.com/x?sub1=abc"
    result = content_platform.adapt_for_threads(v, link)
    check("có affiliate_link", link in result, result)
    check("có disclosure", content.DISCLOSURE_DEFAULT in result, result)
    check("hook ở đầu chuỗi", result.startswith(v.hook), result)
    check("độ dài <= 500", len(result) <= content.PLATFORM_MAX_LEN["threads"], len(result))


def test_adapt_for_threads_truncates_long_body_but_keeps_link_and_disclosure():
    print("\nadapt_for_threads() cắt body dài nhưng vẫn giữ đủ link + disclosure")
    from acp.core import content_platform, content
    v = _mk_test_variant(main_message="m" * 600)
    link = "https://go.isclix.com/x?sub1=abc"
    result = content_platform.adapt_for_threads(v, link)
    check("độ dài <= 500 dù body gốc rất dài", len(result) <= content.PLATFORM_MAX_LEN["threads"], len(result))
    check("vẫn có link sau khi cắt", link in result, result)
    check("vẫn có disclosure sau khi cắt", content.DISCLOSURE_DEFAULT in result, result)


def test_adapt_for_facebook_merges_main_message_and_body_into_paragraph():
    print("\nadapt_for_facebook() gộp main_message + body thành 1 đoạn văn liền, không xuống dòng giữa chúng")
    from acp.core import content_platform, content
    v = _mk_test_variant(main_message="Ý chính", body=["Điểm phụ 1", "Điểm phụ 2"])
    link = "https://go.isclix.com/x?sub1=abc"
    result = content_platform.adapt_for_facebook(v, link)
    check("main_message và body[0] cùng 1 dòng (gộp đoạn văn)",
          "Ý chính Điểm phụ 1 Điểm phụ 2" in result, result)
    check("có affiliate_link", link in result, result)
    check("có disclosure", content.DISCLOSURE_DEFAULT in result, result)
    check("độ dài <= 63206", len(result) <= content.PLATFORM_MAX_LEN["facebook"], len(result))


def test_adapt_for_instagram_includes_link_and_disclosure():
    print("\nadapt_for_instagram() có affiliate_link + disclosure, giới hạn <=2200 ký tự")
    from acp.core import content_platform, content
    v = _mk_test_variant()
    link = "https://go.isclix.com/x?sub1=abc"
    result = content_platform.adapt_for_instagram(v, link)
    check("có affiliate_link", link in result, result)
    check("có disclosure", content.DISCLOSURE_DEFAULT in result, result)
    check("hook ở đầu chuỗi", result.startswith(v.hook), result)
    check("độ dài <= 2200", len(result) <= content.PLATFORM_MAX_LEN["instagram"], len(result))


def test_platform_adapters_never_add_hashtag():
    print("\nCả 3 adapter không tự thêm hashtag nào ngoài disclosure (PTYC mục 24)")
    from acp.core import content_platform, content
    v = _mk_test_variant()
    link = "https://go.isclix.com/x?sub1=abc"
    for adapter in (content_platform.adapt_for_threads, content_platform.adapt_for_facebook,
                    content_platform.adapt_for_instagram):
        result = adapter(v, link)
        without_disclosure = result.replace(content.DISCLOSURE_DEFAULT, "")
        check(f"{adapter.__name__} không có # ngoài disclosure", "#" not in without_disclosure, result)


def test_fit_to_length_no_truncation_when_body_fits():
    print("\n_fit_to_length() không cắt khi body đã vừa budget")
    from acp.core import content_platform
    result = content_platform._fit_to_length("body ngắn", "https://link.test", "disclosure test", 500)
    check("giữ nguyên body", result.startswith("body ngắn"), result)
    check("có link + disclosure ở cuối", result.endswith("disclosure test") and "https://link.test" in result, result)


def test_fit_to_length_truncates_when_body_too_long():
    print("\n_fit_to_length() cắt đúng khi body vượt budget, vẫn giữ link + disclosure")
    from acp.core import content_platform
    long_body = "từ " * 200
    result = content_platform._fit_to_length(long_body, "https://link.test", "disclosure test", 100)
    check("độ dài đúng giới hạn", len(result) <= 100, len(result))
    check("vẫn có link", "https://link.test" in result, result)
    check("vẫn có disclosure", "disclosure test" in result, result)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `ModuleNotFoundError: No module named 'acp.core.content_platform'`

- [ ] **Step 3: Viết `core/content_platform.py`**

```python
"""Platform Adaptation -- ghép ContentVariant (E3) thành caption hoàn
chỉnh riêng cho Threads/Facebook/Instagram (Content Engine v2, PTYC mục
23-27).

Không đụng core/pipeline.py/core/content.py (chỉ đọc 2 hằng số read-only)
-- dormant như E1-E4, chưa nối vào luồng tạo bài thật (việc của E6).
"""
from . import content


def _fit_to_length(body: str, affiliate_link: str, disclosure: str, max_len: int) -> str:
    """Nối affiliate_link + disclosure vào cuối, cắt body nếu vượt max_len."""
    tail = f"\n\n{affiliate_link}\n\n{disclosure}"
    budget = max_len - len(tail)
    body = body.strip()
    if len(body) <= budget:
        return body + tail
    head = body[:max(0, budget)].rsplit(" ", 1)[0].rstrip(" ,.—-") + "…"
    return head + tail


def adapt_for_threads(variant, affiliate_link: str, disclosure: str = None) -> str:
    """PTYC mục 24: hook cực nhanh, conversational, dòng ngắn, không
    paragraph dài, CTA nhẹ, không hashtag.
    """
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    lines = [variant.hook, "", variant.main_message, *variant.body, variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["threads"])


def adapt_for_facebook(variant, affiliate_link: str, disclosure: str = None) -> str:
    """PTYC mục 25: dòng đầu mạnh (hook), có thể giải thích hơn Threads --
    gộp main_message + body thành 1 đoạn văn liền mạch.
    """
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    paragraph = " ".join([variant.main_message, *variant.body])
    lines = [variant.hook, "", paragraph, "", variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["facebook"])


def adapt_for_instagram(variant, affiliate_link: str, disclosure: str = None) -> str:
    """PTYC mục 26: hook đầu, ngắn rõ, CTA rõ -- cùng kiểu ghép xuống dòng
    như Threads, khác biệt chính là giới hạn ký tự (2200 vs 500).
    """
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    lines = [variant.hook, "", variant.main_message, *variant.body, variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["instagram"])
```

- [ ] **Step 4: Đăng ký 7 test, chạy lại**

Thêm 7 hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`, ngay sau `test_select_best_variant_repetition_penalty_affects_choice()`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `test_adapt_for_threads_includes_link_and_disclosure` (4 check), `test_adapt_for_threads_truncates_long_body_but_keeps_link_and_disclosure` (3 check), `test_adapt_for_facebook_merges_main_message_and_body_into_paragraph` (4 check), `test_adapt_for_instagram_includes_link_and_disclosure` (4 check), `test_platform_adapters_never_add_hashtag` (3 check), `test_fit_to_length_no_truncation_when_body_fits` (2 check), `test_fit_to_length_truncates_when_body_too_long` (3 check) — tổng đúng 23 check mới. Tổng: 475 + 23 = 498 PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add core/content_platform.py tests/test_pipeline.py
git commit -m "feat: adapt_for_threads/facebook/instagram() -- Platform Adaptation (Content Engine v2, E5)"
```

---

### Task 2: Dispatcher + batch (`adapt_for_platform()` / `adapt_for_platforms()`)

**Files:**
- Modify: `core/content_platform.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `adapt_for_threads()`/`adapt_for_facebook()`/`adapt_for_instagram()` (Task 1).
- Produces: `adapt_for_platform(variant, platform, affiliate_link, disclosure=None) -> str`, `adapt_for_platforms(variant, platforms, affiliate_link, disclosure=None) -> dict`. Đây là API cuối cùng E6 sẽ gọi.

- [ ] **Step 1: Viết 4 test (sẽ fail vì hàm chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, sau `test_fit_to_length_truncates_when_body_too_long()`:

```python
def test_adapt_for_platform_dispatches_correctly():
    print("\nadapt_for_platform() dispatch đúng theo tên platform")
    from acp.core import content_platform
    v = _mk_test_variant()
    link = "https://go.isclix.com/x?sub1=abc"
    for platform in ("threads", "facebook", "instagram"):
        direct = getattr(content_platform, f"adapt_for_{platform}")(v, link)
        via_dispatch = content_platform.adapt_for_platform(v, platform, link)
        check(f"dispatch {platform} khớp gọi trực tiếp", via_dispatch == direct, (platform, via_dispatch, direct))


def test_adapt_for_platform_invalid_platform_raises_keyerror():
    print("\nadapt_for_platform() raise KeyError với platform không hợp lệ")
    from acp.core import content_platform
    v = _mk_test_variant()
    link = "https://go.isclix.com/x?sub1=abc"
    try:
        content_platform.adapt_for_platform(v, "tiktok", link)
        check("phải raise KeyError", False)
    except KeyError:
        check("raise đúng KeyError", True)


def test_adapt_for_platforms_returns_only_requested_platforms():
    print("\nadapt_for_platforms() chỉ trả đúng platform trong danh sách yêu cầu, không tự thêm")
    from acp.core import content_platform
    v = _mk_test_variant()
    link = "https://go.isclix.com/x?sub1=abc"
    result_one = content_platform.adapt_for_platforms(v, ["threads"], link)
    check("chỉ có đúng 1 platform", set(result_one.keys()) == {"threads"}, result_one.keys())


def test_adapt_for_platforms_all_three_matches_individual_calls():
    print("\nadapt_for_platforms() với đủ 3 platform khớp từng lời gọi riêng lẻ")
    from acp.core import content_platform
    v = _mk_test_variant()
    link = "https://go.isclix.com/x?sub1=abc"
    result = content_platform.adapt_for_platforms(v, ["threads", "facebook", "instagram"], link)
    check("khớp cả 3 platform với gọi riêng lẻ",
          result == {
              "threads": content_platform.adapt_for_threads(v, link),
              "facebook": content_platform.adapt_for_facebook(v, link),
              "instagram": content_platform.adapt_for_instagram(v, link),
          }, result)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `AttributeError: module 'acp.core.content_platform' has no attribute 'adapt_for_platform'`

- [ ] **Step 3: Thêm dispatcher + batch vào `core/content_platform.py`**

Thêm cuối file:

```python
_ADAPTERS = {
    "threads": adapt_for_threads,
    "facebook": adapt_for_facebook,
    "instagram": adapt_for_instagram,
}


def adapt_for_platform(variant, platform: str, affiliate_link: str, disclosure: str = None) -> str:
    return _ADAPTERS[platform](variant, affiliate_link, disclosure)


def adapt_for_platforms(variant, platforms: list, affiliate_link: str, disclosure: str = None) -> dict:
    """PTYC mục 27 "dùng nội dung này cho tất cả kênh": tính riêng từng
    platform trong danh sách được truyền vào -- không tự ý sinh cả 3 nếu
    caller chỉ cần 1-2 platform.
    """
    return {p: adapt_for_platform(variant, p, affiliate_link, disclosure) for p in platforms}
```

- [ ] **Step 4: Đăng ký 4 test, chạy lại toàn bộ**

Thêm 4 hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`, sau các test của Task 1.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ PASS, 0 FAIL, không hàm nào từ Task 1/E1-E4 bị hỏng.

- [ ] **Step 5: Chạy toàn bộ regression suite**

Run:
```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pipeline
acp/.venv/bin/python3 -m acp.tests.test_pilot
```

Expected: cả 2 file 0 FAIL — `test_pilot.py` phải giữ nguyên baseline 340 PASS.

- [ ] **Step 6: Commit**

```bash
git add core/content_platform.py tests/test_pipeline.py
git commit -m "feat: adapt_for_platform() + adapt_for_platforms() -- dispatcher và batch (Content Engine v2, E5)"
```
