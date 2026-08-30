#!/usr/bin/env python3
"""Run one bounded Shopee Affiliate image-enrichment pass for the Auto timer."""
import os
import sys

# systemd gọi script bằng đường dẫn tuyệt đối, không có PYTHONPATH, nên gói
# `acp` không nằm trên sys.path và import bên dưới sẽ chết. run.py tự bootstrap
# đúng như vậy; thiếu dòng này thì ExecStartPre luôn hỏng và cả service
# auto-schedule không bao giờ chạy tới bước lấp lịch.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
