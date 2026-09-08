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


class WriteTrace:
    """Ghi lại mọi câu lệnh làm thay đổi dữ liệu."""

    KEYWORDS = ("INSERT", "UPDATE", "DELETE", "REPLACE")

    def __init__(self, conn):
        self.conn = conn
        self.statements = []

    def __enter__(self):
        self.conn.set_trace_callback(self._on_statement)
        return self

    def __exit__(self, *exc):
        self.conn.set_trace_callback(None)
        return False

    def _on_statement(self, statement):
        head = statement.lstrip()[:12].upper()
        if any(head.startswith(word) for word in self.KEYWORDS):
            self.statements.append(" ".join(statement.split())[:80])


class ProductPoolIsReadOnlyTests(unittest.TestCase):
    """Dựng trang danh sách là một request GET: không được ghi gì vào DB.

    Đường hiển thị từng gọi sync_product_system_topics cho mỗi cặp sản
    phẩm×kênh, tức là hàng trăm lệnh ghi cho một lần xem trang. Những lệnh ghi
    đó phải xếp hàng sau acp-worker (chạy mỗi 60 giây) nên mỗi lệnh mất 93-725ms
    thay vì 0,9ms, và trang không bao giờ dựng xong.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "pool-readonly.db")
        self.addCleanup(self._restore_db_path)
        db.init_db()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        self.conn.execute(
            """INSERT INTO channel (id, code, platform, handle, status, enabled,
                                    auto_schedule_enabled, niches, created_at)
               VALUES ('ch-ro','threads_ro','threads','@ro','ACTIVE',1,1,
                       '[\"my-pham\"]',datetime('now'))""",
        )
        self.conn.execute(
            """INSERT INTO product (id, source, merchant, external_product_id, name,
                                    current_price, commission_value, category_code,
                                    product_url, is_available, created_at, updated_at,
                                    affiliate_link_status, post_count, provider,
                                    main_image_url, last_synced_at, affiliate_url)
               VALUES ('p-ro','shopee','shopee.vn','ext-ro',
                       'Son Tint lì Romand Juicy Lasting Tint 5.5g',
                       100000,10000,'khac','https://shopee.vn/p-ro',1,
                       datetime('now'),datetime('now'),'READY',0,'SHOPEE_AFFILIATE',
                       'https://cdn/p.jpg',datetime('now'),'https://s.shopee.vn/abc')""",
        )
        # Không có job ảnh READY thì hàm kiểm tra thoát sớm và không bao giờ
        # chạm tới tầng chủ đề -- test sẽ xanh giả.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(shopee_image_enrichment_job)")}
        self.conn.execute(
            "INSERT INTO shopee_image_enrichment_job (product_id, status, created_at, updated_at)"
            " VALUES ('p-ro','READY',datetime('now'),datetime('now'))"
            if "created_at" in columns else
            "INSERT INTO shopee_image_enrichment_job (product_id, status) VALUES ('p-ro','READY')"
        )

    def _restore_db_path(self):
        db.DB_PATH = self.old_db_path

    def test_tinh_trang_thai_hien_thi_khong_ghi_gi_vao_db(self):
        from acp.core import shopee_product_pool

        product = dict(self.conn.execute("SELECT * FROM product WHERE id='p-ro'").fetchone())
        product["enrichment_status"] = "READY"
        channels = shopee_product_pool._active_auto_channels(self.conn)
        usage = shopee_product_pool._usage_state(self.conn, "p-ro")
        now_utc = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

        with WriteTrace(self.conn) as trace:
            shopee_product_pool._auto_state(self.conn, product, usage, channels, now_utc)

        self.assertEqual(
            trace.statements, [],
            "trang danh sách đã ghi vào DB: " + "; ".join(trace.statements[:3]))

    def test_duong_chi_doc_cho_cung_ket_qua_voi_duong_ghi(self):
        """Ràng buộc quan trọng nhất: trang và scheduler phải nói cùng một kết quả.

        Nếu hai đường lệch nhau thì người vận hành thấy 'ĐỦ ĐIỀU KIỆN' trên
        trang nhưng tới giờ bài lại bị huỷ, hoặc ngược lại.
        """
        from acp.core import shopee_auto_runtime

        import datetime as _dt
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        product = dict(self.conn.execute("SELECT * FROM product WHERE id='p-ro'").fetchone())
        channel = self.conn.execute("SELECT * FROM channel WHERE id='ch-ro'").fetchone()

        # Chủ đề hệ thống phải tồn tại thì hai đường mới so sánh được -- đúng
        # như trên hệ thống thật, nơi luồng ghi đã tạo chúng từ trước.
        topic_engine.ensure_system_topics(self.conn)

        readonly = shopee_auto_runtime._shopee_product_auto_eligibility(
            self.conn, product, channel, now_utc,
            require_auto_schedule=True, persist_topics=False)
        persisted = shopee_auto_runtime._shopee_product_auto_eligibility(
            self.conn, product, channel, now_utc,
            require_auto_schedule=True, persist_topics=True)

        self.assertEqual(
            readonly, persisted,
            f"đường chỉ đọc trả {readonly} còn đường ghi trả {persisted}")
        self.assertTrue(
            readonly[0],
            "sản phẩm son tint phải đủ điều kiện cho kênh my-pham -- "
            "test sẽ vô nghĩa nếu cả hai đường cùng trả False vì lý do khác")


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
