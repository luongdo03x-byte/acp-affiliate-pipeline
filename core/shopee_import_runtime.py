"""Compatibility installation for the Shopee CSV import result contract.

The existing importer owns the transaction and counters.  This narrow wrapper
adds the canonical Product IDs touched by valid unique rows after the import
transaction has completed, without changing CSV parsing or commercial field
ownership.
"""
from __future__ import annotations

from . import shopee_csv_import

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_import_rows = shopee_csv_import.import_rows

    def import_rows(conn, row_results):
        rows = list(row_results or [])
        summary = dict(original_import_rows(conn, rows))
        touched = []
        seen = set()
        for result in rows:
            if (
                result.row is None
                or result.error is not None
                or result.status in ("ERROR", "DUPLICATE_IN_UPLOAD")
            ):
                continue
            try:
                product = shopee_csv_import._find_matching_product(conn, result.row)
            except shopee_csv_import.ShopeeCsvError:
                continue
            if product is None:
                continue
            product_id = str(product["id"])
            if product_id not in seen:
                seen.add(product_id)
                touched.append(product_id)
        summary["touched_product_ids"] = touched
        return summary

    shopee_csv_import.import_rows = import_rows
    _INSTALLED = True
