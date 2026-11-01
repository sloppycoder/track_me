"""Place-name normalization shared by geocode (derive) and catalog search.

Google returns Chinese city names inconsistently: the same city comes back as
"Cheng Du Shi", "Chengdu", "Xi An Shi" / "Xi'An", or "广州市". "Shi" (市) means
"city" and is normally dropped, so all of these collapse to one spelling:

    Cheng Du Shi -> Chengdu      Xi An Shi -> Xian       Xi'An -> Xian
    Huang Shi Shi -> Huangshi    Chengdu  -> Chengdu     广州市 -> 广州

Rule (``normalize_cn_city``): drop a trailing *separate* "Shi" word (so the real
city Huangshi, written as one word, keeps its "shi"), remove spaces and
apostrophes, then capitalize. Names that end in a district / county / prefecture /
province marker ("Huang Pu Qu", "Xuhui District", "Da Li Bai Zu Zi Zhi Zhou") are
left as-is: they are not cities, and squashing them would be unreadable.
"""

from __future__ import annotations

_CN_CITY_SUFFIX = "shi"  # 市, "city" in pinyin
_CN_CITY_SUFFIX_HANZI = "市"
# Last words marking a non-city admin unit: qu = district, xian = county,
# sheng = province; plus their English forms.
_CN_NON_CITY_SUFFIXES = {"qu", "xian", "sheng", "district", "county", "prefecture", "province"}
_CN_AUTONOMOUS = "zi zhi"  # 自治 "autonomous", as in "... Zi Zhi Zhou"


def normalize_cn_city(name: str | None) -> str | None:
    """Canonical spelling of a Chinese city name (see module docstring)."""
    if not name:
        return name
    name = name.strip()
    if not name.isascii():
        squashed = "".join(name.split())
        if squashed.endswith(_CN_CITY_SUFFIX_HANZI) and len(squashed) > 1:
            squashed = squashed[:-1]
        return squashed
    words = name.split()
    if len(words) > 1 and words[-1].lower() == _CN_CITY_SUFFIX:
        words = words[:-1]
    elif len(words) > 1 and (
        words[-1].lower() in _CN_NON_CITY_SUFFIXES or _CN_AUTONOMOUS in name.lower()
    ):
        return name  # district / county / prefecture: leave readable
    squashed = "".join(words).replace("'", "")
    return squashed[:1].upper() + squashed[1:].lower()


def city_query_variants(text: str) -> list[str]:
    """The text plus its normalized Chinese-city form, for case-insensitive search
    (so "Cheng Du Shi" or "chengdu" finds the stored "Chengdu")."""
    variants = [text]
    norm = normalize_cn_city(text)
    if norm and norm.lower() != text.lower():
        variants.append(norm)
    return variants
