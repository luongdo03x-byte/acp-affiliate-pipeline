"""Synthetic Vietnamese profile metadata for Account Factory V2."""
from __future__ import annotations

from dataclasses import dataclass
import random
import re
import unicodedata


@dataclass(frozen=True)
class IdentityPools:
    surnames: tuple[str, ...] = (
        "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi",
        "Đỗ", "Hồ", "Ngô", "Dương", "Lý", "Đinh", "Mai",
    )
    female_given: tuple[str, ...] = (
        "Mai Anh", "Ngọc Linh", "Khánh Vy", "Thảo My", "Minh Anh",
        "Thu Trang", "Hà My", "Phương Anh", "Quỳnh Anh", "Bảo Ngọc",
        "Yến Nhi", "Thanh Hằng", "Diệu Linh", "Tú Anh", "Hải Yến",
        "Gia Hân", "Thùy Linh", "Nhật Linh", "Kim Anh", "Phương Thảo",
        "Lan Anh", "Trúc Linh", "Mỹ Duyên", "Thanh Mai",
    )
    male_given: tuple[str, ...] = (
        "Minh Quân", "Đức Anh", "Quang Huy", "Thanh Đạt", "Tuấn Anh",
        "Hoàng Nam", "Minh Đức", "Gia Bảo", "Anh Tuấn", "Hải Đăng",
        "Trọng Nghĩa", "Khánh Duy", "Đình Phong", "Nhật Minh", "Quốc Bảo",
        "Hữu Phúc", "Tiến Đạt", "Xuân Trường", "Đức Minh", "Quang Minh",
    )


@dataclass(frozen=True)
class GeneratedProfile:
    display_name: str
    username: str
    gender_profile: str
    primary_niche: str
    secondary_interest: str
    personality_style: str
    content_tone: str
    bio: str
    avatar_type: str
    avatar_theme: str
    avatar_prompt: str


_NICHE_COUNTS_50 = {
    "beauty": 9,
    "fashion": 9,
    "tech": 8,
    "home": 8,
    "fitness": 8,
    "food": 8,
}

_INTERESTS = {
    "beauty": ("skincare", "self care", "makeup", "daily routine"),
    "fashion": ("outfits", "accessories", "coffee", "daily style"),
    "tech": ("gadgets", "desk setup", "apps", "accessories"),
    "home": ("decor", "organization", "plants", "home finds"),
    "fitness": ("running", "gym gear", "wellness", "active routine"),
    "food": ("coffee", "kitchen tools", "cooking", "food finds"),
}

_PERSONALITIES = ("minimal", "friendly", "casual", "enthusiastic", "reviewer")
_TONES = ("casual", "warm", "concise", "curious", "reviewer")
_AVATAR_THEMES = {
    "beauty": {
        "illustration": ("minimal beauty illustration", "soft skincare graphic"),
        "object": ("skincare flat lay", "flowers and vanity setup"),
    },
    "fashion": {
        "illustration": ("minimal fashion illustration", "stylized outfit graphic"),
        "object": ("outfit flat lay", "bag and accessories"),
    },
    "tech": {
        "illustration": ("minimal technology graphic", "stylized desk setup"),
        "object": ("keyboard and gadget setup", "clean desktop accessories"),
    },
    "home": {
        "illustration": ("minimal home decor illustration", "cozy room graphic"),
        "object": ("decor corner with plants", "organized kitchen shelf"),
    },
    "fitness": {
        "illustration": ("minimal sports illustration", "stylized running scene"),
        "object": ("running shoes and gym gear", "outdoor fitness equipment"),
    },
    "food": {
        "illustration": ("minimal food illustration", "coffee and kitchen graphic"),
        "object": ("coffee and kitchen tools", "simple cooking flat lay"),
    },
}


def _slug(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    normalized = unicodedata.normalize("NFD", value)
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", ascii_like.lower())


def _scaled_counts(weights: dict[str, float], count: int) -> dict[str, int]:
    raw = {name: weight * count for name, weight in weights.items()}
    result = {name: int(value) for name, value in raw.items()}
    remainder = count - sum(result.values())
    order = sorted(weights, key=lambda name: (raw[name] - result[name], weights[name]), reverse=True)
    for name in order[:remainder]:
        result[name] += 1
    return result


def _expanded(counts: dict[str, int], rng: random.Random) -> list[str]:
    values = [name for name, total in counts.items() for _ in range(total)]
    rng.shuffle(values)
    return values


def _username_candidates(surname: str, given: str) -> list[tuple[str, str]]:
    surname_slug = _slug(surname)
    given_slug = _slug(given)
    surname_initial = surname_slug[:1]
    given_parts = [_slug(part) for part in given.split() if _slug(part)]
    last_given = given_parts[-1] if given_parts else given_slug
    candidates = [
        (f"{given_slug}.{surname_slug}", "given.surname"),
        (f"{given_slug}.{surname_initial}", "given.surname_initial"),
        (f"{surname_slug}.{given_slug}", "surname.given"),
        (f"{surname_slug}{given_slug}", "full_name_compact"),
        (f"{last_given}.{surname_slug}", "last_given.surname"),
        (f"{given_slug}{surname_initial}", "given.surname_initial_compact"),
    ]
    seen = set()
    return [(value, pattern) for value, pattern in candidates if value and not (value in seen or seen.add(value))]


def _select_username(surname: str, given: str, used: set[str], pattern_counts: dict[str, int]) -> str:
    candidates = _username_candidates(surname, given)
    scored = []
    given_slug = _slug(given)
    for value, pattern in candidates:
        if value in used:
            continue
        score = 100
        score += 20 if value.startswith(given_slug) else 0
        score += 10 if not any(ch.isdigit() for ch in value) else 0
        score -= len(value) * 0.25
        score -= pattern_counts.get(pattern, 0) * 2
        scored.append((score, value, pattern))
    if scored:
        _, value, pattern = max(scored, key=lambda item: (item[0], item[1]))
        pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        return value

    base = _slug(given) or "profile"
    surname_slug = _slug(surname)
    for number in range(2, 100):
        value = f"{base}.{surname_slug}{number}" if surname_slug else f"{base}{number}"
        if value not in used:
            pattern_counts["numeric_fallback"] = pattern_counts.get("numeric_fallback", 0) + 1
            return value
    raise RuntimeError("could not generate a unique username")


def _bio(niche: str, interest: str, tone: str, rng: random.Random) -> str:
    templates = (
        "{interest} • everyday finds",
        "{interest}, little routines & useful finds",
        "sharing {interest} and things worth trying",
        "{interest} • simple notes • daily finds",
    )
    text = rng.choice(templates).format(interest=interest)
    return text if tone != "warm" else f"{text} ✨"


def generate_profiles(
    count: int = 50,
    seed: int | None = None,
    *,
    pools: IdentityPools | None = None,
) -> list[GeneratedProfile]:
    if count <= 0:
        return []
    pools = pools or IdentityPools()
    if not pools.surnames or not pools.female_given or not pools.male_given:
        raise ValueError("identity pools must contain surnames and both given-name sets")

    rng = random.Random(seed)
    gender_counts = {"female": 35, "male": 15} if count == 50 else _scaled_counts(
        {"female": 0.7, "male": 0.3}, count
    )
    niche_counts = _NICHE_COUNTS_50.copy() if count == 50 else _scaled_counts(
        {name: total / 50 for name, total in _NICHE_COUNTS_50.items()}, count
    )
    avatar_counts = {"illustration": 30, "object": 20} if count == 50 else _scaled_counts(
        {"illustration": 0.6, "object": 0.4}, count
    )

    genders = _expanded(gender_counts, rng)
    niches = _expanded(niche_counts, rng)
    avatar_types = _expanded(avatar_counts, rng)
    used_usernames: set[str] = set()
    pattern_counts: dict[str, int] = {}
    result: list[GeneratedProfile] = []

    for index in range(count):
        gender = genders[index]
        surname = rng.choice(pools.surnames)
        given = rng.choice(pools.female_given if gender == "female" else pools.male_given)
        display_name = f"{surname} {given}"
        username = _select_username(surname, given, used_usernames, pattern_counts)
        used_usernames.add(username)

        niche = niches[index]
        interest = rng.choice(_INTERESTS[niche])
        personality = rng.choice(_PERSONALITIES)
        tone = rng.choice(_TONES)
        avatar_type = avatar_types[index]
        avatar_theme = rng.choice(_AVATAR_THEMES[niche][avatar_type])
        avatar_prompt = f"{avatar_theme}, clean profile avatar, no text, no real-person impersonation"

        result.append(GeneratedProfile(
            display_name=display_name,
            username=username,
            gender_profile=gender,
            primary_niche=niche,
            secondary_interest=interest,
            personality_style=personality,
            content_tone=tone,
            bio=_bio(niche, interest, tone, rng),
            avatar_type=avatar_type,
            avatar_theme=avatar_theme,
            avatar_prompt=avatar_prompt,
        ))
    return result
