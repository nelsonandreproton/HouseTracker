import copy
import json
import re
from datetime import UTC, datetime

import pytest
import responses

from housetracker.sources import leilosoc

from conftest import read_fixture

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def _lots() -> dict:
    return leilosoc.extract_lots(read_fixture("leilosoc.html").decode("utf-8"))


def _moradia(**overrides) -> dict:
    item = copy.deepcopy(next(i for i in _lots()["items"] if i["batchSubCategoryId"] == 6))
    item.update(addressLocation="Sintra", auctionEndDate="2099-01-01T10:00:00.000Z",
                valueOpen=None, valueBase=120000, title="Moradia T3 em Sintra")
    item.update(overrides)
    return item


def _page_html(lots: dict) -> str:
    data = {"props": {"pageProps": {"lots": lots}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_extract_lots_fixture_has_counts_and_items():
    lots = _lots()
    assert lots["totalCount"] == 63 and lots["totalPages"] == 6 and len(lots["items"]) == 12


def test_extract_lots_without_next_data_raises():
    with pytest.raises(ValueError, match="__NEXT_DATA__"):
        leilosoc.extract_lots("<html></html>")


def test_extract_lots_missing_keys_raises():
    with pytest.raises(ValueError):
        leilosoc.extract_lots('<script id="__NEXT_DATA__">{"props": {}}</script>')


def test_parse_item_fixture_page_keeps_only_region_houses():
    unmatched: set[str] = set()
    parsed = [p for i in _lots()["items"] if (p := leilosoc.parse_item(i, NOW, unmatched))]
    assert [(p.municipality, p.district, p.kind, p.price) for p in parsed] == [
        ("Alcácer do Sal", "Setúbal", "moradia", 73000)]
    assert "Oliveira do Bairro" not in unmatched  # only house kinds are reported
    assert "Viseu" in unmatched


def test_parse_item_moradia_uses_base_value_when_open_value_is_null():
    listing = leilosoc.parse_item(_moradia(), NOW, set())
    assert listing.price == 120000 and listing.price_label == "valor base"
    assert listing.district == "Lisboa" and listing.municipality == "Sintra"
    assert listing.kind == "moradia" and listing.typology == "Moradia T3"
    assert listing.sale_type == "leilão" and listing.end_date.tzinfo is not None


def test_parse_item_open_value_wins_over_base_value():
    listing = leilosoc.parse_item(_moradia(valueOpen=90000), NOW, set())
    assert listing.price == 90000 and listing.price_label == "valor de abertura"


def test_parse_item_url_is_built_from_ids_and_slugified_title():
    item = _moradia(title="Moradia à Ericeira – Mafra", auctionId=11, batchId=22, addressLocation="Mafra")
    listing = leilosoc.parse_item(item, NOW, set())
    assert listing.url == "https://leilosoc.com/pt-PT/lot/11/22-moradia-a-ericeira-mafra/"
    assert listing.external_id == "22"


def test_parse_item_home_type_custom_field_sets_typology():
    item = _moradia(title="Moradia", customFieldsValues={"home_type": "t5"})
    assert leilosoc.parse_item(item, NOW, set()).typology == "Moradia T5"


def test_parse_item_private_negotiation_sale_type():
    listing = leilosoc.parse_item(_moradia(auctionTypeCode="negociacao_particular"), NOW, set())
    assert listing.sale_type == "negociação particular"


def test_parse_item_apartment_and_other_category():
    assert leilosoc.parse_item(_moradia(batchSubCategoryId=1, title="Apartamento T2"), NOW, set()).typology == "T2"
    assert leilosoc.parse_item(_moradia(batchSubCategoryId=10), NOW, set()) is None


def test_parse_item_ended_auction_is_skipped():
    assert leilosoc.parse_item(_moradia(auctionEndDate="2020-01-01T10:00:00.000Z"), NOW, set()) is None


def test_slugify_strips_accents_and_punctuation():
    assert leilosoc.slugify("Moradia – Ação, Nº 5!") == "moradia-acao-no-5"


@responses.activate
def test_fetch_loops_all_pages_and_reports_total_count(config):
    pages = []

    def callback(request):
        number = int(re.search(r"page=(\d+)", request.url).group(1))
        pages.append(number)
        lots = {"items": [_moradia(batchId=number * 100, addressLocation="Sintra")],
                "totalCount": 3, "totalPages": 3}
        return (200, {}, _page_html(lots))

    responses.add_callback(responses.GET, "https://leilosoc.com/pt-PT/category/5-imoveis/",
                           callback=callback)
    raw_total, listings = leilosoc.fetch(config)
    assert raw_total == 3 and pages == [1, 2, 3] and len(listings) == 3


@responses.activate
def test_fetch_page_without_next_data_raises(config):
    responses.add(responses.GET, "https://leilosoc.com/pt-PT/category/5-imoveis/", body="<html/>")
    with pytest.raises(ValueError):
        leilosoc.fetch(config)
