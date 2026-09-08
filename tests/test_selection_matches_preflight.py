"""Khâu chọn ứng viên và khâu preflight phải cùng một định nghĩa "hợp kênh".

Hai khâu này dùng hai bộ luật khác nhau:

- chọn ứng viên đi qua ``topic_engine.channel_accepts_product``, tức bảng
  ``product_topic``;
- preflight ngay trước giờ đăng dùng ``niche.match_reasons`` trên cột
  ``channel.niches``.

Khi không niche nào khớp, ``sync_product_system_topics`` vẫn gắn cho sản phẩm
một chủ đề đoán gần nhất với nhãn ``SYSTEM_FALLBACK``. Khâu chọn tin vào nhãn
đó nên nhận, còn preflight so khớp từ khoá thấy không hợp nên huỷ.

Quan sát trên máy thật: 198 dòng ``SYSTEM_FALLBACK``, và 33/55 bài đã lên lịch
sẽ bị huỷ đúng vì lý do này. Ví dụ "Ghế mây thư giãn bập bênh ngoài trời" bị
gán vào ``cong-nghe`` rồi đưa lên kênh công nghệ.

Bất biến cần giữ: đã chọn thì phải đăng được. Chọn một sản phẩm rồi để nó chết
ở preflight là lãng phí slot -- slot đó lẽ ra dành cho hàng thật sự hợp kênh.
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import auto_scheduler, db, shopee_auto_runtime, topic_engine


class SelectionMatchesPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "consistency.db")
        self.addCleanup(self._restore_db_path)
        db.init_db()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        self.now = datetime.now(timezone.utc)

        self.conn.execute(
            """INSERT INTO channel (id, code, platform, handle, status, enabled,
                                    auto_schedule_enabled, daily_post_target,
                                    daily_post_cap, posting_timezone, niches, created_at)
               VALUES ('ch-cn','threads_congnghe','threads','@cn','ACTIVE',1,1,
                       3,12,'Asia/Bangkok','["cong-nghe"]',datetime('now'))"""
        )
        self.channel = self.conn.execute(
            "SELECT * FROM channel WHERE id='ch-cn'").fetchone()
        topic_engine.ensure_system_topics(self.conn)

    def _restore_db_path(self):
        db.DB_PATH = self.old_db_path

    def _add_product(self, product_id: str, name: str, category_code: str = "khac"):
        self.conn.execute(
            """INSERT INTO product (id, source, merchant, external_product_id, name,
                                    current_price, commission_value, category_code,
                                    product_url, is_available, created_at, updated_at,
                                    affiliate_link_status, post_count, provider,
                                    main_image_url, affiliate_url, score, last_synced_at)
               VALUES (?,'shopee','shopee.vn',?,?,200000,50000,?,?,1,
                       datetime('now'),datetime('now'),'READY',0,'SHOPEE_AFFILIATE',
                       ?,?,50.0,datetime('now'))""",
            (product_id, product_id, name, category_code,
             f"https://shopee.vn/{product_id}",
             f"https://cdn.example/{product_id}.jpg",
             f"https://s.shopee.vn/{product_id}"),
        )
        self.conn.execute(
            """INSERT INTO shopee_image_enrichment_job (product_id, status, created_at, updated_at)
               VALUES (?, 'READY', datetime('now'), datetime('now'))""",
            (product_id,),
        )
        return self.conn.execute(
            "SELECT * FROM product WHERE id=?", (product_id,)).fetchone()

    def _channel(self, channel_id: str, niches_json: str):
        self.conn.execute(
            """INSERT INTO channel (id, code, platform, handle, status, enabled,
                                    auto_schedule_enabled, daily_post_target,
                                    daily_post_cap, posting_timezone, niches, created_at)
               VALUES (?,?,'threads',?,'ACTIVE',1,1,3,12,'Asia/Bangkok',?,datetime('now'))""",
            (channel_id, f"threads_{channel_id}", f"@{channel_id}", niches_json),
        )
        return self.conn.execute(
            "SELECT * FROM channel WHERE id=?", (channel_id,)).fetchone()

    def test_hang_chi_duoc_gan_bang_phong_doan_thi_khong_duoc_chon(self):
        """Tên thật từ máy chạy thật.

        Fallback thấy chữ "thể thao" trong tên nên gán vào chủ đề the-thao, còn
        bộ so khớp chuẩn của the-thao đòi từ khoá cụ thể ("giày chạy bộ", "thảm
        yoga"...) mà "sneaker" không nằm trong đó. Trước đây khâu chọn tin vào
        nhãn phỏng đoán và nhận, rồi preflight huỷ ngay trước giờ đăng.
        """
        channel = self._channel("ch-tt", '["the-thao"]')
        product = self._add_product(
            "p-giay",
            "Giày Sneaker Da Cao Cấp CS375, Đế Cao Su Nguyên Khối, "
            "Sneaker Thể Thao Cao Cấp Êm Chân, Bền Bỉ, Chống Trơn Trượt")

        attached = topic_engine.sync_product_system_topics(self.conn, product)
        source = self.conn.execute(
            "SELECT source FROM product_topic WHERE product_id='p-giay'").fetchone()
        self.assertIsNotNone(source, "cần có gán chủ đề thì test mới có nghĩa")
        self.assertEqual(
            source["source"], "SYSTEM_FALLBACK",
            f"cần một gán phỏng đoán để tái hiện; thực tế gán {attached}")

        eligible, reason = shopee_auto_runtime._shopee_product_auto_eligibility(
            self.conn, product, channel, self.now, require_auto_schedule=True)
        self.assertFalse(
            eligible,
            "sản phẩm chỉ hợp kênh nhờ nhãn phỏng đoán thì không được chọn làm ứng viên")
        self.assertEqual(reason, "product_no_longer_matches_channel")

    def test_fallback_khong_duoc_doan_theo_chuoi_con(self):
        """"đọc sách" từng khớp vào từ khoá "sac" của cong-nghe."""
        product = self._add_product(
            "p-ghe",
            "Ghế mây thư giãn bập bênh ngoài trời kèm nệm - Sản phẩm chuẩn "
            "kích thước dành cho người lớn đọc sách từ HOME MÂY")
        topic_engine.sync_product_system_topics(self.conn, product)
        gan = self.conn.execute(
            """SELECT t.code FROM product_topic pt JOIN topic t ON t.id=pt.topic_id
               WHERE pt.product_id='p-ghe'""").fetchall()
        self.assertNotIn(
            "cong-nghe", [row["code"] for row in gan],
            "ghế mây bị gán vào phụ kiện công nghệ vì chuỗi con 'sach' chứa 'sac'")

    def test_hang_khop_that_su_van_duoc_chon(self):
        product = self._add_product(
            "p-tai", "Tai nghe bluetooth chụp tai chống ồn pin 40 giờ")
        topic_engine.sync_product_system_topics(self.conn, product)

        eligible, reason = shopee_auto_runtime._shopee_product_auto_eligibility(
            self.conn, product, self.channel, self.now, require_auto_schedule=True)
        self.assertTrue(
            eligible,
            f"hàng khớp từ khoá thật sự phải được chọn, nhưng bị loại: {reason}")

    def test_da_chon_thi_phai_qua_duoc_preflight(self):
        """Bất biến chính: không được chọn thứ mà preflight sẽ huỷ."""
        cases = [
            ("p-ghe2", "Ghế mây thư giãn bập bênh ngoài trời dành cho người lớn đọc sách"),
            ("p-tai2", "Tai nghe bluetooth chụp tai chống ồn pin 40 giờ"),
            ("p-sac", "Sạc dự phòng 20000mAh sạc nhanh 22.5W"),
            ("p-son", "Son tint lì Romand Juicy Lasting Tint"),
            ("p-mi", "Mì Kokomi Đại 90 thùng 30 gói"),
        ]
        for product_id, name in cases:
            with self.subTest(san_pham=name[:40]):
                product = self._add_product(product_id, name)
                topic_engine.sync_product_system_topics(self.conn, product)

                selected, _ = shopee_auto_runtime._shopee_product_auto_eligibility(
                    self.conn, product, self.channel, self.now, require_auto_schedule=True)
                if not selected:
                    continue

                # preflight dùng cột channel.niches, không dùng tầng chủ đề
                niches = [code for code in auto_scheduler._channel_niches(self.channel)
                          if code in auto_scheduler.niche.NICHES]
                rejected = auto_scheduler.niche.match_reasons(product, niches)
                self.assertEqual(
                    rejected, [],
                    f"'{name[:50]}' được chọn nhưng preflight sẽ huỷ: {rejected}")


if __name__ == "__main__":
    unittest.main()
