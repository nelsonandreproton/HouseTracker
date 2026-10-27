from dataclasses import replace
from datetime import UTC, datetime, timedelta

from housetracker import db

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_upsert_listings_new_listing_is_returned_and_stored(config, make_listing):
    conn = db.connect(config.db_path)
    new = db.upsert_listings(conn, [make_listing()], T0)
    stored = db.get_listing(conn, "eleiloes", "A1")
    conn.close()
    assert len(new) == 1
    assert stored.first_seen == T0 and stored.last_seen == T0 and stored.active


def test_upsert_listings_existing_keeps_first_seen_updates_last_seen_and_price(config, make_listing):
    conn = db.connect(config.db_path)
    db.upsert_listings(conn, [make_listing()], T0)
    later = T0 + timedelta(hours=6)
    new = db.upsert_listings(conn, [make_listing(price=140000.0)], later)
    stored = db.get_listing(conn, "eleiloes", "A1")
    conn.close()
    assert new == []
    assert stored.first_seen == T0 and stored.last_seen == later and stored.price == 140000.0


def test_deactivate_unseen_only_affects_unseen_listings_of_that_source(config, make_listing):
    conn = db.connect(config.db_path)
    db.upsert_listings(conn, [make_listing(external_id="A1"), make_listing(external_id="A2"),
                              make_listing(source="santander", external_id="S1")], T0)
    later = T0 + timedelta(hours=6)
    db.upsert_listings(conn, [make_listing(external_id="A1")], later)
    assert db.deactivate_unseen(conn, "eleiloes", later) == 1
    assert db.get_listing(conn, "eleiloes", "A1").active
    assert not db.get_listing(conn, "eleiloes", "A2").active
    assert db.get_listing(conn, "santander", "S1").active
    conn.close()


def test_upsert_listings_deactivated_listing_seen_again_is_reactivated_not_new(config, make_listing):
    conn = db.connect(config.db_path)
    db.upsert_listings(conn, [make_listing()], T0)
    db.deactivate_unseen(conn, "eleiloes", T0 + timedelta(hours=1))
    new = db.upsert_listings(conn, [make_listing()], T0 + timedelta(hours=2))
    assert new == [] and db.get_listing(conn, "eleiloes", "A1").active
    conn.close()


def test_list_active_sorted_by_price_and_hides_ended(config, make_listing):
    conn = db.connect(config.db_path)
    now = datetime.now(UTC)
    db.upsert_listings(conn, [
        make_listing(external_id="B", price=200000.0),
        make_listing(external_id="C", price=100000.0),
        make_listing(external_id="D", price=50000.0, end_date=now - timedelta(hours=1)),
        make_listing(external_id="E", price=120000.0, end_date=now + timedelta(days=1)),
    ], now)
    ids = [x.external_id for x in db.list_active(conn, now)]
    conn.close()
    assert ids == ["C", "E", "B"]


def test_list_active_filters_by_district_kind_and_sale_type(config, make_listing):
    conn = db.connect(config.db_path)
    now = datetime.now(UTC)
    db.upsert_listings(conn, [
        make_listing(external_id="1"),
        make_listing(external_id="2", district="Setúbal"),
        make_listing(external_id="3", kind="moradia"),
        make_listing(external_id="4", sale_type="venda direta"),
    ], now)
    assert [x.external_id for x in db.list_active(conn, now, district="Setúbal")] == ["2"]
    assert [x.external_id for x in db.list_active(conn, now, kind="moradia")] == ["3"]
    assert [x.external_id for x in db.list_active(conn, now, sale_type="venda direta")] == ["4"]
    conn.close()


def test_upsert_listings_sql_injection_in_title_is_stored_literally(config, make_listing):
    conn = db.connect(config.db_path)
    evil = "x'); DROP TABLE listings;--"
    db.upsert_listings(conn, [make_listing(title=evil)], T0)
    assert db.get_listing(conn, "eleiloes", "A1").title == evil
    conn.close()


def test_status_and_meta_roundtrip(config):
    conn = db.connect(config.db_path)
    assert db.get_status(conn, "x") is None
    db.set_status(conn, "x", False, "boom", None, T0)
    status = db.get_status(conn, "x")
    assert (status.ok, status.last_error, status.last_run) == (False, "boom", T0)
    db.set_meta(conn, "k", "v")
    db.set_meta(conn, "k", "v2")
    assert db.get_meta(conn, "k") == "v2" and db.get_meta(conn, "nope") is None
    conn.close()


def test_listing_dataclass_is_frozen(make_listing):
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_listing().price = 1  # type: ignore[misc]
    assert replace(make_listing(), price=1.0).price == 1.0
