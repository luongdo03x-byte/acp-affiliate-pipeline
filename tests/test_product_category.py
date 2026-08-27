"""Suy đoán danh mục từ tên sản phẩm (core/product_category.py).

Phủ: token mạnh/thường + ngưỡng MIN_SCORE, thứ tự ưu tiên khi hoà điểm,
và việc import CSV Shopee phải gán danh mục suy đoán thay vì 'khac' cứng.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acp.core.product_category import infer_category  # noqa: E402


class InferCategoryTests(unittest.TestCase):
    def test_specific_categories_win_over_generic_words(self):
        self.assertEqual(infer_category("Cát Vệ Sinh Cho Mèo Mooncat 9L"), "thu-cung")
        self.assertEqual(infer_category("Pate cho mèo vị cá 70g"), "thu-cung")
        self.assertEqual(infer_category("Tã quần Bobby siêu mềm"), "me-va-be")
        self.assertEqual(infer_category("Set 2 bát gấu kèm thìa cho bé tập ăn"), "me-va-be")

    def test_cosmetics_vs_personal_care_split(self):
        self.assertEqual(infer_category("Son kem lì MAC màu đỏ"), "my-pham")
        self.assertEqual(infer_category("Phấn phủ bột kiềm dầu 2g"), "my-pham")
        self.assertEqual(infer_category("Sữa rửa mặt Cetaphil dịu nhẹ"), "cham-soc-ca-nhan")
        self.assertEqual(infer_category("Kem chống nắng Biore UV 50g"), "cham-soc-ca-nhan")

    def test_price_led_and_feature_led_household_items(self):
        self.assertEqual(infer_category("Nồi chiên không dầu 5L Digital"), "gia-dung")
        self.assertEqual(infer_category("Chăn ga gối Lụa Thái 4 mùa"), "gia-dung")

    def test_fashion_including_accessories(self):
        self.assertEqual(infer_category("Mũ Lưỡi Trai Vintage Kaki Cotton"), "thoi-trang")
        self.assertEqual(infer_category("Áo Thun Nữ Ôm Body Cổ Tròn"), "thoi-trang")
        self.assertEqual(infer_category("Quần Đùi Kaki Tôn Dáng"), "thoi-trang")

    def test_tech_accessories(self):
        self.assertEqual(infer_category("Tai nghe Bluetooth TWS chống ồn"), "phu-kien-cong-nghe")
        self.assertEqual(infer_category("Ốp lưng iPhone 17 trong suốt"), "phu-kien-cong-nghe")

    def test_unclassifiable_titles_stay_khac(self):
        # Thực phẩm/văn phòng phẩm/đồ chơi ĐÃ CÓ mã riêng từ 2026-08; những
        # gì thực sự không có tín hiệu vẫn phải trung thực là 'khac'.
        for title in ("", "Túi zip đựng đồ đa năng", "Khung ảnh để bàn mini",
                      "Combo tiết kiệm điện năng min"):
            self.assertEqual(infer_category(title), "khac", title)

    def test_new_codes_food_stationery_toys(self):
        self.assertEqual(infer_category("Bánh trung thu nhân đậu xanh"), "thuc-pham")
        self.assertEqual(infer_category("[O'FOOD VN] Rong biển giòn trộn gia vị"), "thuc-pham")
        self.assertEqual(infer_category("Bút bi Thiên Long TL-027"), "van-phong-pham")
        self.assertEqual(infer_category("Mô hình lắp ráp Robot Sempo"), "do-choi")

    def test_word_boundary_no_substring_false_positive(self):
        # "ta" (tã) không được khớp bên trong "túi"/"tạp"...
        self.assertNotEqual(infer_category("Túi giữ nhiệt dã ngoại"), "me-va-be")
        # ...và "mi" (mí/mi giả) không khớp trong "min".
        self.assertNotEqual(infer_category("Bộ combo tiết kiệm điện năng min"), "cham-soc-ca-nhan")


class CsvImportCategoryTests(unittest.TestCase):
    def test_insert_product_uses_inferred_category(self):
        from acp.core import db
        from acp.core.shopee_csv_import import ShopeeAffiliateCsvRow, _insert_product

        previous_db_path = db.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            db.DB_PATH = os.path.join(directory, "csv-cat.db")
            try:
                db.init_db()
                conn = db.connect()
                try:
                    row = ShopeeAffiliateCsvRow(
                        item_id="123", shop_id="456",
                        name="Nồi chiên không dầu 5L Digital",
                        current_price=890_000, commission_amount=20_000,
                        commission_rate_percent=5.0, sold_count=10,
                        product_url="https://shopee.vn/product/1/2",
                        shop_name="Shop A", affiliate_url="https://s.shopee.vn/x",
                        source_filename="test.csv", source_row_number=1,
                    )
                    pid = _insert_product(conn, row)
                    code = conn.execute(
                        "SELECT category_code FROM product WHERE id=?", (pid,)
                    ).fetchone()["category_code"]
                finally:
                    conn.close()
            finally:
                db.DB_PATH = previous_db_path
        self.assertEqual(code, "gia-dung")


if __name__ == "__main__":
    unittest.main(verbosity=2)
