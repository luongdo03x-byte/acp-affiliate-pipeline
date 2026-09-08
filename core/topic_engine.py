"""Dynamic topic hierarchy for Product Pool and channel routing.

System topics are mirrored from ``core.niche.NICHES`` so the existing content
safety rules remain authoritative. Dynamic topics only add routing/filtering
specificity; they never replace system-topic safety checks.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from . import niche
from .db import audit, now, ulid

TOPIC_TYPES = frozenset({"SYSTEM", "AUTO", "MANUAL"})
RULE_MODES = frozenset({"INCLUDE", "EXCLUDE"})
AUTO_CLUSTER_MIN = 5
AUTO_CONFIDENCE_MIN = 0.80
AUTO_MERGE_SIMILARITY = 0.92
DUPLICATE_HINT_SIMILARITY = 0.80

_STOPWORDS = {
    "ao", "quan", "set", "bo", "nu", "nam", "cho", "voi", "va", "mau",
    "hang", "cao", "cap", "chinh", "hang", "dep", "xinh", "form", "size",
    "co", "khong", "loai", "kieu", "thun", "chat", "vai", "new", "sale",
    "san", "pham", "the", "style", "basic", "hot", "gia", "re", "si", "le",
}

# Friendly high-signal subtopics. The engine is still data-driven: a label is
# only created after the configured cluster threshold is reached.
_CANDIDATE_PATTERNS = (
    ("Đồ mặc nhà", (r"\bdo mac nha\b", r"\bmac nha\b", r"\bpijama\b", r"\bpajama\b")),
    ("Bigsize", (r"\bbigsize\b", r"\bbig size\b", r"\b55 90kg\b", r"\bngoai co\b")),
    ("Phụ kiện tóc", (r"\bphu kien toc\b", r"\bkep toc\b", r"\bkep cang cua\b", r"\bbang do\b", r"\bscrunchie\b")),
    ("Đồ đi biển", (r"\bdi bien\b", r"\bdo bien\b", r"\bbeachwear\b")),
    ("Quần ống rộng", (r"\bquan ong rong\b", r"\bong rong\b", r"\bwide leg\b")),
    ("Đồ công sở", (r"\bcong so\b", r"\bdo cong so\b", r"\boffice wear\b")),
    ("Váy maxi", (r"\bvay maxi\b", r"\bdam maxi\b", r"\bmaxi\b")),
    ("Babydoll", (r"\bbabydoll\b",)),
    ("Nhà bếp", (r"\bnha bep\b", r"\bdung cu bep\b", r"\bkitchen\b")),
    ("Phòng ngủ", (r"\bphong ngu\b", r"\bgiuong ngu\b", r"\bbedroom\b")),
)


def _row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (AttributeError, IndexError, KeyError):
        return default


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_text(value)).strip("-") or "topic"


def _request_cache(conn):
    """Chỗ chứa cache của connection, hoặc None với sqlite3.Connection thuần."""
    return getattr(conn, "acp_cache", None)


def invalidate_topic_cache(conn) -> None:
    """Xoá cache chủ đề của connection sau khi ghi vào topic/channel_topic_rule.

    Gọi ở mọi nơi làm đổi luật của kênh hoặc đổi trạng thái/tên chủ đề. Bỏ sót
    một chỗ nghĩa là trong phần còn lại của request đó, người dùng vẫn thấy dữ
    liệu cũ.
    """
    cache = _request_cache(conn)
    if cache is not None:
        cache.pop("system_topics_ready", None)
        cache.pop("channel_rules", None)
        cache.pop("synced_products", None)
        cache.pop("topic_descendants", None)


def ensure_system_topics(conn) -> None:
    # 8 chủ đề hệ thống x (1 SELECT + 1 UPDATE) = 16 truy vấn mỗi lần gọi, mà
    # hàm này nằm ở đầu channel_rules -- tức là chạy lại cho từng cặp sản
    # phẩm x kênh khi dựng trang /sanpham/shopee. Trong một request thì lần
    # đầu đã đủ.
    cache = _request_cache(conn)
    if cache is not None and cache.get("system_topics_ready"):
        return

    stamp = now()
    for code, definition in niche.NICHES.items():
        row = conn.execute("SELECT id FROM topic WHERE code=?", (code,)).fetchone()
        if row:
            conn.execute(
                """UPDATE topic SET name=?, topic_type='SYSTEM', status='ACTIVE', updated_at=?
                   WHERE id=?""",
                (definition["name"], stamp, row["id"]),
            )
            continue
        conn.execute(
            """INSERT INTO topic (
                 id, code, name, topic_type, parent_id, status, confidence,
                 product_count, created_at, updated_at)
               VALUES (?,?,?,'SYSTEM',NULL,'ACTIVE',1.0,0,?,?)""",
            (ulid(), code, definition["name"], stamp, stamp),
        )

    if cache is not None:
        cache["system_topics_ready"] = True


def topic_by_code(conn, code: str):
    ensure_system_topics(conn)
    return conn.execute("SELECT * FROM topic WHERE code=?", (str(code or "").strip(),)).fetchone()


def _unique_code(conn, name: str) -> str:
    base = _slug(name)
    code = base
    index = 2
    while conn.execute("SELECT 1 FROM topic WHERE code=?", (code,)).fetchone():
        code = f"{base}-{index}"
        index += 1
    return code


def create_topic(
    conn,
    *,
    code: str | None,
    name: str,
    topic_type: str = "AUTO",
    parent_id: str | None = None,
    confidence: float | None = None,
    duplicate_candidate_of: str | None = None,
):
    topic_type = str(topic_type or "AUTO").upper()
    if topic_type not in TOPIC_TYPES:
        raise ValueError("topic_type không hợp lệ")
    display = str(name or "").strip()
    if not display:
        raise ValueError("Tên topic không được rỗng")
    code = str(code or "").strip() or _unique_code(conn, display)
    existing = conn.execute("SELECT * FROM topic WHERE code=?", (code,)).fetchone()
    if existing:
        return existing
    stamp = now()
    topic_id = ulid()
    conn.execute(
        """INSERT INTO topic (
             id, code, name, topic_type, parent_id, status, confidence,
             product_count, duplicate_candidate_of, created_at, updated_at)
           VALUES (?,?,?,?,?,'ACTIVE',?,0,?,?,?)""",
        (
            topic_id, code, display, topic_type, parent_id,
            confidence, duplicate_candidate_of, stamp, stamp,
        ),
    )
    audit(
        conn,
        "topic",
        topic_id,
        "created",
        actor="topic_engine",
        detail={"code": code, "topic_type": topic_type, "parent_id": parent_id},
    )
    return conn.execute("SELECT * FROM topic WHERE id=?", (topic_id,)).fetchone()


def _refresh_product_count(conn, topic_id: str) -> None:
    count = conn.execute(
        "SELECT COUNT(*) FROM product_topic WHERE topic_id=?", (topic_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE topic SET product_count=?, updated_at=? WHERE id=?",
        (int(count), now(), topic_id),
    )


def attach_product_topic(conn, product_id: str, topic_id: str, confidence: float, source: str) -> None:
    stamp = now()
    conn.execute(
        """INSERT INTO product_topic (
             product_id, topic_id, confidence, source, created_at, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(product_id, topic_id) DO UPDATE SET
             confidence=excluded.confidence,
             source=excluded.source,
             updated_at=excluded.updated_at""",
        (
            str(product_id), str(topic_id),
            max(0.0, min(1.0, float(confidence))),
            str(source or "AUTO")[:32], stamp, stamp,
        ),
    )
    _refresh_product_count(conn, str(topic_id))


def product_topic_codes(conn, product_id: str) -> list[str]:
    return [
        row["code"]
        for row in conn.execute(
            """SELECT t.code
               FROM product_topic pt JOIN topic t ON t.id=pt.topic_id
               WHERE pt.product_id=? AND t.status='ACTIVE'
               ORDER BY t.topic_type='SYSTEM' DESC, t.name, t.code""",
            (str(product_id),),
        ).fetchall()
    ]


def _ancestors(conn, topic_id: str) -> list[str]:
    out = []
    current = str(topic_id)
    seen = set()
    while current and current not in seen:
        seen.add(current)
        row = conn.execute("SELECT parent_id FROM topic WHERE id=?", (current,)).fetchone()
        if not row or not row["parent_id"]:
            break
        current = row["parent_id"]
        out.append(current)
    return out


def _descendants(conn, topic_id: str) -> set[str]:
    # Cây chủ đề tĩnh trong một request; hàm này bị gọi cho mỗi luật EXCLUDE
    # của mỗi kênh, với mỗi sản phẩm. invalidate_topic_cache() xoá cache.
    cache = _request_cache(conn)
    cached = cache.setdefault("topic_descendants", {}) if cache is not None else None
    if cached is not None and str(topic_id) in cached:
        return set(cached[str(topic_id)])

    result = set()
    pending = [str(topic_id)]
    while pending:
        parent = pending.pop()
        rows = conn.execute(
            "SELECT id FROM topic WHERE parent_id=? AND status='ACTIVE'", (parent,)
        ).fetchall()
        for row in rows:
            child = row["id"]
            if child not in result:
                result.add(child)
                pending.append(child)

    if cached is not None:
        cached[str(topic_id)] = set(result)
    return result


def channel_rules(conn, channel_id: str, *, ensure: bool = True) -> dict:
    # Luật của một kênh không đổi giữa chừng một request, nhưng trang danh sách
    # sản phẩm gọi hàm này một lần cho MỖI cặp sản phẩm x kênh. Cache theo
    # connection biến 751 x 10 lần đọc thành 10 lần.
    key = str(channel_id)
    cache = _request_cache(conn)
    cached_rules = cache.setdefault("channel_rules", {}) if cache is not None else None
    if cached_rules is not None and key in cached_rules:
        return _copy_rules(cached_rules[key])

    # ensure=False cho đường hiển thị: ensure_system_topics ghi 8 lệnh UPDATE
    # mỗi request, mà một request GET thì không được ghi. Chủ đề hệ thống do
    # luồng ghi tạo ra; ở đây chỉ đọc những gì đã có.
    if ensure:
        ensure_system_topics(conn)
    rows = conn.execute(
        """SELECT r.rule_mode, t.id, t.code, t.name
           FROM channel_topic_rule r JOIN topic t ON t.id=r.topic_id
           WHERE r.channel_id=? AND t.status='ACTIVE'
           ORDER BY r.rule_mode, t.name, t.code""",
        (str(channel_id),),
    ).fetchall()
    includes = [dict(row) for row in rows if row["rule_mode"] == "INCLUDE"]
    excludes = [dict(row) for row in rows if row["rule_mode"] == "EXCLUDE"]
    if not includes and not excludes:
        channel = conn.execute("SELECT niches FROM channel WHERE id=?", (str(channel_id),)).fetchone()
        try:
            legacy = json.loads(channel["niches"] or "[]") if channel else []
        except (TypeError, ValueError):
            legacy = []
        for code in legacy:
            topic = conn.execute(
                "SELECT id, code, name FROM topic WHERE code=? AND status='ACTIVE'", (code,)
            ).fetchone()
            if topic:
                includes.append(dict(topic))

    result = {"includes": includes, "excludes": excludes}
    if cached_rules is not None:
        cached_rules[key] = result
    # Trả bản sao: người gọi sửa kết quả không được làm hỏng bản trong cache.
    return _copy_rules(result)


def _copy_rules(rules: dict) -> dict:
    return {
        "includes": [dict(row) for row in rules["includes"]],
        "excludes": [dict(row) for row in rules["excludes"]],
    }


def set_channel_rules(conn, channel_id: str, includes, excludes) -> dict:
    ensure_system_topics(conn)
    channel = conn.execute("SELECT id FROM channel WHERE id=?", (str(channel_id),)).fetchone()
    if not channel:
        raise ValueError("Kênh không tồn tại")

    def _valid_codes(values):
        result = []
        seen = set()
        for raw in values or []:
            code = str(raw or "").strip()
            if not code or code in seen:
                continue
            if conn.execute("SELECT 1 FROM topic WHERE code=? AND status='ACTIVE'", (code,)).fetchone():
                result.append(code)
                seen.add(code)
        return result

    include_codes = _valid_codes(includes)
    exclude_codes = [code for code in _valid_codes(excludes) if code not in include_codes]
    conn.execute("DELETE FROM channel_topic_rule WHERE channel_id=?", (str(channel_id),))
    stamp = now()
    for mode, codes in (("INCLUDE", include_codes), ("EXCLUDE", exclude_codes)):
        for code in codes:
            topic = conn.execute("SELECT id FROM topic WHERE code=?", (code,)).fetchone()
            conn.execute(
                """INSERT INTO channel_topic_rule (
                     channel_id, topic_id, rule_mode, created_at, updated_at)
                   VALUES (?,?,?,?,?)""",
                (str(channel_id), topic["id"], mode, stamp, stamp),
            )

    # Backward compatibility: old scorer/content safety receives only static roots.
    legacy_system_codes = []
    for code in include_codes:
        row = conn.execute("SELECT topic_type FROM topic WHERE code=?", (code,)).fetchone()
        if row and row["topic_type"] == "SYSTEM":
            legacy_system_codes.append(code)
    conn.execute(
        "UPDATE channel SET niches=? WHERE id=?",
        (json.dumps(legacy_system_codes, ensure_ascii=False), str(channel_id)),
    )
    audit(
        conn,
        "channel",
        str(channel_id),
        "set_topic_rules",
        actor="operator",
        detail={"include": include_codes, "exclude": exclude_codes},
    )
    invalidate_topic_cache(conn)
    return {"includes": include_codes, "excludes": exclude_codes}


def system_topic_ids_for_product(conn, product) -> set:
    """Id chủ đề hệ thống của sản phẩm, tính trong bộ nhớ và KHÔNG ghi gì.

    Dùng cho đường hiển thị: một request GET không được ghi vào DB. Kết quả
    khớp với ``sync_product_system_topics`` vì cùng dựa trên
    ``niche.match_reasons``; khác biệt duy nhất là không lưu lại.

    Chủ đề hệ thống nào chưa tồn tại trong bảng ``topic`` thì bỏ qua thay vì
    tạo mới -- luồng ghi (auto-schedule, nhập hàng) sẽ tạo nó.
    """
    codes = {row["code"]: row["id"] for row in conn.execute(
        "SELECT code, id FROM topic WHERE status='ACTIVE' AND topic_type='SYSTEM'")}
    return {
        codes[code] for code in niche.NICHES
        if code in codes and not niche.match_reasons(product, [code])
    }


def channel_accepts_product(conn, channel_id: str, product_id: str, *,
                            product=None, persist: bool = True) -> bool:
    rules = channel_rules(conn, channel_id, ensure=persist)
    rows = conn.execute(
        """SELECT t.id
           FROM product_topic pt JOIN topic t ON t.id=pt.topic_id
           WHERE pt.product_id=? AND t.status='ACTIVE'""",
        (str(product_id),),
    ).fetchall()
    product_ids = {row["id"] for row in rows}
    if not product_ids:
        row = product if product is not None else conn.execute(
            "SELECT * FROM product WHERE id=?", (str(product_id),)).fetchone()
        if row is not None:
            if persist:
                sync_product_system_topics(conn, row)
                rows = conn.execute(
                    """SELECT t.id FROM product_topic pt JOIN topic t ON t.id=pt.topic_id
                       WHERE pt.product_id=? AND t.status='ACTIVE'""",
                    (str(product_id),),
                ).fetchall()
                product_ids = {row_["id"] for row_ in rows}
            else:
                product_ids = system_topic_ids_for_product(conn, row)

    excluded = set()
    for item in rules["excludes"]:
        excluded.add(item["id"])
        excluded.update(_descendants(conn, item["id"]))
    if product_ids & excluded:
        return False

    if not rules["includes"]:
        return True
    included = set()
    for item in rules["includes"]:
        included.add(item["id"])
        included.update(_descendants(conn, item["id"]))
    return bool(product_ids & included)


def _system_parent_for_product(conn, product):
    matches = []
    for code in niche.NICHES:
        if not niche.match_reasons(product, [code]):
            row = conn.execute("SELECT * FROM topic WHERE code=?", (code,)).fetchone()
            if row:
                matches.append(row)
    if matches:
        return matches[0]

    folded = normalize_text(
        " ".join((
            str(_row_get(product, "category_code") or ""),
            str(_row_get(product, "category_data") or ""),
            str(_row_get(product, "name") or ""),
        ))
    )
    fallbacks = (
        ("thoi-trang-nu", (" nu ", "vay", "dam", "chan vay", "ao yem", "croptop", "quan ong rong", "mac nha")),
        ("thoi-trang-nam", (" nam ", "polo", "quan kaki", "giay tay")),
        ("my-pham", ("serum", "kem duong", "son", "skincare", "my pham")),
        ("me-va-be", ("em be", "tre em", "so sinh", "me va be")),
        ("thu-cung", ("cho meo", "thu cung", "pet")),
        ("gia-dung", ("gia dung", "nha bep", "phong ngu", "noi chien", "hop dung")),
        ("cong-nghe", ("tai nghe", "sac", "ban phim", "chuot", "op lung", "cong nghe")),
        ("the-thao", ("the thao", "yoga", "fitness", "da ngoai")),
    )
    padded = f" {folded} "
    for code, tokens in fallbacks:
        if any(token in padded for token in tokens):
            return conn.execute("SELECT * FROM topic WHERE code=?", (code,)).fetchone()
    return None


def sync_product_system_topics(conn, product) -> list[str]:
    # Kết quả chỉ phụ thuộc vào sản phẩm, không phụ thuộc kênh -- nhưng lớp
    # kiểm tra điều kiện gọi hàm này một lần cho MỖI kênh, tức 10 lần cho cùng
    # một sản phẩm khi dựng trang. Mỗi lần chạy tới 8 SELECT + 8 lệnh ghi, nên
    # đây là phần đắt nhất còn lại sau khi đã cache channel_rules.
    product_id = str(_row_get(product, "id") or "")
    cache = _request_cache(conn)
    synced = cache.setdefault("synced_products", {}) if cache is not None else None
    if synced is not None and product_id in synced:
        return list(synced[product_id])

    ensure_system_topics(conn)
    attached = []
    for code in niche.NICHES:
        if niche.match_reasons(product, [code]):
            continue
        row = conn.execute("SELECT id FROM topic WHERE code=?", (code,)).fetchone()
        if row:
            attach_product_topic(conn, _row_get(product, "id"), row["id"], 1.0, "SYSTEM")
            attached.append(code)
    if not attached:
        fallback = _system_parent_for_product(conn, product)
        if fallback:
            attach_product_topic(conn, _row_get(product, "id"), fallback["id"], 0.80, "SYSTEM_FALLBACK")
            attached.append(fallback["code"])

    if synced is not None and product_id:
        synced[product_id] = list(attached)
    return attached


def _topic_similarity(left: str, right: str) -> float:
    a = normalize_text(left)
    b = normalize_text(right)
    # Filler particles should not stop obvious aliases from merging.
    filler = {"o", "thoi", "trang", "san", "pham"}
    a_tokens = [token for token in a.split() if token not in filler]
    b_tokens = [token for token in b.split() if token not in filler]
    a_norm = " ".join(a_tokens)
    b_norm = " ".join(b_tokens)
    sequence = SequenceMatcher(None, a_norm, b_norm).ratio()
    a_set, b_set = set(a_tokens), set(b_tokens)
    union = a_set | b_set
    jaccard = len(a_set & b_set) / len(union) if union else 0.0
    if a_set and a_set == b_set:
        return 1.0
    return max(sequence, jaccard)


def find_or_create_dynamic_topic(
    conn,
    *,
    parent_id: str | None,
    candidate_name: str,
    confidence: float,
    product_count: int,
) -> dict:
    ensure_system_topics(conn)
    candidate_name = str(candidate_name or "").strip()
    normalized = normalize_text(candidate_name)
    if not normalized:
        raise ValueError("candidate_name không hợp lệ")

    alias = conn.execute(
        """SELECT t.* FROM topic_alias a JOIN topic t ON t.id=a.topic_id
           WHERE a.alias_normalized=? AND t.status='ACTIVE'""",
        (normalized,),
    ).fetchone()
    if alias:
        return {"action": "merged", "topic_id": alias["id"], "name": alias["name"]}

    rows = conn.execute(
        """SELECT * FROM topic
           WHERE status='ACTIVE' AND ((parent_id IS NULL AND ? IS NULL) OR parent_id=?)
           ORDER BY topic_type='SYSTEM' DESC, name, id""",
        (parent_id, parent_id),
    ).fetchall()
    best = None
    best_score = 0.0
    for row in rows:
        score = _topic_similarity(candidate_name, row["name"])
        if score > best_score:
            best, best_score = row, score
    if best is not None and best_score >= AUTO_MERGE_SIMILARITY:
        conn.execute(
            """INSERT OR IGNORE INTO topic_alias (alias_normalized, alias_display, topic_id, created_at)
               VALUES (?,?,?,?)""",
            (normalized, candidate_name, best["id"], now()),
        )
        conn.execute(
            """UPDATE topic SET confidence=MAX(COALESCE(confidence,0), ?), updated_at=?
               WHERE id=?""",
            (float(confidence), now(), best["id"]),
        )
        return {"action": "merged", "topic_id": best["id"], "name": best["name"]}

    duplicate_id = best["id"] if best is not None and best_score >= DUPLICATE_HINT_SIMILARITY else None
    topic = create_topic(
        conn,
        code=None,
        name=candidate_name,
        topic_type="AUTO",
        parent_id=parent_id,
        confidence=confidence,
        duplicate_candidate_of=duplicate_id,
    )
    conn.execute(
        "UPDATE topic SET product_count=MAX(product_count, ?), updated_at=? WHERE id=?",
        (int(product_count), now(), topic["id"]),
    )
    return {"action": "created", "topic_id": topic["id"], "name": topic["name"]}


def _friendly_candidates(name: str) -> list[tuple[str, float]]:
    folded = normalize_text(name)
    out = []
    for label, patterns in _CANDIDATE_PATTERNS:
        if any(re.search(pattern, folded) for pattern in patterns):
            out.append((label, 0.95))
    return out


def _generic_phrases(name: str) -> list[str]:
    tokens = [
        token for token in normalize_text(name).split()
        if len(token) >= 3 and token not in _STOPWORDS and not token.isdigit()
    ]
    phrases = []
    for width in (3, 2):
        for index in range(0, max(0, len(tokens) - width + 1)):
            phrase = " ".join(tokens[index:index + width])
            if len(phrase) >= 7:
                phrases.append(phrase)
    return phrases[:8]


def _display_generic(phrase: str) -> str:
    return " ".join(part.capitalize() for part in str(phrase).split())


def discover_dynamic_topics(conn) -> dict:
    """Discover repeated subtopics from the current Shopee Product Pool.

    The operation is deterministic and safe to rerun. It mirrors system topics,
    clusters high-signal title phrases, applies the approved thresholds, and
    attaches products to canonical topics. No network request is performed.
    """
    ensure_system_topics(conn)
    products = conn.execute(
        """SELECT * FROM product
           WHERE provider='SHOPEE_AFFILIATE' AND is_available=1
           ORDER BY id"""
    ).fetchall()
    parent_products = defaultdict(list)
    product_parents = {}
    for product in products:
        sync_product_system_topics(conn, product)
        parent = _system_parent_for_product(conn, product)
        if not parent:
            continue
        parent_products[parent["id"]].append(product)
        product_parents[_row_get(product, "id")] = parent["id"]

    clusters = defaultdict(lambda: {"products": set(), "scores": []})
    for product in products:
        product_id = _row_get(product, "id")
        parent_id = product_parents.get(product_id)
        if not parent_id:
            continue
        friendly = _friendly_candidates(_row_get(product, "name", ""))
        if friendly:
            for label, score in friendly:
                key = (parent_id, label)
                clusters[key]["products"].add(product_id)
                clusters[key]["scores"].append(score)
            continue
        for phrase in _generic_phrases(_row_get(product, "name", "")):
            key = (parent_id, _display_generic(phrase))
            clusters[key]["products"].add(product_id)
            clusters[key]["scores"].append(0.80)

    created, merged = [], []
    for (parent_id, label), data in sorted(clusters.items(), key=lambda item: (item[0][0], item[0][1])):
        product_ids = sorted(data["products"])
        if len(product_ids) < AUTO_CLUSTER_MIN:
            continue
        confidence = sum(data["scores"]) / max(1, len(data["scores"]))
        confidence = max(0.0, min(1.0, confidence))
        if confidence < AUTO_CONFIDENCE_MIN:
            continue
        result = find_or_create_dynamic_topic(
            conn,
            parent_id=parent_id,
            candidate_name=label,
            confidence=confidence,
            product_count=len(product_ids),
        )
        for product_id in product_ids:
            attach_product_topic(conn, product_id, result["topic_id"], confidence, "AUTO")
        payload = {
            "topic_id": result["topic_id"],
            "name": result["name"],
            "count": len(product_ids),
            "confidence": confidence,
        }
        (merged if result["action"] == "merged" else created).append(payload)

    return {"products": len(products), "created": created, "merged": merged}


def topic_tree(conn) -> list[dict]:
    ensure_system_topics(conn)
    rows = [
        dict(row) for row in conn.execute(
            """SELECT * FROM topic WHERE status='ACTIVE'
               ORDER BY topic_type='SYSTEM' DESC, name, code"""
        ).fetchall()
    ]
    by_parent = defaultdict(list)
    for row in rows:
        by_parent[row["parent_id"]].append(row)

    def build(parent_id):
        result = []
        for row in by_parent.get(parent_id, []):
            item = dict(row)
            item["children"] = build(row["id"])
            result.append(item)
        return result

    return build(None)


def topic_paths_for_product(conn, product_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT t.* FROM product_topic pt JOIN topic t ON t.id=pt.topic_id
           WHERE pt.product_id=? AND t.status='ACTIVE'
           ORDER BY t.topic_type='SYSTEM' DESC, t.name""",
        (str(product_id),),
    ).fetchall()
    paths = []
    for row in rows:
        names = [row["name"]]
        parent_id = row["parent_id"]
        while parent_id:
            parent = conn.execute("SELECT id, name, parent_id FROM topic WHERE id=?", (parent_id,)).fetchone()
            if not parent:
                break
            names.append(parent["name"])
            parent_id = parent["parent_id"]
        paths.append(" › ".join(reversed(names)))
    return paths
