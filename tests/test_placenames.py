"""Chinese city-name normalization (Google returns the same city many ways)."""

import pytest

from track_me.placenames import city_query_variants, cn_municipality, normalize_cn_city


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Cheng Du Shi", "Chengdu"),
        ("cheng du", "Chengdu"),
        ("Cheng DU shi", "Chengdu"),
        ("Chengdu", "Chengdu"),
        ("CHENGDU", "Chengdu"),
        ("Xi An Shi", "Xian"),
        ("Xi'An", "Xian"),  # apostrophe dropped so both spellings match
        ("Guang Zhou Shi", "Guangzhou"),
        ("Huang Shi Shi", "Huangshi"),  # only the separate trailing "Shi" goes
        ("Huangshi", "Huangshi"),  # one word: "shi" is part of the name
        ("广州市", "广州"),
        ("Huang Pu Qu", "Huang Pu Qu"),  # district: left readable
        ("Pu Dong Xin Qu", "Pu Dong Xin Qu"),
        ("Xuhui District", "Xuhui District"),
        ("Da Li Bai Zu Zi Zhi Zhou", "Da Li Bai Zu Zi Zhi Zhou"),  # autonomous prefecture
        ("Guang Dong Sheng", "Guang Dong Sheng"),  # province
        ("Su Zhou", "Suzhou"),  # city without "Shi"
        ("Xishuangbanna Dai Autonomous Prefecture", "Xishuangbanna Dai Autonomous Prefecture"),
        ("Shi", "Shi"),
        (None, None),
        ("", ""),
    ],
)
def test_normalize_cn_city(raw, expected):
    assert normalize_cn_city(raw) == expected


def test_variants_with_same_key_collapse():
    names = ["Cheng Du Shi", "cheng du", "Cheng DU shi", "Chengdu"]
    assert {normalize_cn_city(n) for n in names} == {"Chengdu"}


def test_city_query_variants():
    assert city_query_variants("Cheng Du Shi") == ["Cheng Du Shi", "Chengdu"]
    assert city_query_variants("chengdu") == ["chengdu"]  # NOCASE already covers it
    assert city_query_variants("Huang Pu Qu") == ["Huang Pu Qu"]


@pytest.mark.parametrize(
    "admin1, expected",
    [
        ("Shang Hai Shi", "Shanghai"),
        ("Shanghai", "Shanghai"),
        ("上海市", "Shanghai"),
        ("Chong Qing Shi", "Chongqing"),
        ("Chongqing", "Chongqing"),
        ("Bei Jing Shi", "Beijing"),
        ("Tian Jin Shi", "Tianjin"),
        ("Guang Dong Sheng", None),  # a province, not a municipality
        ("Si Chuan Sheng", None),
        (None, None),
    ],
)
def test_cn_municipality(admin1, expected):
    assert cn_municipality(admin1) == expected
