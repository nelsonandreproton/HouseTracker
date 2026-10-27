import re

import pytest
import responses

from housetracker.sources import millennium, santander

from conftest import read_fixture


# --- Millennium -----------------------------------------------------------

def test_millennium_parse_cards_fixture_card_fields():
    (card,) = millennium.parse_cards(read_fixture("millennium.html"), "Lisboa", "apartamento")
    assert card.external_id == "5d380c1d-7501-11f1-9c13-060000000051"
    assert card.url == ("https://millenniumimoveis.janeladigital.com/Detail.aspx"
                        "?UID=5d380c1d-7501-11f1-9c13-060000000051")
    assert card.title == "Apartamento T3, Casal de Cambra - 119126"
    assert (card.typology, card.price, card.kind) == ("T3", 232000.0, "apartamento")
    assert (card.municipality, card.parish, card.district) == ("Sintra", "Casal de Cambra", "Lisboa")
    assert (card.sale_type, card.price_label) == ("venda direta", "preço venda direta")


def test_millennium_parse_cards_moradia_typology_prefixed():
    (card,) = millennium.parse_cards(read_fixture("millennium.html"), "Setúbal", "moradia")
    assert card.typology == "Moradia T3" and card.district == "Setúbal"


def test_millennium_parse_cards_missing_container_raises():
    with pytest.raises(ValueError, match="container"):
        millennium.parse_cards(b"<html><body>blocked</body></html>", "Lisboa", "apartamento")


def test_millennium_parse_cards_empty_results_page_is_not_an_error():
    html = b'<div class="searchResultHeader">0 resultados</div>'
    assert millennium.parse_cards(html, "Lisboa", "moradia") == []


@responses.activate
def test_millennium_fetch_queries_each_district_and_kind_with_max_price(config):
    responses.add(responses.GET, millennium.SEARCH_URL, body=read_fixture("millennium.html"))
    raw_total, listings = millennium.fetch(config)
    queries = sorted((c.request.params["sa"], c.request.params["mca"], c.request.params["gp"])
                     for c in responses.calls)
    assert queries == [("11", "1", "300000"), ("11", "2", "300000"),
                       ("15", "1", "300000"), ("15", "2", "300000")]
    assert raw_total == 4 and len(listings) == 4


@responses.activate
def test_millennium_fetch_zero_cards_returns_zero_raw_total(config):
    responses.add(responses.GET, millennium.SEARCH_URL,
                  body=b'<div class="searchResultHeader">0</div>')
    assert millennium.fetch(config) == (0, [])


# --- Santander ------------------------------------------------------------

def _with_type(name: str, fixture: str = "santander_r69.html") -> bytes:
    html = read_fixture(fixture).decode("utf-8")
    html = re.sub(r"Loja / com(?:é|&#233;|&eacute;)rcio \|", f"{name} |", html)
    return html.replace("Garagem |", f"{name} |").encode("utf-8")


def test_santander_parse_cards_fixture_has_no_houses_but_counts_cards():
    count, listings = santander.parse_cards(read_fixture("santander_r69.html"), "Lisboa")
    assert count == 2 and listings == []


def test_santander_parse_cards_apartment_fields():
    count, listings = santander.parse_cards(_with_type("Apartamento"), "Lisboa")
    first = listings[0]
    assert count == 2 and len(listings) == 2
    assert first.external_id == "198756" and first.price == 215000.0
    assert first.url.startswith("https://imoveis.santander.pt/detalhe/198756")
    assert (first.kind, first.typology, first.municipality, first.parish) == (
        "apartamento", "Apartamento", "Lisboa", "Benfica")
    assert (first.sale_type, first.price_label) == ("venda direta", "preço venda direta")
    assert "27587" in first.title


def test_santander_parse_cards_moradia_kind():
    _, listings = santander.parse_cards(_with_type("Moradia", "santander_r73.html"), "Setúbal")
    assert {x.kind for x in listings} == {"moradia"} and listings[0].typology == "Moradia"
    assert listings[0].district == "Setúbal" and listings[0].municipality == "Setúbal"


def test_santander_parse_cards_missing_container_raises():
    with pytest.raises(ValueError, match="container"):
        santander.parse_cards(b"<html>Access denied</html>", "Lisboa")


@responses.activate
def test_santander_fetch_uses_region_and_max_price_in_path(config):
    responses.add(responses.GET, url=re.compile(r"https://imoveis\.santander\.pt/imoveis/.*"),
                  body=read_fixture("santander_r69.html"))
    raw_total, listings = santander.fetch(config)
    urls = [c.request.url for c in responses.calls]
    assert urls == [
        "https://imoveis.santander.pt/imoveis/1/0/-1/-1/-1/69/-1/-1/-1/300000/-1/-1/-/-1/-1/",
        "https://imoveis.santander.pt/imoveis/1/0/-1/-1/-1/73/-1/-1/-1/300000/-1/-1/-/-1/-1/",
    ]
    assert raw_total == 4 and listings == []
