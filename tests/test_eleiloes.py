import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from housetracker.sources import eleiloes

from conftest import load_json

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
FAR_FUTURE = "2099-01-01T10:00:00"


def _items(district: str, end: str | None = FAR_FUTURE) -> list[dict]:
    items = [i for i in load_json("eleiloes_list74.json") if i["moradaDistrito"] == district]
    for item in items:
        if end:
            item["dataFim"] = end
    return items


def _serve(by_district: dict[str, list[dict]], seen: list | None = None):
    def callback(request):
        params = json.loads(parse_qs(urlparse(request.url).query)["tableParams"][0])
        if seen is not None:
            seen.append((request.headers.get("Accept"), params))
        district = params["filters"]["distrito"]["value"]
        items = by_district[district]
        first = params["first"]
        body = {"list": items[first:first + 12],
                "pagination": {"first": first, "rows": 12, "total": len(items)}}
        return (200, {}, json.dumps(body))
    responses.add_callback(responses.GET, eleiloes.API_URL, callback=callback)


def test_parse_item_fixture_apartment_maps_all_fields():
    item = next(i for i in _items("Lisboa") if i["referencia"] and i["subtipoId"] == 1)
    listing = eleiloes.parse_item(item, "Lisboa", NOW)
    assert listing.kind == "apartamento" and listing.source == "eleiloes"
    assert listing.price == item["valorMinimo"] and listing.price_label == "valor mínimo"
    assert listing.url == f"https://www.e-leiloes.pt/evento/{item['referencia']}"
    assert listing.district == "Lisboa" and listing.municipality == item["moradaConcelho"]
    assert listing.sale_type in {"leilão", "negociação particular"}


def test_parse_item_moradia_typology_uses_tipologia_id():
    item = {"referencia": "X1", "subtipoId": 2, "tipologiaId": 5, "titulo": "Moradia",
            "moradaDistrito": "Setúbal", "moradaConcelho": "Almada", "valorMinimo": 1.0,
            "modalidadeId": 1, "dataFim": FAR_FUTURE}
    assert eleiloes.parse_item(item, "Setúbal", NOW).typology == "Moradia T3"
    assert eleiloes.parse_item({**item, "tipologiaId": 1}, "Setúbal", NOW).typology == "Moradia"
    assert eleiloes.parse_item({**item, "subtipoId": 1}, "Setúbal", NOW).typology == "T3"


def test_parse_item_other_subtype_is_skipped():
    assert eleiloes.parse_item({"referencia": "X", "subtipoId": 27}, "Lisboa", NOW) is None


@pytest.mark.parametrize("flag", ["cancelado", "terminado"])
def test_parse_item_cancelled_or_finished_is_skipped(flag):
    item = {"referencia": "X", "subtipoId": 1, flag: True, "dataFim": FAR_FUTURE}
    assert eleiloes.parse_item(item, "Lisboa", NOW) is None


def test_parse_item_ended_date_is_skipped():
    item = {"referencia": "X", "subtipoId": 1, "dataFim": "2026-10-27T12:30:00"}
    # 12:30 Lisbon (WET/WEST, UTC+0 after DST ends Oct 25 2026) == 12:30 UTC, after NOW
    assert eleiloes.parse_item(item, "Lisboa", NOW) is not None
    assert eleiloes.parse_item({**item, "dataFim": "2026-10-27T11:59:00"}, "Lisboa", NOW) is None


def test_parse_end_date_naive_value_is_interpreted_as_lisbon_time():
    summer = eleiloes.parse_end_date("2026-07-01T12:00:00")
    winter = eleiloes.parse_end_date("2026-01-01T12:00:00")
    assert summer.utcoffset().total_seconds() == 3600  # WEST
    assert winter.utcoffset().total_seconds() == 0
    assert summer.astimezone(UTC).hour == 11
    assert eleiloes.parse_end_date(None) is None and eleiloes.parse_end_date("junk") is None


@responses.activate
def test_fetch_paginates_both_districts_and_sums_raw_total(config):
    lisboa, setubal = _items("Lisboa"), _items("Setúbal")
    assert len(lisboa) > 12  # forces a second page
    seen: list = []
    _serve({"Lisboa": lisboa, "Setúbal": setubal}, seen)
    raw_total, listings = eleiloes.fetch(config)
    assert raw_total == len(lisboa) + len(setubal)
    lisboa_pages = [p["first"] for _, p in seen if p["filters"]["distrito"]["value"] == "Lisboa"]
    assert lisboa_pages == list(range(0, len(lisboa), 12))
    assert all(accept == "application/json" for accept, _ in seen)
    assert all(p["filters"]["tipo"] == {"value": 1, "matchMode": "equals"} for _, p in seen)
    assert listings and {x.kind for x in listings} <= {"apartamento", "moradia"}
    assert {x.district for x in listings} == {"Lisboa", "Setúbal"}


@responses.activate
def test_fetch_uses_combined_ca_bundle_not_verify_false(config, monkeypatch):
    seen = {}
    original = requests.Session.send

    def spy(self, request, **kwargs):
        seen["verify"] = kwargs.get("verify", self.verify)
        return original(self, request, **kwargs)

    monkeypatch.setattr(requests.Session, "send", spy)
    _serve({"Lisboa": _items("Lisboa")[:3], "Setúbal": _items("Setúbal")[:3]})
    eleiloes.fetch(config)
    bundle = seen["verify"]
    assert isinstance(bundle, str) and bundle.endswith("ca-bundle-eleiloes.pem")
    text = open(bundle, encoding="ascii").read()
    assert text.count("BEGIN CERTIFICATE") > 10
    assert eleiloes.INTERMEDIATE_PEM.read_text(encoding="ascii").strip() in text


@responses.activate
def test_fetch_missing_json_keys_raises_value_error(config):
    responses.add(responses.GET, eleiloes.API_URL, json={"unexpected": 1})
    with pytest.raises(ValueError):
        eleiloes.fetch(config)


@responses.activate
def test_fetch_ssl_error_propagates_without_retry(config):
    responses.add(responses.GET, eleiloes.API_URL, body=requests.exceptions.SSLError("bad chain"))
    with pytest.raises(requests.exceptions.SSLError):
        eleiloes.fetch(config)
    assert len(responses.calls) == 1
