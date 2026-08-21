#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return False
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one match in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def task1():
    replace_once(
        "run.py",
        "    python3 run.py auto-schedule        lấp lịch Threads Auto 48 giờ; không bật worker/global publish\n",
        "    python3 run.py auto-schedule        lấp lịch Threads Auto 48 giờ; không bật worker/global publish\n"
        "    python3 run.py shopee-enrich        enrich tối đa 20 ảnh Shopee đã import; không đăng bài\n",
    )
    replace_once(
        "run.py",
        "                                         thứ tự timer an toàn: sync catalog -> auto-schedule -> worker-once\n",
        "                                         thứ tự timer an toàn: sync catalog -> shopee-enrich -> auto-schedule -> worker-once\n",
    )
    replace_once(
        "run.py",
        "from acp.core import attribution, crypto, jobs, pipeline, scoring\n",
        "from acp.core import attribution, crypto, jobs, pipeline, scoring, shopee_image_enrichment\n",
    )
    replace_once(
        "run.py",
        "    return 0\n\n\ndef cmd_review():\n",
        "    return 0\n\n\ndef cmd_shopee_enrich():\n"
        "    \"\"\"Run one bounded Shopee image-enrichment pass; never publish posts.\"\"\"\n"
        "    try:\n"
        "        init_db()\n"
        "        summary = shopee_image_enrichment.run_batch(\n"
        "            connect,\n"
        "            limit=shopee_image_enrichment.MAX_BATCH_SIZE,\n"
        "        )\n"
        "    except Exception:\n"
        "        print(\"Shopee enrichment failed. Check local service logs.\")\n"
        "        return 1\n\n"
        "    print(\n"
        "        \"Shopee enrichment: \"\n"
        "        + \", \".join(\n"
        "            f\"{key}={int(summary.get(key, 0))}\"\n"
        "            for key in (\"processed\", \"ready\", \"needs_helper\", \"failed\", \"pending\")\n"
        "        )\n"
        "    )\n"
        "    return 0\n\n\ndef cmd_review():\n",
    )
    replace_once(
        "run.py",
        "    \"auto-schedule\": cmd_auto_schedule,\n",
        "    \"auto-schedule\": cmd_auto_schedule, \"shopee-enrich\": cmd_shopee_enrich,\n",
    )
    replace_once(
        "run.py",
        "    elif cmd in (\"worker-once\", \"worker-status\", \"auto-schedule\"):\n",
        "    elif cmd in (\"worker-once\", \"worker-status\", \"auto-schedule\", \"shopee-enrich\"):\n",
    )

    replace_once(
        "ops/acp-auto-schedule.service",
        "ExecStartPre=/bin/bash -lc 'set -a; . \"%h/Downloads/ACP/acp/.env.local\"; set +a; exec \"%h/Downloads/ACP/acp/.venv/bin/python\" \"%h/Downloads/ACP/acp/run.py\" product-sync'\n# Explicit command contract for docs/tests: run.py auto-schedule\n",
        "ExecStartPre=/bin/bash -lc 'set -a; . \"%h/Downloads/ACP/acp/.env.local\"; set +a; exec \"%h/Downloads/ACP/acp/.venv/bin/python\" \"%h/Downloads/ACP/acp/run.py\" product-sync'\n"
        "ExecStartPre=/bin/bash -lc 'set -a; . \"%h/Downloads/ACP/acp/.env.local\"; set +a; exec \"%h/Downloads/ACP/acp/.venv/bin/python\" \"%h/Downloads/ACP/acp/run.py\" shopee-enrich'\n"
        "# Explicit command contract for docs/tests: run.py auto-schedule\n",
    )
    replace_once(
        "ops/acp-auto-schedule.service",
        "TimeoutStartSec=55s\n",
        "TimeoutStartSec=120s\n",
    )


if __name__ == "__main__":
    task1()
