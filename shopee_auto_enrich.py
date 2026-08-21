#!/usr/bin/env python3
"""Run one bounded Shopee Affiliate image-enrichment pass for the Auto timer."""
import sys

from acp.core import db
from acp.core import shopee_image_enrichment as enrichment


def main() -> int:
    try:
        db.init_db()
        summary = enrichment.run_batch(
            db.connect,
            limit=enrichment.MAX_BATCH_SIZE,
        )
    except Exception:
        # Never echo provider response bodies or request details into service logs.
        print("Shopee enrichment failed. Check local service logs.")
        return 1

    print(
        "Shopee enrichment: "
        + ", ".join(
            f"{key}={int(summary.get(key, 0))}"
            for key in ("processed", "ready", "needs_helper", "failed", "pending")
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
