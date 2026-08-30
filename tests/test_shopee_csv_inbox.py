"""Unattended import of operator-dropped Shopee Affiliate CSV files.

The affiliate short links and the real price only exist in the CSV Shopee's
portal exports, so the download stays manual. What this removes is the six-step
web upload: drop the file in a folder, the timer imports it.
"""
import os
import tempfile
import unittest

from acp.core import db
from acp.core import shopee_csv_inbox as inbox


HEADER = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,"
    "Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"
)


def csv_text(*, item_id="123", shop_id="1", name="Ao thun nu", price="100"):
    """Đúng định dạng Shopee xuất ra: giá số trần, tỉ lệ dùng dấu phẩy thập phân."""
    rate = '"42,5%"'
    return HEADER + (
        f"{item_id},{name},{price},1000,Shop A,{rate},\u20ab42.500,"
        f"https://shopee.vn/product/{shop_id}/{item_id},https://s.shopee.vn/abc\n"
    )


class ShopeeCsvInboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "inbox.db")
        db.init_db()
        self.conn = db.connect()
        self.base = os.path.join(self.tmp.name, "shopee-inbox")

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _drop(self, filename: str, content: str) -> str:
        paths = inbox.ensure_dirs(self.base)
        target = os.path.join(paths["inbox"], filename)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)
        return target

    def _listing(self, key: str):
        return sorted(os.listdir(inbox.ensure_dirs(self.base)[key]))

    def _product(self, item_id="123"):
        return self.conn.execute(
            "SELECT * FROM product WHERE source='manual_shopee' AND external_product_id=?",
            (item_id,),
        ).fetchone()

    def test_ensure_dirs_creates_the_three_working_folders(self):
        paths = inbox.ensure_dirs(self.base)
        for key in ("inbox", "archive", "rejected"):
            self.assertTrue(os.path.isdir(paths[key]), key)

    def test_dropped_csv_is_imported_into_the_product_pool(self):
        self._drop("batch.csv", csv_text())

        summary = inbox.run_once(self.conn, self.base)

        self.assertEqual(summary["files_imported"], 1)
        self.assertEqual(summary["new"], 1)
        row = self._product()
        self.assertIsNotNone(row)
        self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/abc")
        self.assertEqual(row["current_price"], 100)

    def test_imported_file_moves_to_archive_and_leaves_inbox_empty(self):
        self._drop("batch.csv", csv_text())

        inbox.run_once(self.conn, self.base)

        self.assertEqual(self._listing("inbox"), [])
        self.assertEqual(len(self._listing("archive")), 1)
        self.assertIn("batch.csv", self._listing("archive")[0])

    def test_reimport_of_an_unchanged_row_still_refreshes_auto_freshness(self):
        """The whole point of the folder: a stable product must stay Auto-eligible."""
        self._drop("first.csv", csv_text())
        inbox.run_once(self.conn, self.base)
        product_id = self._product()["id"]
        self.conn.execute(
            "UPDATE product SET last_synced_at=? WHERE id=?",
            ("2020-01-01T00:00:00+00:00", product_id),
        )

        self._drop("second.csv", csv_text())
        summary = inbox.run_once(self.conn, self.base)

        self.assertEqual(summary["unchanged"], 1)
        self.assertGreater(self._product()["last_synced_at"], "2020-01-01T00:00:00+00:00")

    def test_malformed_csv_is_rejected_with_a_readable_sidecar_and_not_imported(self):
        self._drop("broken.csv", "khong,phai,csv,shopee\n1,2,3,4\n")

        summary = inbox.run_once(self.conn, self.base)

        self.assertEqual(summary["files_rejected"], 1)
        self.assertEqual(summary["files_imported"], 0)
        rejected = self._listing("rejected")
        self.assertTrue(any(name.endswith("broken.csv") for name in rejected), rejected)
        self.assertTrue(any(name.endswith(".error.txt") for name in rejected))

    def test_rejected_sidecar_explains_the_missing_columns_without_leaking_rows(self):
        self._drop("broken.csv", "khong,phai,csv,shopee\n1,2,3,4\n")
        inbox.run_once(self.conn, self.base)

        paths = inbox.ensure_dirs(self.base)
        sidecar = next(n for n in os.listdir(paths["rejected"]) if n.endswith(".error.txt"))
        text = open(os.path.join(paths["rejected"], sidecar), encoding="utf-8").read()
        self.assertIn("Mã sản phẩm", text)

    def test_oversized_file_is_rejected_without_being_parsed(self):
        self._drop("huge.csv", HEADER + ("x" * (inbox.MAX_FILE_BYTES + 1)))

        summary = inbox.run_once(self.conn, self.base)

        self.assertEqual(summary["files_rejected"], 1)
        self.assertEqual(self._listing("inbox"), [])

    def test_non_csv_files_are_ignored_and_left_in_place(self):
        self._drop("notes.txt", "chỉ là ghi chú")

        summary = inbox.run_once(self.conn, self.base)

        self.assertEqual(summary["files_seen"], 0)
        self.assertEqual(self._listing("inbox"), ["notes.txt"])

    def test_empty_inbox_is_a_cheap_no_op(self):
        summary = inbox.run_once(self.conn, self.base)
        self.assertEqual(summary["files_seen"], 0)
        self.assertEqual(summary["files_imported"], 0)

    def test_batch_is_capped_so_one_pass_cannot_run_unbounded(self):
        for index in range(inbox.MAX_FILES + 3):
            self._drop(f"b{index:02d}.csv", csv_text(item_id=str(1000 + index)))

        summary = inbox.run_once(self.conn, self.base)

        self.assertEqual(summary["files_imported"], inbox.MAX_FILES)
        self.assertEqual(len(self._listing("inbox")), 3)

    def test_archive_names_never_collide_for_same_named_drops(self):
        self._drop("batch.csv", csv_text(item_id="111"))
        inbox.run_once(self.conn, self.base)
        self._drop("batch.csv", csv_text(item_id="222"))
        inbox.run_once(self.conn, self.base)

        self.assertEqual(len(self._listing("archive")), 2)

    def test_path_traversal_in_a_filename_cannot_escape_the_archive(self):
        paths = inbox.ensure_dirs(self.base)
        nested = os.path.join(paths["inbox"], "..", "escape.csv")
        with open(nested, "w", encoding="utf-8") as handle:
            handle.write(csv_text())

        summary = inbox.run_once(self.conn, self.base)

        self.assertEqual(summary["files_seen"], 0)
        self.assertTrue(os.path.isfile(nested))


if __name__ == "__main__":
    unittest.main()
