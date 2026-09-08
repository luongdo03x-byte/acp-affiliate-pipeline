"""Độ phủ của bộ lọc chủ đề trên hàng Shopee thật.

Mỗi ca dưới đây là một sản phẩm CÓ THẬT trong kho đang nuôi các kênh Threads.
Nhóm "phải khớp" là hàng từng bị huỷ oan ngay trước giờ đăng với lý do
``product_no_longer_matches_channel``. Nhóm "không được khớp" quan trọng ngang
thế: nới từ khoá quá tay thì thực phẩm và hàng nam sẽ lọt sang kênh mỹ phẩm hoặc
thời trang nữ, tức là đăng sai kênh lên tài khoản thật.
"""
import unittest

from acp.core import niche


def product(name, category_code="khac", merchant="shopee.vn"):
    return {"name": name, "category_code": category_code, "merchant": merchant}


# (tên sản phẩm, danh mục, chủ đề phải khớp)
MUST_MATCH = [
    # -- Nhà cửa & gia dụng ------------------------------------------------
    ("Khăn Tắm, Khăn Gội, Khăn Mặt Royal Towel Sợi Cotton Mềm Mịn, Thấm Hút Tốt",
     "gia-dung", "gia-dung"),
    ("[LOẠI TO ĐẶC BIỆT] Thùng 10 bịch khăn giấy rút Top Gia treo tường đa sắc 1280 tờ",
     "khac", "gia-dung"),
    ("[1kg] túi đựng rác có quai, bao rác đen tự phân hủy, túi nilong đựng rác",
     "khac", "gia-dung"),
    ("Nước Giặt TopGia 3in1 Hương Huyền Diệu Dịu Nhẹ Làm Sạch Vết Bẩn, Lưu Hương 72h",
     "khac", "gia-dung"),
    ("Cây chà sàn Parroti cán nhôm cao cấp xoay 180 độ thông minh gạt nước chổi cọ sàn",
     "khac", "gia-dung"),
    ("Sáp thơm phòng khử mùi JYoohome Hương thơm dịu nhẹ cho phòng ngủ, nhà vệ sinh",
     "thoi-trang", "gia-dung"),

    # -- Thời trang nữ -----------------------------------------------------
    ("BỘ NGỦ NỮ CAXA SET PIJAMA MẶC NHÀ 2 MÀU XANH CAM", "khac", "thoi-trang-nu"),
    ("Đồ ngủ Pijama Tiểu Thư Phối Ren Bigsize, Chất Lụa Latin cao cấp thời trang nữ freesize",
     "thoi-trang", "thoi-trang-nu"),
    ("Dép Tông Nữ Đế Phẳng Đi Biển Dáng Đẹp - Dép Kẹp Nữ Mùa Hè Thoáng Khí",
     "khac", "thoi-trang-nu"),

    ("Áo yếm nữ ren Mamie có kèm mút, lưng chun, dây cột có thể điều chỉnh",
     "khac", "thoi-trang-nu"),
    ("Nova - Dây áo vest nữ màu hồng chấm bi, thoáng mát phù hợp mùa hè",
     "khac", "thoi-trang-nu"),
    ("Đồ bộ tole nữ from dáng babydoll mặc nhà quần dài tay dài Shop Nghi 85",
     "thoi-trang", "thoi-trang-nu"),

    # -- Phụ kiện công nghệ ------------------------------------------------
    ("Card Màn Hình Gigabyte NVIDIA CMP 30HX D6 6GB GDDR6",
     "phu-kien-cong-nghe", "cong-nghe"),

    # -- Mỹ phẩm -----------------------------------------------------------
    ("[Rom&nd] Son Tint lì cho môi căng mọng Hàn Quốc Romand Juicy Lasting Tint 5.5g",
     "khac", "my-pham"),

    # -- Thú cưng ----------------------------------------------------------
    ("Cát Nhật đen MoonCat 18L chính hãng túi 8kg", "khac", "thu-cung"),

    # -- Mẹ & bé -----------------------------------------------------------
    ("KHĂN VẢI KHÔ ĐA NĂNG DÀNH CHO MẸ VÀ BÉ GUMI 300/600/900g", "me-va-be", "me-va-be"),
]

# (tên sản phẩm, danh mục, chủ đề KHÔNG được khớp, vì sao)
MUST_NOT_MATCH = [
    ("Nước Mắm CHIN-SU Cá Hồi Đậm Đặc Chai 500ml", "khac", "gia-dung",
     "thực phẩm không phải hàng gia dụng"),
    ("[Chọn vị] Mì Kokomi Đại 90 - Thùng 30 Gói x 90g", "khac", "gia-dung",
     "thực phẩm không phải hàng gia dụng"),
    ("Thùng 48 Hộp Sữa Tươi Tiệt Trùng Vinamilk Green Farm Rất ít đường 180ml",
     "khac", "me-va-be", "sữa uống là thực phẩm, nhóm mẹ & bé cấm hàng dinh dưỡng"),
    ("Thùng SBPS Nutifood GrowPLUS+ ít đường Suy Dinh Dưỡng - Tăng Cân, Tăng Chiều Cao",
     "khac", "me-va-be", "sản phẩm dinh dưỡng, cần giấy phép riêng"),
    ("Áo sơ mi nam công sở dài tay vải lụa cao cấp", "thoi-trang", "thoi-trang-nu",
     "hàng nam không được lọt vào kênh thời trang nữ"),
    ("Quần áo trẻ em bé gái mùa hè cotton", "thoi-trang", "thoi-trang-nu",
     "hàng trẻ em không được lọt vào kênh thời trang nữ"),
    ("Viên uống collagen trắng da đẹp dáng", "khac", "my-pham",
     "thực phẩm chức năng bị loại khỏi nhóm mỹ phẩm"),
    ("[CÓ SIZE MỚI] Nước Tẩy Trang làm sạch sâu dịu nhẹ cho mọi loại da", "khac", "gia-dung",
     "tẩy trang là mỹ phẩm; từ khoá hoá chất tẩy rửa không được nuốt nó"),
    ("Balo sao vàng GENBAG balo thời trang nữ đi học đi chơi vải dù trượt nước vừa laptop",
     "khac", "cong-nghe", "balo là hàng thời trang dù mô tả có chữ laptop"),
]


class NicheCoverageTests(unittest.TestCase):
    def test_hang_bi_bo_sot_phai_khop_dung_chu_de(self):
        for name, category, expected in MUST_MATCH:
            with self.subTest(san_pham=name[:45], chu_de=expected):
                reasons = niche.match_reasons(product(name, category), [expected])
                self.assertEqual(
                    reasons, [],
                    f"'{name[:60]}' phải thuộc {expected} nhưng bị loại: {reasons}")

    def test_hang_ngoai_chu_de_van_phai_bi_loai(self):
        for name, category, forbidden, why in MUST_NOT_MATCH:
            with self.subTest(san_pham=name[:45], chu_de=forbidden):
                reasons = niche.match_reasons(product(name, category), [forbidden])
                self.assertNotEqual(
                    reasons, [],
                    f"'{name[:60]}' KHÔNG được thuộc {forbidden} ({why})")

    def test_moi_include_token_deu_khop_chinh_no(self):
        """Token viết sai (thừa dấu cách, sai dấu) sẽ không bao giờ khớp gì.

        Ràng buộc này bắt lỗi chính tả ngay khi thêm từ khoá mới, thay vì để nó
        nằm im trong danh sách và âm thầm không có tác dụng.
        """
        for code, spec in niche.NICHES.items():
            for token in spec["include_tokens"]:
                with self.subTest(chu_de=code, token=token):
                    haystack = f" {niche._norm_keep_tone(token)} "
                    self.assertTrue(
                        niche._has_word(haystack, token.lower()),
                        f"token '{token}' của {code} không tự khớp chính nó")


if __name__ == "__main__":
    unittest.main()
