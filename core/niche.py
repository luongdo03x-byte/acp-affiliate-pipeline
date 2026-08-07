"""Chủ đề kênh (niche).

Kênh chuyên một ngách gần như luôn thắng kênh tạp về EPC: người theo dõi biết họ
đang theo dõi cái gì, nên click có chủ đích hơn.

Module này định nghĩa các chủ đề và cách nhận biết một sản phẩm có thuộc chủ đề
hay không. Việc nhận biết phải chịu được dữ liệu bẩn: mỗi nguồn trả danh mục một
kiểu ("thoi-trang" của Shopee, "Womenswear & Underwear" của TikTok Shop), nên
đối chiếu bằng token trên CẢ danh mục lẫn tên sản phẩm chứ không so khớp tuyệt đối.

Chủ đề nào cũng có thể thêm rào chắn nội dung riêng. Mỹ phẩm là ví dụ bắt buộc:
đây là nhóm hàng quảng cáo có điều kiện, cấm cam kết công dụng.
"""
import re
import unicodedata


def _fold(text: str) -> str:
    """Bỏ dấu, hạ chữ thường, chuẩn hoá dấu phân cách -> so khớp được cả hai ngôn ngữ."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", str(text))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


NICHES = {
    "thoi-trang-nu": {
        "name": "Thời trang nữ",
        "category_tokens": ["thoi trang", "fashion", "womenswear", "women", "apparel", "clothing",
                            "giay dep", "shoes", "tui vi", "bags", "accessories", "do lot",
                            "underwear", "trang suc", "jewelry"],
        # Viết CÓ DẤU: "đầm" khác "dặm", "tóc" khác "tốc".
        "include_tokens": [
            "váy", "đầm", "chân váy", "áo kiểu", "áo thun nữ", "áo khoác nữ", "áo sơ mi nữ",
            "quần jean nữ", "quần tây nữ", "legging", "jumpsuit", "bikini", "đồ bơi nữ",
            "túi xách", "túi đeo", "ví nữ", "giày cao gót", "cao gót", "sandal nữ", "dép nữ",
            "sneaker nữ", "giày nữ", "áo len nữ", "áo nỉ nữ", "set đồ nữ", "đồ bộ nữ",
            "áo ngủ", "nội y", "áo lót", "khuyên tai", "dây chuyền", "vòng tay",
            "kẹp tóc", "thắt lưng nữ", "khăn choàng", "croptop", "áo croptop"],
        "exclude_tokens": ["nam giới", "cho nam", "áo sơ mi nam", "quần nam", "giày nam",
                           "dép nam", "ví nam", "thắt lưng nam", "boxer nam", "quần lót nam",
                           "đồ nam", "trẻ em", "bé trai", "bé gái", "sơ sinh", "em bé"],
        "search_queries": ["váy nữ", "áo kiểu nữ", "túi xách nữ", "giày nữ", "chân váy", "set đồ nữ"],
        "extra_banned_phrases": [],
        "gender": "nu",
        "require_keyword": True,
    },
    "thoi-trang-nam": {
        "name": "Thời trang nam",
        "category_tokens": ["thoi trang", "fashion", "menswear", "men", "apparel", "clothing",
                            "giay dep", "shoes"],
        "include_tokens": [
            "áo sơ mi nam", "áo thun nam", "áo polo", "quần âu", "quần kaki nam", "quần jean nam",
            "quần short nam", "áo khoác nam", "giày tây", "giày nam", "dép nam", "sneaker nam",
            "ví nam", "thắt lưng nam", "cà vạt", "áo hoodie nam", "đồ bộ nam", "boxer"],
        "exclude_tokens": ["cho nữ", "váy", "đầm", "áo lót nữ", "trẻ em", "bé trai", "bé gái"],
        "search_queries": ["áo thun nam", "quần jean nam", "giày nam", "áo sơ mi nam", "ví nam"],
        "extra_banned_phrases": [],
        "require_keyword": True,
    },
    "my-pham": {
        "name": "Mỹ phẩm & chăm sóc da",
        "category_tokens": ["my pham", "cosmetic", "beauty", "personal care", "cham soc ca nhan",
                            "skincare", "makeup", "cham soc da", "cham soc toc", "hair care"],
        "include_tokens": [
            "sữa rửa mặt", "tẩy trang", "toner", "nước hoa hồng", "serum", "tinh chất",
            "kem dưỡng", "dưỡng ẩm", "kem chống nắng", "chống nắng", "mặt nạ", "mask",
            "tẩy tế bào chết", "son môi", "son dưỡng", "cushion", "kem nền", "che khuyết điểm",
            "mascara", "kẻ mắt", "eyeliner", "phấn mắt", "phấn phủ", "chân mày", "nước hoa",
            "sữa tắm", "dưỡng thể", "dầu gội", "dầu xả", "ủ tóc", "dưỡng tóc", "xịt khoáng",
            "kem mắt", "dưỡng môi", "skincare"],
        "exclude_tokens": ["viên uống", "thực phẩm chức năng", "collagen uống", "thuốc",
                           "dược phẩm", "máy trị liệu", "thiết bị y tế", "kim tiêm", "filler", "botox"],
        "search_queries": ["sữa rửa mặt", "kem chống nắng", "serum dưỡng da", "son môi",
                           "kem dưỡng ẩm", "mặt nạ"],
        # Nhóm hàng quảng cáo CÓ ĐIỀU KIỆN: cấm mọi khẳng định điều trị.
        "extra_banned_phrases": [
            "tri mun", "tri nam", "tri tham", "tri seo", "xoa nhan", "xoa tham",
            "trang da", "lam trang", "trang bat tong", "trang sang cap toc",
            "hieu qua sau", "cam ket trang", "cam ket het", "danh bay mun",
            "sach mun", "het nam", "het tham", "tre hoa", "cang bong tuc thi",
            "thay the thuoc", "dac tri", "chua khoi"],
        "require_keyword": True,
    },
    "me-va-be": {
        "name": "Mẹ & bé",
        "category_tokens": ["me va be", "mom", "baby", "kids", "children", "tre em",
                            "do choi", "toys", "mother"],
        "include_tokens": [
            "bình sữa", "núm ti", "máy hâm sữa", "máy tiệt trùng", "tã", "bỉm", "khăn sữa",
            "yếm ăn", "ghế ăn dặm", "xe đẩy", "nôi", "cũi", "địu em bé", "xe tập đi",
            "đồ chơi gỗ", "đồ chơi giáo dục", "xếp hình", "sữa tắm em bé", "quần áo trẻ em",
            "quần áo sơ sinh", "bỉm quần", "bô vệ sinh", "ghế ngồi ô tô", "gối chống trào ngược",
            "máy hút sữa", "áo cho con bú", "đai bụng sau sinh"],
        # Nhóm này cấm hàng ăn uống cho bé: liên quan dinh dưỡng, cần giấy phép riêng.
        "exclude_tokens": ["sữa bột", "sữa công thức", "thực phẩm chức năng", "vitamin",
                           "cốm ăn ngon", "men vi sinh", "thuốc", "dược phẩm"],
        "search_queries": ["bình sữa", "đồ chơi trẻ em", "quần áo trẻ em", "xe đẩy em bé",
                           "ghế ăn dặm", "khăn sữa"],
        "extra_banned_phrases": [
            "tang can nhanh", "giup be an ngon", "phat trien tri nao", "tang chieu cao",
            "tang de khang", "chua bieng an", "bo sung dinh duong", "thay the sua me"],
        "exclude_kids": False,   # đây LÀ nhóm hàng trẻ em
        "require_keyword": True,
    },
    "thu-cung": {
        "name": "Thú cưng",
        "category_tokens": ["thu cung", "pet", "pets", "pet supplies", "cho meo", "dog", "cat"],
        "include_tokens": [
            "cho chó", "cho mèo", "chó mèo", "thú cưng", "hạt cho mèo", "hạt cho chó",
            "pate cho", "cát vệ sinh", "khay vệ sinh", "vòng cổ chó", "vòng cổ mèo",
            "dây dắt", "lồng vận chuyển", "chuồng chó", "chuồng mèo", "nhà cho mèo",
            "cây cào móng", "đồ chơi cho chó", "đồ chơi cho mèo", "bát ăn cho",
            "sữa tắm cho chó", "sữa tắm cho mèo", "lược chải lông", "tông đơ cắt lông",
            "quần áo cho chó", "balo thú cưng", "bàn cào", "snack cho chó", "snack cho mèo"],
        "exclude_tokens": ["thuốc thú y", "vaccine", "kháng sinh", "tẩy giun", "trị ve",
                           "trị ghẻ", "thuốc nhỏ gáy"],
        "search_queries": ["đồ chơi cho mèo", "hạt cho chó", "cát vệ sinh mèo",
                           "vòng cổ thú cưng", "bát ăn cho chó", "lược chải lông"],
        "extra_banned_phrases": ["tri ve", "tri ghe", "chua benh", "dac tri", "thay the thuoc thu y"],
        "exclude_kids": False,
        "require_keyword": True,
    },
    "gia-dung": {
        "name": "Nhà cửa & gia dụng",
        "category_tokens": ["gia dung", "home", "kitchen", "household", "home appliances",
                            "kitchenware", "noi that", "furniture"],
        "include_tokens": [
            "nồi chiên", "nồi cơm", "máy xay", "bình đun", "ấm siêu tốc", "máy hút bụi",
            "cây lau nhà", "hộp đựng", "hộp thủy tinh", "kệ để", "giá treo", "móc dán tường",
            "đèn bàn", "đèn ngủ", "chăn ga", "gối", "rèm cửa", "thảm chùi chân", "máy lọc không khí",
            "bàn ủi", "máy sấy tóc", "quạt", "nồi áp suất", "chảo chống dính", "bộ dao",
            "thớt", "giỏ đựng đồ", "sọt rác", "móc phơi"],
        "exclude_tokens": [],
        "search_queries": ["nồi chiên không dầu", "hộp đựng thực phẩm", "kệ nhà tắm",
                           "máy hút bụi", "đèn ngủ", "chảo chống dính"],
        "extra_banned_phrases": [],
        "require_keyword": True,
    },
    "cong-nghe": {
        "name": "Phụ kiện công nghệ",
        "category_tokens": ["cong nghe", "electronics", "computer", "phone", "phu kien",
                            "mobile accessories", "gadget"],
        "include_tokens": [
            "tai nghe", "sạc nhanh", "củ sạc", "cáp sạc", "pin dự phòng", "sạc dự phòng",
            "bàn phím", "chuột không dây", "giá đỡ", "hub usb", "ổ cứng", "usb", "thẻ nhớ",
            "ốp lưng", "cường lực", "dán màn hình", "giá đỡ điện thoại", "webcam",
            "loa bluetooth", "đèn led phòng", "kẹp điện thoại", "adapter"],
        "exclude_tokens": [],
        "search_queries": ["tai nghe không dây", "sạc dự phòng", "cáp sạc", "bàn phím cơ",
                           "giá đỡ laptop", "ốp lưng"],
        "extra_banned_phrases": [],
        "require_keyword": True,
    },
    "the-thao": {
        "name": "Thể thao & dã ngoại",
        "category_tokens": ["the thao", "sports", "outdoor", "fitness", "da ngoai"],
        "include_tokens": [
            "thảm yoga", "dây nhảy", "tạ tay", "tạ đòn", "bình nước thể thao", "găng tay tập",
            "con lăn massage", "dây kháng lực", "xà đơn", "bóng tập", "đai lưng tập",
            "giày chạy bộ", "quần áo thể thao", "lều cắm trại", "túi ngủ", "bếp cắm trại",
            "ghế xếp", "balo leo núi", "gậy leo núi", "đèn pin"],
        "exclude_tokens": ["thực phẩm chức năng", "whey", "tăng cơ", "đốt mỡ", "giảm cân"],
        "search_queries": ["thảm yoga", "tạ tay", "dây kháng lực", "bình nước thể thao",
                           "lều cắm trại", "giày chạy bộ"],
        "extra_banned_phrases": ["giam can nhanh", "dot mo cap toc", "tang co nhanh", "eo thon"],
        "require_keyword": True,
    },
}

# Kênh có thể chạy nhiều chủ đề cùng lúc.
DEFAULT_ACTIVE = []


def get(code: str):
    return NICHES.get(code)


def list_codes():
    return list(NICHES)


def _norm_keep_tone(text: str) -> str:
    """Hạ chữ thường, chuẩn hoá dấu phân cách, GIỮ NGUYÊN dấu tiếng Việt.

    Bỏ dấu là sai ở đây: "đầm" (váy) và "dặm" (ăn dặm) cùng thành "dam", "tóc" và
    "tốc" cùng thành "toc". Từ khoá tiếng Việt phải so khớp có dấu.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFC", str(text)).lower()
    return re.sub(r"[^0-9a-zà-ỹ]+", " ", s).strip()


def _haystack(product):
    """Trả về (chuỗi có dấu, chuỗi bỏ dấu). Cả hai đều đã đệm khoảng trắng hai đầu
    để so khớp theo ranh giới từ."""
    def field(key):
        try:
            return product[key]
        except (KeyError, TypeError, IndexError):
            return getattr(product, key, "")
    raw = " ".join(str(field(k) or "") for k in ("category_code", "name", "merchant"))
    return f" {_norm_keep_tone(raw)} ", f" {_fold(raw)} "


def _has_word(haystack_padded: str, token: str) -> bool:
    """Khớp theo ranh giới từ, không phải chuỗi con.

    Không có bước này thì "siêu tốc" khớp với "ủ tóc" và "ăn dặm" khớp với "đầm".
    """
    return f" {token.strip()} " in haystack_padded


# "nam" đứng độc lập là dấu hiệu hàng nam, nhưng nó cũng là nửa sau của "việt nam".
# Gỡ các bẫy này trước khi tách từ.
_FALSE_NAM = ("viet nam", "nam dinh", "ha nam", "nam dan", "nam a", "nam my", "nam phi")

# Cố ý KHÔNG đưa "trai"/"gái" vào đây: "ngọc trai" là hạt trai, không phải bé trai.
# Hàng trẻ em đã được chặn bằng exclude_tokens có dấu ("bé trai", "bé gái").
_MALE_WORDS = {"nam", "male", "men", "mens", "boy", "boys"}
_FEMALE_WORDS = {"nu", "female", "women", "womens", "woman", "lady", "ladies", "girl", "girls"}
_KID_WORDS = {"kids", "kid", "baby", "so sinh"}


def _gender_conflict(folded_padded: str, want: str, exclude_kids: bool = True) -> str:
    """Trả về từ gây xung đột giới tính, hoặc chuỗi rỗng nếu không có.

    Chỉ xét từ ĐỘC LẬP -- "giày sneaker nam" phải bắt được dù có chữ chen giữa,
    trong khi "Việt Nam" thì không được tính.
    """
    cleaned = folded_padded
    for trap in _FALSE_NAM:
        cleaned = cleaned.replace(trap, " ")
    tokens = set(cleaned.split())

    if want == "nu":
        if tokens & _FEMALE_WORDS:
            return ""  # đã ghi rõ hàng nữ thì bỏ qua nhiễu
        hit = tokens & _MALE_WORDS
        if hit:
            return sorted(hit)[0]
    if exclude_kids and (tokens & _KID_WORDS or "tre em" in cleaned):
        return "trẻ em"
    return ""


def match_reasons(product, niche_codes) -> list:
    """Trả về lý do sản phẩm KHÔNG thuộc chủ đề đang bật. Rỗng nghĩa là hợp lệ.

    Không bật chủ đề nào -> không lọc gì cả (giữ nguyên hành vi cũ).

    Chỉ trùng danh mục thì CHƯA đủ khi chủ đề đặt require_keyword: danh mục
    "thời trang" của sàn gộp cả hàng nam, nên phải có từ khoá cụ thể mới nhận.
    """
    if not niche_codes:
        return []
    toned, folded = _haystack(product)
    if not toned.strip():
        return ["không đọc được danh mục hoặc tên sản phẩm"]

    names = []
    for code in niche_codes:
        n = NICHES.get(code)
        if not n:
            continue
        names.append(n["name"])

        hit_cat = any(_has_word(folded, _fold(t)) for t in n["category_tokens"])
        hit_kw = any(_has_word(toned, t.lower()) for t in n["include_tokens"])
        if not (hit_cat or hit_kw):
            continue
        if n.get("require_keyword") and not hit_kw:
            continue  # danh mục đúng nhưng không có từ khoá cụ thể -> để chủ đề khác xét

        bad = next((t for t in n["exclude_tokens"] if _has_word(toned, t.lower())), None)
        if bad:
            return [f"thuộc {n['name']} nhưng dính từ loại trừ “{bad}”"]
        clash = _gender_conflict(folded, n.get("gender", ""), n.get("exclude_kids", True))
        if clash:
            return [f"thuộc {n['name']} nhưng là hàng “{clash}”"]
        return []
    return [f"ngoài chủ đề kênh ({', '.join(names) or 'không rõ'})"]


def banned_phrases(niche_codes) -> list:
    """Cụm từ bị cấm thêm theo chủ đề, cộng vào rào chắn chung của content.py."""
    out = []
    for code in niche_codes or []:
        n = NICHES.get(code)
        if n:
            out.extend(n["extra_banned_phrases"])
    return out


def search_queries(niche_codes) -> list:
    out = []
    for code in niche_codes or []:
        n = NICHES.get(code)
        if n:
            out.extend(n["search_queries"])
    return out


def contains_banned(text: str, niche_codes) -> list:
    """Dò cụm cấm theo chủ đề trên văn bản đã bỏ dấu, để bắt cả biến thể không dấu."""
    folded = _fold(text)
    return [p for p in banned_phrases(niche_codes) if p in folded]
