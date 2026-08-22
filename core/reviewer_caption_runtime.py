"""Install Reviewer Caption Engine v2 on the active content.generate surface.

The compatibility wrapper is provider-scoped: only SHOPEE_AFFILIATE changes.
All other providers continue to use the legacy generator unchanged.
"""
from __future__ import annotations

from . import content, reviewer_caption

SHOPEE_PROVIDER = "SHOPEE_AFFILIATE"
_INSTALLED = False


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (AttributeError, KeyError, IndexError, TypeError):
        return default


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_generate = content.generate

    def generate(product, template_code: str, affiliate_link: str,
                 discount_pct: float = 0.0, disclosure: str = "",
                 hook_code: str = None, rng=None) -> str:
        if str(_row_get(product, "provider", "") or "") != SHOPEE_PROVIDER:
            return original_generate(
                product,
                template_code,
                affiliate_link,
                discount_pct=discount_pct,
                disclosure=disclosure,
                hook_code=hook_code,
                rng=rng,
            )

        normalized = dict(product)
        normalized["name"] = content._strip_shop_suffix(
            normalized.get("name"),
            normalized.get("shop_name") or normalized.get("shop"),
        )
        draft = reviewer_caption.generate(
            normalized,
            affiliate_link,
            discount_pct=discount_pct,
            hook_code=hook_code,
            llm_fn=content._llm_fn,
        )
        effective_disclosure = disclosure or content.DISCLOSURE_DEFAULT
        return content._fit(draft, effective_disclosure)

    content.generate = generate
    _INSTALLED = True
