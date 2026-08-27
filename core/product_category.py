"""Suy đoán danh mục sản phẩm từ TÊN -- dùng cho hàng nhập tay/CSV Shopee
mà nguồn không kèm danh mục (hiện CSV đổ cả kho vào 'khac', làm mất tầng
điều chỉnh theo danh mục của chấm điểm và chính sách caption).

Trả về mã CÙNG HỆ với product.category_code đang có:
  gia-dung, thoi-trang, me-va-be, thu-cung, my-pham, the-thao,
  cham-soc-ca-nhan, phu-kien-cong-nghe, thuc-pham, van-phong-pham,
  do-choi -- không đủ dấu hiệu thì 'khac'.

Lưu ý hệ thống: blocked_categories của chấm điểm chặn "thuc-pham-chuc-nang"
(thực phẩm chức năng cần giấy phép) chứ KHÔNG chặn "thuc-pham" thường --
hai mã này cố ý phân biệt.

Cơ chế: chấm điểm theo token trên tên đã bỏ dấu. Token ghép (2+ từ) tính
2 điểm vì chính xác hơn; token đơn tính 1 điểm và phải đạt ngưỡng mới được
xét. Danh mục nhiều điểm hơn thắng; hoà thì nhóm khai báo trước (đặc thù)
ưu tiên. Token được khai thác từ từ vựng THẬT của catalog (08/2026).
"""
import re
import unicodedata

# (mã, (token_mạnh_2điểm..., token_thường_1điểm...)) -- viết KHÔNG DẤU đã fold.
_RULES: list[tuple[str, tuple[tuple[str, ...], tuple[str, ...]]]] = [
    ("me-va-be", (
        ("ta quet", "ta quan", "binh sua", "do choi tre em", "quan ao tre em",
         "do luong nhiet", "may hut sua", "gac be", "xe day em be",
         "bim so sinh", "sua bot", "yeo be", "do boi be", "non be",
         "giay be", "dep be", "khan suong be", "binh pha sua", "me va be",
         "set me va be", "cho be", "danh cho be", "be yeu", "do so sinh",
         "bo so sinh"),
        ("ta", "bim", "em be", "be trai", "be gai", "so sinh", "tre em",
         "do choi", "mam non"),
    )),
    ("thu-cung", (
        ("cat ve sinh", "pate cho", "pate meo", "suong vuot long", "chuong cho",
         "chuong meo", "vong co cho", "vong co meo", "ao cho", "ao meo",
         "do choi cho", "do choi meo", "day dat cho", "day dat meo",
         "thuc an cho", "thuc an meo", "ban ca canh", "long meo", "cho meo",
         "dan cho", "quat meo"),
        ("thu cung", "meo", "hamster", "chim canh"),
    )),
    ("my-pham", (
        ("son moi", "son kem", "kem nen", "phan nuoc", "phan phu",
         "che khuyet diem", "ban trang diem", "ke mat", "chi ke mat",
         "cu tay trang", "bun ve sinh", "mat na giay", "chu ot mau",
         "bang mau", "cau vong mau", "phan mat", "tang trang diem"),
        ("son", "mascara", "my pham", "makeup", "trang diem", "phan"),
    )),
    ("cham-soc-ca-nhan", (
        ("sua tam", "dau goi", "dau xa", "kem danh rang", "nuoc suc mieng",
         "kem duong the", "mat na", "duong toc", "dau duong toc", "gom tay",
         "gom chan", "cham soc da", "cham soc ca nhan", "khan giay lau",
         "duong am da", "nuoc hoa nam", "nuoc hoa nu", "nuoc hoa",
         "nuoc tay trang", "sua rua mat", "tay trang", "kem tri mun",
         "nuoc hoa hong", "kem chong nang", "xit chong nang", "chong nang",
         "chong tia uv"),
        ("serum", "loc da", "dung cu lam dep", "nuoc hoa", "mi", "keo mi",
         "kem duong", "sua rua"),
    )),
    ("the-thao", (
        ("the thao", "quan tap gym", "ao tap gym", "bo tap gym", "may chay bo",
         "vot cau long", "vo cau long", "gay bong bong", "xe dap the thao",
         "gang tap gym", "that tap gym", "tham yoga", "quan yoga",
         "ao ba lo the thao", "tap yoga", "tap gym"),
        ("gym", "yoga", "dumbbell", "xe dap", "bong da", "bong ro",
         "cau long", "bong ban", "quat tap", "day nha", "balo the thao"),
    )),
    ("phu-kien-cong-nghe", (
        ("tai nghe", "cap sac", "sac du phong", "sac nhanh", "op lung",
         "kinh cuong luc", "mieng dan man hinh", "chuot may tinh",
         "chuot gaming", "ban phim co", "ban phim", "loa bluetooth",
         "the nho", "dong ho thong minh", "smartwatch", "quat lam mat",
         "quat mini", "hub usb", "giac sac", "day sac", "cap type c",
         "op macbook", "tui anti giat", "card man hinh", "man hinh may tinh",
         "op dien thoai", "vo dien thoai", "tan nhiet", "vga", "op tablet",
         "sac khong day", "day an toan"),
        ("usb", "webcam", "sac", "bluetooth", "wifi", "dien thoai",
         "may tinh", "linh kien"),
    )),
    ("gia-dung", (
        ("noi chien khong dau", "bo noi", "chao chong dinh", "may hut bui",
         "quat dieu hoa", "den led", "bo dao dua", "ly thuy tinh",
         "binh giu nhiet", "tham lau san", "bo chen", "hop thuc pham",
         "dung cu nha bep", "nha bep", "ke rack", "binh nuoc nong",
         "may loc nuoc", "chau rua", "khan noi", "bon hoa", "may say to",
         "noi com dien", "chan ga goi", "ga goi", "goi su", "chan lan",
         "khan mat", "khan uot", "khan kho", "den ngu", "den ban",
         "du che mua", "che mua", "o che mua", "o gap", "ke treo tuong",
         "treo tuong", "dung cu ve sinh", "cay lau nha", "binh xit",
         "to chuc nha cua", "hop dung cu"),
        ("noi", "chao", "may xay", "quat", "den", "ke", "tham", "khay",
         "binh", "ly", "dao", "thung rac", "gia do", "khan", "nha cua",
         "chan", "goi"),
    )),
    ("thoi-trang", (
        ("chan vay", "ao khoac", "ao so mi", "ao thun", "quan jean",
         "quan au", "quan short", "quan dui", "do boi", "ao ba lo",
         "ao len", "ao ni", "tui xach", "tui deo cheo", "giay cao got",
         "giay sneaker", "dep sandal", "ao lot", "noi y", "do ngu",
         "croptop", "jumpsuit", "legging", "hoodie", "ao polo", "vi da",
         "that lung", "khan choang", "vay maxi", "vay cong so", "do bo",
         "bo do nu", "ao om", "ao ngu", "mu luoi trai", "mu bao hiem",
         "mu len", "giay the thao", "tui nu", "tui nam", "ao dai tay",
         "ao phong", "quan dai", "ao ba lo nu"),
        ("vay", "dam", "bikini", "sneaker", "cao got", "sandal",
         "ao", "quan", "giay", "dep", "tui", "mu", "thoi trang"),
    )),
    ("thuc-pham", (
        ("banh trung thu", "rong bien", "hat dieu", "hat mac ca", "an vat",
         "my goi", "hu tieu", "pho kho", "nuoc ngot", "tra xanh", "ca phe",
         "nuoc ep", "banh trang", "gia vi", "nuoc mam", "tuong ot", "sa ot",
         "sua hat", "thuc pham", "hoa qua say", "trai cay say",
         "mut tet", "keo deo"),
        ("banh", "keo", "ca phe", "tra", "gia vi", "snack", "hat dieu"),
    )),
    ("van-phong-pham", (
        ("but bi", "but gel", "but may", "but chi", "bo but", "hop but",
         "vo ghi", "giay note", "giay in", "dung cu hoc sinh", "but highlight",
         "but long dau", "dinh gim", "kep giay", "gac but", "but bi mau",
         "bo do dung hoc sinh", "but xoa"),
        ("but", "vo", "hoc sinh"),
    )),
    ("do-choi", (
        ("mo hinh lap rap", "do choi lap rap", "lego", "gunpla", "rubik",
         "puzzle", "mo hinh robot", "xe dieu khien", "phieu dieu khien",
         "sung suoi nuoc", "bup be", "gau bong", "xep hinh"),
        ("mo hinh", "dieu khien", "gau bong"),
    )),
]

# Token đơn quá rộng ("áo", "quần", "khăn"...) chỉ cộng điểm chứ không đủ tự
# thắng -- nhóm phải đạt tối thiểu MIN_SCORE mới được xét, để "Áo che nắng ô tô"
# không rơi vào thời trang chỉ vì một chữ "áo".
MIN_SCORE = 2


def _fold(text: str) -> str:
    lowered = unicodedata.normalize("NFD", str(text or "").lower())
    stripped = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    # "đ" là chữ duy nhất của tiếng Việt KHÔNG phân rã được (không nằm trong
    # a-z sau khi bỏ dấu) -- nếu chỉ lọc ký tự thì nó biến MẤT ("đùi" -> "ui").
    stripped = stripped.replace("đ", "d")
    cleaned = re.sub(r"[^a-z0-9]+", " ", stripped)
    return re.sub(r"\s+", " ", cleaned).strip()


def infer_category(title: str) -> str:
    """Mã danh mục suy đoán từ tên sản phẩm; 'khac' nếu không đủ dấu hiệu."""
    flat = f" {_fold(title)} "
    if not flat.strip():
        return "khac"
    best_code, best_score = "khac", 0
    for code, (strong_tokens, weak_tokens) in _RULES:
        score = 0
        for token in strong_tokens:
            if f" {token} " in flat:
                score += 2
        for token in weak_tokens:
            if f" {token} " in flat:
                score += 1
        if score >= MIN_SCORE and score > best_score:
            best_code, best_score = code, score
    return best_code
