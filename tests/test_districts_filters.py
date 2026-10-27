import pytest

from housetracker.districts import canonical_district, district_for, normalize
from housetracker.filters import filter_listings, is_partial_ownership


@pytest.mark.parametrize("name,expected", [
    ("SETUBAL", "Setúbal"), ("Setúbal", "Setúbal"), ("lisboa", "Lisboa"),
    ("Porto", None), (None, None),
])
def test_canonical_district_accent_and_case_variants_normalize(name, expected):
    assert canonical_district(name) == expected


@pytest.mark.parametrize("concelho,expected", [
    ("Vila Franca de Xira", "Lisboa"), ("VILA  FRANCA DE XIRA", "Lisboa"),
    ("Lourinha", "Lisboa"), ("Santiago do Cacem", "Setúbal"),
    ("Alcácer do Sal", "Setúbal"), ("Viseu", None),
])
def test_district_for_concelho_maps_to_district(concelho, expected):
    assert district_for(concelho) == expected


def test_normalize_empty_value_returns_empty_string():
    assert normalize(None) == ""


def test_filter_listings_valid_house_under_max_price_is_kept(make_listing):
    assert len(filter_listings([make_listing()], 300000)) == 1


def test_filter_listings_price_equal_to_max_is_kept(make_listing):
    assert filter_listings([make_listing(price=300000.0)], 300000)


@pytest.mark.parametrize("price", [300000.01, 0, None, -5])
def test_filter_listings_bad_or_high_price_is_dropped(make_listing, price):
    assert filter_listings([make_listing(price=price)], 300000) == []


def test_filter_listings_other_district_is_dropped(make_listing):
    assert filter_listings([make_listing(district="Porto")], 300000) == []


def test_filter_listings_district_accent_variant_is_kept(make_listing):
    assert filter_listings([make_listing(district="SETUBAL")], 300000)


def test_filter_listings_non_house_kind_is_dropped(make_listing):
    assert filter_listings([make_listing(kind="loja")], 300000) == []


@pytest.mark.parametrize("title", [
    "1/4 de Casas de habitação", "Proporção de 23/24 de Edifício", "Nua propriedade de T2",
    "NUA RAIZ de moradia", "Direito de usufruto", "Direito de superfície sobre moradia",
    "Quota-parte indivisa", "Fração ideal de apartamento", "Parte ideal de moradia",
    "Direito à Meação sob Moradia", "Proporções à Torrão",
])
def test_is_partial_ownership_partial_titles_match(title):
    assert is_partial_ownership(title)


@pytest.mark.parametrize("title", [
    "Apartamento T2 - Alverca do Ribatejo", "Moradia T3 sito Quinta do Conde", "Fração A T1",
])
def test_is_partial_ownership_full_ownership_titles_do_not_match(title):
    assert not is_partial_ownership(title)


def test_filter_listings_partial_ownership_is_dropped(make_listing):
    assert filter_listings([make_listing(title="1/2 de T4 em Lisboa")], 300000) == []
