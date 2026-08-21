import unittest

from acp.core.shopee_csv_import import (
    ShopeeCsvError,
    dedupe_upload_rows,
    parse_commission_amount,
    parse_commission_percent,
    parse_price_vnd,
    parse_shopee_affiliate_csv,
    parse_sold_count,
)


HEADER = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,"
    "Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"
)


def csv_bytes(row: str, *, bom: bool = False) -> bytes:
    text = HEADER + row + "\n"
    if bom:
        text = "\ufeff" + text
    return text.encode("utf-8")


class ShopeeCsvParserTests(unittest.TestCase):
    def test_verified_number_formats(self):
        self.assertEqual(parse_price_vnd("53,9k"), 53_900)
        self.assertEqual(parse_price_vnd("300,0k"), 300_000)
        self.assertEqual(parse_price_vnd("1,2tr"), 1_200_000)
        self.assertEqual(parse_price_vnd("100,0tr"), 100_000_000)
        self.assertEqual(parse_commission_percent("42,5%"), 42.5)
        self.assertEqual(parse_commission_percent("4,51%"), 4.51)
        self.assertEqual(parse_commission_amount("₫4.000.000"), 4_000_000)
        self.assertEqual(parse_sold_count("300k+"), 300_000)
        self.assertEqual(parse_sold_count("28"), 28)

    def test_blank_optional_numeric_values_are_none(self):
        self.assertIsNone(parse_commission_percent(""))
        self.assertIsNone(parse_commission_amount(""))
        self.assertIsNone(parse_sold_count(""))

    def test_invalid_numeric_values_are_rejected(self):
        for value in ("", "0", "-1", "abc"):
            with self.subTest(price=value):
                with self.assertRaises(ShopeeCsvError):
                    parse_price_vnd(value)
        for value in ("-1%", "100,1%", "abc"):
            with self.subTest(percent=value):
                with self.assertRaises(ShopeeCsvError):
                    parse_commission_percent(value)
        for value in ("-1", "abc"):
            with self.subTest(commission=value):
                with self.assertRaises(ShopeeCsvError):
                    parse_commission_amount(value)
        for value in ("-1", "abc"):
            with self.subTest(sold=value):
                with self.assertRaises(ShopeeCsvError):
                    parse_sold_count(value)

    def test_real_shape_row_preserves_affiliate_link_and_identity(self):
        raw = csv_bytes(
            '20834209498,"Cát Min, mùi thơm","53,9k",10k+,BALA PETSHOP,5%,₫2.695,'
            'https://shopee.vn/product/196194160/20834209498,'
            'https://s.shopee.vn/AUtM2b13go'
        )
        result = parse_shopee_affiliate_csv(raw, "batch.csv")[0]
        self.assertIsNone(result.error)
        self.assertEqual(result.status, "VALID")
        self.assertEqual(result.row.shop_id, "196194160")
        self.assertEqual(result.row.item_id, "20834209498")
        self.assertEqual(result.row.current_price, 53_900)
        self.assertEqual(result.row.sold_count, 10_000)
        self.assertEqual(result.row.shop_name, "BALA PETSHOP")
        self.assertEqual(result.row.commission_rate_percent, 5.0)
        self.assertEqual(result.row.commission_amount, 2_695)
        self.assertEqual(
            result.row.product_url,
            "https://shopee.vn/product/196194160/20834209498",
        )
        self.assertEqual(result.row.affiliate_url, "https://s.shopee.vn/AUtM2b13go")
        self.assertEqual(result.row.source_filename, "batch.csv")
        self.assertEqual(result.row.source_row_number, 2)

    def test_utf8_bom_is_supported(self):
        raw = csv_bytes(
            "123,X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,"
            "https://s.shopee.vn/abc",
            bom=True,
        )
        self.assertIsNone(parse_shopee_affiliate_csv(raw, "bom.csv")[0].error)

    def test_quoted_name_with_comma_is_parsed_as_one_field(self):
        raw = csv_bytes(
            '123,"Tên, có dấu phẩy",100,0,Shop,5%,₫5,'
            'https://shopee.vn/product/1/123,https://s.shopee.vn/abc'
        )
        result = parse_shopee_affiliate_csv(raw, "quoted.csv")[0]
        self.assertEqual(result.row.name, "Tên, có dấu phẩy")

    def test_item_id_mismatch_is_row_error_without_aborting_file(self):
        raw = (
            HEADER
            + "999,X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,https://s.shopee.vn/abc\n"
            + "124,Y,200,1,Shop,5%,₫10,https://shopee.vn/product/1/124,https://s.shopee.vn/def\n"
        ).encode("utf-8")
        results = parse_shopee_affiliate_csv(raw, "mixed.csv")
        self.assertEqual(len(results), 2)
        self.assertIsNone(results[0].row)
        self.assertEqual(results[0].status, "ERROR")
        self.assertIn("không khớp", results[0].error.lower())
        self.assertIsNone(results[1].error)

    def test_missing_required_columns_is_batch_error(self):
        raw = "Mã sản phẩm,Tên sản phẩm\n123,X\n".encode("utf-8")
        with self.assertRaises(ShopeeCsvError):
            parse_shopee_affiliate_csv(raw, "missing.csv")

    def test_invalid_utf8_is_batch_error(self):
        with self.assertRaises(ShopeeCsvError):
            parse_shopee_affiliate_csv(b"\xff\xfe\xfa", "bad.csv")

    def test_required_row_fields_are_enforced(self):
        cases = {
            "item": ",X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,https://s.shopee.vn/abc",
            "name": "123,,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,https://s.shopee.vn/abc",
            "price": "123,X,,0,Shop,5%,₫5,https://shopee.vn/product/1/123,https://s.shopee.vn/abc",
            "product_url": "123,X,100,0,Shop,5%,₫5,,https://s.shopee.vn/abc",
            "affiliate_url": "123,X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,",
        }
        for name, row in cases.items():
            with self.subTest(field=name):
                result = parse_shopee_affiliate_csv(csv_bytes(row), "required.csv")[0]
                self.assertEqual(result.status, "ERROR")
                self.assertIsNone(result.row)

    def test_product_url_must_be_direct_https_shopee_vietnam_url(self):
        urls = (
            "http://shopee.vn/product/1/123",
            "https://example.com/product/1/123",
            "https://s.shopee.vn/abc",
            "https://user@shopee.vn/product/1/123",
        )
        for url in urls:
            with self.subTest(url=url):
                raw = csv_bytes(
                    f"123,X,100,0,Shop,5%,₫5,{url},https://s.shopee.vn/abc"
                )
                result = parse_shopee_affiliate_csv(raw, "url.csv")[0]
                self.assertEqual(result.status, "ERROR")

    def test_affiliate_url_must_be_exact_https_short_host(self):
        urls = (
            "http://s.shopee.vn/abc",
            "https://shopee.vn/product/1/123",
            "https://evil-s.shopee.vn/abc",
            "https://user@s.shopee.vn/abc",
        )
        for url in urls:
            with self.subTest(url=url):
                raw = csv_bytes(
                    f"123,X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,{url}"
                )
                result = parse_shopee_affiliate_csv(raw, "aff.csv")[0]
                self.assertEqual(result.status, "ERROR")

    def test_control_characters_in_urls_are_rejected(self):
        raw = csv_bytes(
            "123,X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,"
            "https://s.shopee.vn/abc\x01"
        )
        result = parse_shopee_affiliate_csv(raw, "control.csv")[0]
        self.assertEqual(result.status, "ERROR")

    def test_dedupe_keeps_last_valid_occurrence(self):
        first = parse_shopee_affiliate_csv(
            csv_bytes(
                "123,Old,100,1,Shop,5%,₫5,https://shopee.vn/product/1/123,"
                "https://s.shopee.vn/old"
            ),
            "a.csv",
        )[0]
        second = parse_shopee_affiliate_csv(
            csv_bytes(
                "123,New,120,2,Shop,6%,₫7,https://shopee.vn/product/1/123,"
                "https://s.shopee.vn/new"
            ),
            "b.csv",
        )[0]
        rows = dedupe_upload_rows([first, second])
        self.assertEqual(rows[0].status, "DUPLICATE_IN_UPLOAD")
        self.assertEqual(rows[1].status, "VALID")
        self.assertEqual(rows[1].row.name, "New")
        self.assertEqual(rows[1].row.affiliate_url, "https://s.shopee.vn/new")

    def test_invalid_rows_are_not_used_as_duplicate_winners(self):
        valid = parse_shopee_affiliate_csv(
            csv_bytes(
                "123,Good,100,1,Shop,5%,₫5,https://shopee.vn/product/1/123,"
                "https://s.shopee.vn/good"
            ),
            "good.csv",
        )[0]
        invalid = parse_shopee_affiliate_csv(
            csv_bytes(
                "999,Bad,100,1,Shop,5%,₫5,https://shopee.vn/product/1/123,"
                "https://s.shopee.vn/bad"
            ),
            "bad.csv",
        )[0]
        rows = dedupe_upload_rows([valid, invalid])
        self.assertEqual(rows[0].status, "VALID")
        self.assertEqual(rows[1].status, "ERROR")


if __name__ == "__main__":
    unittest.main()
