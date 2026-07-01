from __future__ import annotations

import re
from dataclasses import dataclass


BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "AP": ("angelic pretty", "アンジェリックプリティ", "アンプリ", "angelicpretty", "ap "),
    "BABY": ("baby, the stars shine bright", "baby the stars shine bright", "btssb", "ベイビー", "baby"),
    "AATP": ("alice and the pirates", "aatp", "pirates", "アリスアンドザパイレーツ"),
    "Meta": ("metamorphose", "metamorphose temps de fille", "メタモルフォーゼ", "meta"),
    "MMM": ("moi-meme-moitie", "moi meme moitie", "moi-même-moitié", "moitie", "モワメームモワティエ"),
    "IW": ("innocent world", "イノセントワールド"),
    "VM": ("victorian maiden", "ヴィクトリアンメイデン"),
}

GENERIC_PATTERN_LABELS = {"new_arrivals", "new arrival", "new-arrivals", "unknown", "misc"}


NOISE_PATTERNS = (
    r"\b[A-Z]-\d{2}-\d{2}-\d{2}-[A-Z0-9-]+\b",
    r"\b\d{6,}[-A-Z0-9]*\b",
    r"\bfree\b",
    r"\bone\s*size\b",
    r"\bsize\s*\d+\b",
    r"\b\d+\s*号\b",
    r"\bblack\b|\bwhite\b|\bpink\b|\bsax\b|\bred\b|\bblue\b|\bgreen\b|\bwine\b|\bivory\b",
    r"黒|白|ピンク|サックス|赤|青|緑|ワイン|生成|アイボリー",
    r"\s*[Ｘ×]\s*",
    r"【used】|\[used\]|\bused\b|中古",
)


@dataclass(frozen=True)
class PatternIdentity:
    brand_alias: str
    pattern: str
    key: str


def normalize_brand(value: object, title: object = "") -> str:
    haystack = f"{value or ''} {title or ''}".casefold()
    padded = f" {haystack} "
    for alias, tokens in BRAND_ALIASES.items():
        if any(token.casefold() in padded for token in tokens):
            return alias
    raw = str(value or "").strip()
    return raw.upper()[:24] if raw else ""


def normalize_pattern(title: object, brand_alias: object = "") -> str:
    text = str(title or "")
    if "/" in text:
        left, right = [part.strip() for part in text.split("/", 1)]
        if normalize_brand(left) or str(brand_alias or "").strip():
            text = right
    text = re.sub(r"https?://\S+", " ", text)
    tokens = BRAND_ALIASES.get(str(brand_alias or "").strip(), ())
    if not tokens:
        tokens = tuple(token for values in BRAND_ALIASES.values() for token in values)
    for token in sorted(tokens, key=len, reverse=True):
        if len(token.strip()) >= 5:
            text = re.sub(re.escape(token), " ", text, flags=re.I)
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"[［\[\(（].{0,20}?[］\]\)）]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_·/　")
    return text[:80] or str(title or "").strip()[:80]


def pattern_identity(title: object, brand: object = "") -> PatternIdentity:
    alias = normalize_brand(brand, title)
    pattern = normalize_pattern(title, alias)
    key = "|".join(part.casefold() for part in (alias, pattern) if part)
    return PatternIdentity(brand_alias=alias, pattern=pattern, key=key)
