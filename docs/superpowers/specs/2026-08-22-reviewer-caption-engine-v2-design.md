# Reviewer Caption Engine v2 Design

## Goal

Generate short, hook-led, conversational Shopee Affiliate captions that read like a real Threads reviewer account instead of a marketplace listing, while staying grounded in real product facts and preserving the existing ACP scheduling/publishing pipeline.

## Style basis

The supplied Threads ebook emphasizes a strong first line, short scannable lines, conversational tone, authenticity, value before selling, and one clear CTA. Reviewer v2 applies those principles without claiming first-hand use that ACP cannot prove.

## Output contract

For `provider='SHOPEE_AFFILIATE'`:

- first line is a hook, target <= 12 words;
- total caption is short and easy to scan;
- one dominant angle per post;
- one soft CTA;
- exact affiliate URL preserved;
- no long product-title repetition;
- no fabricated first-hand experience, unsupported numbers, urgency, or efficacy claims;
- no manual `#tiepthilienket — mình có nhận hoa hồng nếu bạn mua qua link này` line in `caption_final`;
- platform/native affiliate labeling, when available, remains owned by the publisher/platform integration and is not changed by this engine.

For non-Shopee providers, legacy content generation remains unchanged.

## Product signal priority

1. Audience/pain point from explicit size range or target wording.
2. Strong social proof from real sold count.
3. Concrete feature or use-case present in the product title/data.
4. Price as fallback.

The generator must not invent a stronger angle than the available facts support.

## Hook and attribution

ACP already persists H1..H9 `variant_code` values for attribution. Reviewer v2 keeps those codes meaningful by mapping each code to a distinct safe hook shape while using the same verified product signal. It must not silently generate the same hook for multiple measured variants.

## LLM role

The LLM is optional and rewrite-only. A deterministic draft is always available. An LLM rewrite is accepted only when it:

- preserves the exact affiliate URL;
- remains within short Threads structure/length limits;
- does not add unsupported numeric claims;
- does not fabricate buying/using/trying experience;
- avoids catalogue/brand language and blocked salesy phrases.

Otherwise ACP falls back to the deterministic draft.

## Integration

Install a provider-scoped wrapper around the existing `content.generate()` surface. The scheduler, product routing, publish queue, quotas, preflight, OAuth, and live publisher are not changed.

Shopee Reviewer captions return reviewer copy plus the affiliate URL only. The existing manual disclosure string may remain stored elsewhere as metadata for compatibility, but it must not be appended to Shopee Reviewer `caption_final`.

## Verification

Use focused tests under mock/test configuration only. No live Threads post is required to verify caption behavior. Regression coverage must include short social-proof copy, bigsize pain-point copy, real CSV title feature extraction, H1..H9 hook uniqueness, LLM safe rewrite/fallback, manual disclosure omission, and legacy-provider compatibility.
