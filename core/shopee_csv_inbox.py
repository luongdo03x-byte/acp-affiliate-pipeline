"""Unattended import of Shopee Affiliate CSV files dropped into a folder.

Shopee's portal owns two things ACP cannot derive: the `s.shopee.vn` short
affiliate link and the real price. Both only arrive in the bulk-link CSV the
operator exports, so downloading it stays a manual step. What this module
removes is everything after the download -- drop the file in `inbox/` and the
Auto timer imports it on the next pass.

Parsing and importing are delegated to `shopee_csv_import`, unchanged: this is
a file-handling shell around the same code path the web upload uses.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .db import audit
from .shopee_csv_import import (
    MAX_FILE_BYTES,
    MAX_FILES,
    ShopeeCsvError,
    dedupe_upload_rows,
    import_rows,
    parse_shopee_affiliate_csv,
    preview_rows_against_db,
)

# Thư mục gốc mặc định nằm cạnh CSDL để chung một backup/quota với var/.
DEFAULT_INBOX_ENV = "ACP_SHOPEE_CSV_INBOX"
SUBDIRS = ("inbox", "archive", "rejected")
CSV_SUFFIX = ".csv"


def default_base_dir() -> str:
    configured = str(os.environ.get(DEFAULT_INBOX_ENV) or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "var", "shopee-inbox")


def ensure_dirs(base_dir: str = None) -> dict:
    base = os.path.abspath(base_dir or default_base_dir())
    paths = {name: os.path.join(base, name) for name in SUBDIRS}
    paths["base"] = base
    for name in SUBDIRS:
        os.makedirs(paths[name], exist_ok=True)
    return paths


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _candidates(inbox_dir: str) -> list:
    """CSV thật sự nằm trực tiếp trong inbox/, cũ trước mới sau.

    `os.listdir` không trả về '..' nên tên có đường dẫn không thể xuất hiện;
    kiểm tra lại dirname là chốt chặn thứ hai, phòng khi có symlink trỏ ra ngoài.
    """
    entries = []
    for name in os.listdir(inbox_dir):
        if not name.lower().endswith(CSV_SUFFIX):
            continue
        path = os.path.join(inbox_dir, name)
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(inbox_dir):
            continue
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        entries.append((os.path.getmtime(path), name, path))
    entries.sort()
    return entries[:MAX_FILES]


def _move(src: str, dest_dir: str, filename: str) -> str:
    """Đổi tên có gắn dấu thời gian để hai lần thả cùng tên không đè nhau."""
    target = os.path.join(dest_dir, f"{_stamp()}-{filename}")
    suffix = 1
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{_stamp()}-{suffix}-{filename}")
        suffix += 1
    os.replace(src, target)
    return target


def _reject(path: str, filename: str, rejected_dir: str, message: str) -> None:
    moved = _move(path, rejected_dir, filename)
    with open(f"{moved}.error.txt", "w", encoding="utf-8") as handle:
        handle.write(
            f"File: {filename}\n"
            f"Thời điểm: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            f"Lý do: {message}\n"
        )


def run_once(conn, base_dir: str = None, *, actor: str = "csv_inbox_timer") -> dict:
    """Import mọi CSV đang chờ trong inbox/. Trả về thống kê cho CLI và test."""
    paths = ensure_dirs(base_dir)
    summary = {
        "files_seen": 0, "files_imported": 0, "files_rejected": 0,
        "total": 0, "new": 0, "updated": 0, "unchanged": 0, "duplicate": 0, "error": 0,
    }

    for _, filename, path in _candidates(paths["inbox"]):
        summary["files_seen"] += 1

        if os.path.getsize(path) > MAX_FILE_BYTES:
            summary["files_rejected"] += 1
            _reject(path, filename, paths["rejected"],
                    f"File vượt quá {MAX_FILE_BYTES // (1024 * 1024)}MB.")
            continue

        try:
            with open(path, "rb") as handle:
                raw = handle.read()
            parsed = parse_shopee_affiliate_csv(raw, filename)
        except ShopeeCsvError as exc:
            summary["files_rejected"] += 1
            _reject(path, filename, paths["rejected"], str(exc))
            continue
        except OSError:
            # Đọc hỏng: để nguyên trong inbox cho lượt sau, không nuốt file.
            summary["files_rejected"] += 1
            continue

        rows = preview_rows_against_db(conn, dedupe_upload_rows(parsed))
        result = import_rows(conn, rows)

        summary["files_imported"] += 1
        for key in ("total", "new", "updated", "unchanged", "duplicate", "error"):
            summary[key] += int(result.get(key, 0) or 0)
        _move(path, paths["archive"], filename)

    if summary["files_imported"] or summary["files_rejected"]:
        audit(conn, actor, "shopee_csv_inbox_import", "product", None, summary)
    return summary
