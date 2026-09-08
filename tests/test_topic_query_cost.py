"""Chi phí truy vấn của tầng chủ đề khi dựng danh sách sản phẩm.

Trang /sanpham/shopee gọi ``channel_rules`` một lần cho MỖI cặp sản phẩm×kênh.
Mỗi lần gọi lại chạy ``ensure_system_topics`` (16 truy vấn) rồi đọc lại luật của
cùng một kênh. Với 751 sản phẩm và 10 kênh, trang phát sinh hơn 150.000 truy vấn
và mất khoảng 4,5 phút, tức là người dùng không bao giờ mở được.

Dữ liệu ở đây không đổi trong một lần dựng trang, mà mỗi request lại dùng một
sqlite3.Connection riêng (``db.connect``), nên cache theo connection chính là
cache theo request. Các test dưới ghim: gọi lại không được chạm DB nữa, nhưng
sửa luật thì lần đọc kế tiếp phải thấy luật mới.
"""
import os
import tempfile
import unittest

from acp.core import db, niche, topic_engine


class CountingTrace:
    """Đếm số câu lệnh SQL mà một connection thực sự gửi xuống sqlite."""

    def __init__(self, conn):
        self.conn = conn
        self.count = 0

    def __enter__(self):
        self.conn.set_trace_callback(self._on_statement)
        return self

    def __exit__(self, *exc):
        self.conn.set_trace_callback(None)
        return False

    def _on_statement(self, _statement):
        self.count += 1


class TopicQueryCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "topic-cost.db")
        self.addCleanup(self._restore_db_path)
        db.init_db()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        self.channel_ids = []
        for index in range(3):
            channel_id = f"ch{index}"
            self.conn.execute(
                """INSERT INTO channel (id, code, platform, handle, status, enabled,
                                        niches, created_at)
                   VALUES (?,?,'threads',?,'ACTIVE',1,'[\"my-pham\"]',datetime('now'))""",
                (channel_id, f"threads_test_{index}", f"@test{index}"),
            )
            self.channel_ids.append(channel_id)

    def _restore_db_path(self):
        db.DB_PATH = self.old_db_path

    def _add_product(self, product_id: str, name: str, category_code: str = "khac") -> dict:
        """Gắn chủ đề ghi vào product_topic, có khoá ngoại tới product."""
        self.conn.execute(
            """INSERT INTO product (id, source, merchant, external_product_id, name,
                                    current_price, commission_value, category_code,
                                    product_url, is_available, created_at, updated_at,
                                    affiliate_link_status, post_count)
               VALUES (?,'shopee','shopee.vn',?,?,100000,10000,?,?,1,
                       datetime('now'),datetime('now'),'READY',0)""",
            (product_id, product_id, name, category_code, f"https://shopee.vn/{product_id}"),
        )
        return {"id": product_id, "name": name, "category_code": category_code,
                "merchant": "shopee.vn"}

    def test_doc_lai_luat_cua_cung_kenh_khong_cham_db_nua(self):
        first = topic_engine.channel_rules(self.conn, self.channel_ids[0])
        with CountingTrace(self.conn) as trace:
            again = topic_engine.channel_rules(self.conn, self.channel_ids[0])
        self.assertEqual(again, first, "kết quả đọc lại phải giống hệt lần đầu")
        self.assertEqual(
            trace.count, 0,
            f"đọc lại luật của cùng kênh vẫn phát sinh {trace.count} truy vấn")

    def test_quet_nhieu_san_pham_khong_lam_truy_van_tang_theo_so_lan_goi(self):
        """Mô phỏng vòng lặp của trang: mỗi 'sản phẩm' đọc luật của cả 3 kênh."""
        for channel_id in self.channel_ids:
            topic_engine.channel_rules(self.conn, channel_id)

        with CountingTrace(self.conn) as trace:
            for _product in range(25):
                for channel_id in self.channel_ids:
                    topic_engine.channel_rules(self.conn, channel_id)
        self.assertEqual(
            trace.count, 0,
            f"75 lần đọc lại vẫn tốn {trace.count} truy vấn — cache không có tác dụng")

    def test_sua_luat_thi_lan_doc_ke_tiep_phai_thay_luat_moi(self):
        channel_id = self.channel_ids[0]
        topic_engine.channel_rules(self.conn, channel_id)
        topic = topic_engine.topic_by_code(self.conn, "gia-dung")
        self.assertIsNotNone(topic, "chủ đề hệ thống phải tồn tại sau khi khởi tạo")

        topic_engine.set_channel_rules(self.conn, channel_id, [topic["code"]], [])
        after = topic_engine.channel_rules(self.conn, channel_id)
        self.assertEqual(
            [row["code"] for row in after["includes"]], ["gia-dung"],
            "cache phải bị xoá khi luật của kênh thay đổi")

    def test_gan_chu_de_cho_cung_san_pham_chi_chay_mot_lan(self):
        """Lớp kiểm tra điều kiện gọi hàm này một lần cho mỗi kênh, cùng sản phẩm."""
        product = self._add_product("prod-1", "Son Tint lì Romand Juicy Lasting Tint")
        first = topic_engine.sync_product_system_topics(self.conn, product)
        with CountingTrace(self.conn) as trace:
            for _channel in range(10):
                again = topic_engine.sync_product_system_topics(self.conn, product)
        self.assertEqual(again, first, "kết quả gọi lại phải giống lần đầu")
        self.assertEqual(
            trace.count, 0,
            f"10 lần gán lại cho cùng sản phẩm vẫn tốn {trace.count} truy vấn")

    def test_nguoi_goi_sua_ket_qua_khong_lam_hong_cache(self):
        product = self._add_product("prod-2", "Khăn giấy rút Topgia 4 lớp")
        first = topic_engine.sync_product_system_topics(self.conn, product)
        first.append("rác")
        self.assertNotIn(
            "rác", topic_engine.sync_product_system_topics(self.conn, product),
            "cache phải trả bản sao, không phải chính list đã đưa cho người gọi")

    def test_chu_de_he_thong_van_duoc_tao_du_tren_db_moi(self):
        topic_engine.ensure_system_topics(self.conn)
        codes = {
            row["code"] for row in
            self.conn.execute("SELECT code FROM topic WHERE topic_type='SYSTEM'")
        }
        self.assertEqual(codes, set(niche.NICHES), "thiếu chủ đề hệ thống")

    def test_ensure_system_topics_chi_ghi_o_lan_dau(self):
        topic_engine.ensure_system_topics(self.conn)
        with CountingTrace(self.conn) as trace:
            topic_engine.ensure_system_topics(self.conn)
        self.assertEqual(
            trace.count, 0,
            f"gọi lại ensure_system_topics vẫn tốn {trace.count} truy vấn")

    def test_connection_moi_van_tu_khoi_tao_duoc(self):
        """Cache theo connection: connection khác phải tự dựng lại, không dùng nhờ."""
        topic_engine.ensure_system_topics(self.conn)
        other = db.connect()
        self.addCleanup(other.close)
        rules = topic_engine.channel_rules(other, self.channel_ids[0])
        self.assertEqual([row["code"] for row in rules["includes"]], ["my-pham"])


class NicheFoldCacheTests(unittest.TestCase):
    def test_chuan_hoa_chuoi_cho_ket_qua_on_dinh_khi_goi_lai(self):
        raw = "thoi-trang Đồ ngủ Pijama Tiểu Thư Phối Ren Bigsize shopee.vn"
        self.assertEqual(niche._fold(raw), niche._fold(raw))
        self.assertEqual(niche._norm_keep_tone(raw), niche._norm_keep_tone(raw))
        # Giữ nguyên ngữ nghĩa: bỏ dấu và giữ dấu phải khác nhau.
        self.assertIn("do ngu", niche._fold(raw))
        self.assertIn("đồ ngủ", niche._norm_keep_tone(raw))

    def test_chuoi_rong_va_none_khong_lam_hong_cache(self):
        self.assertEqual(niche._fold(""), "")
        self.assertEqual(niche._norm_keep_tone(""), "")
        self.assertEqual(niche._fold(None), "")
        self.assertEqual(niche._norm_keep_tone(None), "")


if __name__ == "__main__":
    unittest.main()
