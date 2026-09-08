"""Kho ứng viên cho Auto không được cắt theo điểm số toàn cục.

``_shopee_auto_candidates`` lấy N sản phẩm điểm cao nhất TOÀN KHO rồi mới lọc
theo chủ đề của kênh. Trên hệ thống thật, top 100 bị hàng my-pham/thoi-trang-nu
chiếm hết, nên năm kênh chạy ngách khác (gia-dung, cong-nghe, the-thao,
thu-cung, me-va-be) nhận 0 ứng viên -- dù mỗi kênh có 31-74 sản phẩm hợp lệ,
chỉ vì món đầu tiên của chúng nằm ở hạng 106-108.

Hệ quả: các kênh đó không bao giờ được lấp lịch, và ``auto-schedule`` báo
``scheduled=0, skipped=16``.

KHÔNG có test cho ràng buộc "phải fetchall() thay vì duyệt cursor lười" (xem
chú thích trong ``_shopee_auto_candidates``). Đã thử: ``sqlite3`` nạp kết quả
theo lô nên với vài trăm dòng, cursor đã đọc xong và đóng giao dịch đọc trước
khi vòng lặp chạy -- test xanh cả khi code sai. Muốn tái hiện phải dựng hàng
nghìn dòng và ép đúng thời điểm ghi từ connection khác, quá mong manh để giữ
lại. Ràng buộc đó hiện chỉ được bảo vệ bằng chú thích trong code.
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db, shopee_auto_runtime

# Ngưỡng cũ: LIMIT = max(limit,1) * 5. Với limit=20 (giá trị fill_auto_schedule
# dùng) là đúng 100 sản phẩm đầu bảng.
LIMIT = 20
OLD_CUTOFF = LIMIT * 5


class ShopeeCandidateScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "candidates.db")
        self.addCleanup(self._restore_db_path)
        db.init_db()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        self.now = datetime.now(timezone.utc)

        self.conn.execute(
            """INSERT INTO channel (id, code, platform, handle, status, enabled,
                                    auto_schedule_enabled, daily_post_target,
                                    daily_post_cap, posting_timezone, niches, created_at)
               VALUES ('ch-giadung','threads_giadung','threads','@giadung','ACTIVE',1,1,
                       3,12,'Asia/Bangkok','["gia-dung"]',datetime('now'))"""
        )
        self.channel = self.conn.execute(
            "SELECT * FROM channel WHERE id='ch-giadung'").fetchone()

    def _restore_db_path(self):
        db.DB_PATH = self.old_db_path

    def _product(self, index: int, name: str, score: float) -> str:
        """Sản phẩm hợp lệ hoàn toàn, chỉ khác nhau ở tên và điểm số."""
        product_id = f"p{index:04d}"
        self.conn.execute(
            """INSERT INTO product (id, source, merchant, external_product_id, name,
                                    current_price, commission_value, category_code,
                                    product_url, is_available, created_at, updated_at,
                                    affiliate_link_status, post_count, provider,
                                    main_image_url, affiliate_url, score, last_synced_at)
               VALUES (?,'shopee','shopee.vn',?,?,200000,50000,'khac',?,1,
                       datetime('now'),datetime('now'),'READY',0,'SHOPEE_AFFILIATE',
                       ?,?,?,datetime('now'))""",
            (product_id, product_id, name, f"https://shopee.vn/{product_id}",
             f"https://cdn.example/{product_id}.jpg",
             f"https://s.shopee.vn/{product_id}", score),
        )
        self.conn.execute(
            """INSERT INTO shopee_image_enrichment_job (product_id, status, created_at, updated_at)
               VALUES (?, 'READY', datetime('now'), datetime('now'))""",
            (product_id,),
        )
        return product_id

    def _seed_pool(self):
        """Dựng lại đúng hình dạng kho thật: hàng ngách khác chiếm hết đầu bảng."""
        # Điểm cao, thuộc thoi-trang-nu -- KHÔNG thuộc gia-dung.
        for index in range(OLD_CUTOFF + 5):
            self._product(index, f"Váy xinh nữ dáng chữ A số {index}", score=90.0)
        # Điểm thấp hơn nên xếp sau ngưỡng cũ, nhưng đúng ngách của kênh.
        wanted = []
        for offset, name in enumerate((
            "Khăn giấy rút Topgia 4 lớp mềm mịn",
            "Nước giặt TopGia 3in1 hương huyền diệu",
            "Túi đựng rác tự phân hủy loại dày",
        )):
            wanted.append(self._product(OLD_CUTOFF + 100 + offset, name, score=10.0))
        return wanted

    def test_san_pham_dung_nganh_nhung_diem_thap_van_phai_thanh_ung_vien(self):
        wanted = self._seed_pool()

        candidates = shopee_auto_runtime._shopee_auto_candidates(
            self.conn, self.channel, LIMIT, self.now)
        found = {str(item["product"]["id"]) for item in candidates}

        missing = [pid for pid in wanted if pid not in found]
        self.assertEqual(
            missing, [],
            "sản phẩm đúng ngách bị bỏ qua chỉ vì xếp sau top "
            f"{OLD_CUTOFF} của bảng điểm toàn kho")

    def test_khong_tra_ve_qua_so_luong_yeu_cau(self):
        self._seed_pool()
        candidates = shopee_auto_runtime._shopee_auto_candidates(
            self.conn, self.channel, LIMIT, self.now)
        self.assertLessEqual(
            len(candidates), LIMIT,
            "quét hết bảng không có nghĩa là trả về hết -- vẫn phải tôn trọng limit")

    def test_kenh_tat_auto_van_khong_nhan_ung_vien_nao(self):
        self._seed_pool()
        self.conn.execute("UPDATE channel SET auto_schedule_enabled=0 WHERE id='ch-giadung'")
        channel = self.conn.execute("SELECT * FROM channel WHERE id='ch-giadung'").fetchone()
        self.assertEqual(
            shopee_auto_runtime._shopee_auto_candidates(self.conn, channel, LIMIT, self.now),
            [],
            "kênh đã tắt auto thì không được nhận ứng viên")

    def test_limit_bang_khong_thi_khong_quet_gi(self):
        self._seed_pool()
        self.assertEqual(
            shopee_auto_runtime._shopee_auto_candidates(self.conn, self.channel, 0, self.now),
            [])


if __name__ == "__main__":
    unittest.main()
