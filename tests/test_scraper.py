import ssl

import pytest
import requests

from housetracker import db, scraper
from housetracker.notifier import Notifier


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


def _source(listings, raw=None):
    return lambda config: (len(listings) if raw is None else raw, listings)


def _failing(exc):
    def fetch(config):
        raise exc
    return fetch


def _run(config, sources, sender=None):
    sender = sender or FakeNotifier()
    results = scraper.run_scrape(config, sender, sources)
    return results, sender


def _active_ids(config, source):
    conn = db.connect(config.db_path)
    try:
        rows = conn.execute("SELECT external_id FROM listings WHERE source=? AND active=1", (source,))
        return sorted(r[0] for r in rows)
    finally:
        conn.close()


def test_run_scrape_first_run_seeds_silently_with_single_summary(config, make_listing):
    items = [make_listing(external_id=str(i)) for i in range(3)]
    _, sender = _run(config, {"eleiloes": _source(items)})
    assert sender.sent == ["HouseTracker started: 3 listings tracked"]


def test_run_scrape_second_run_sends_one_message_per_new_listing(config, make_listing):
    first = [make_listing(external_id="1")]
    _run(config, {"eleiloes": _source(first)})
    second = first + [make_listing(external_id="2"), make_listing(external_id="3")]
    _, sender = _run(config, {"eleiloes": _source(second)})
    assert len(sender.sent) == 2
    assert all(m.startswith("Novo imóvel:") and "https://example.test/A1" in m for m in sender.sent)
    assert "valor mínimo" in sender.sent[0] and "leilão" in sender.sent[0]


def test_run_scrape_more_than_ten_new_listings_sends_one_grouped_message(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing(external_id="seed")])})
    many = [make_listing(external_id=str(i)) for i in range(11)]
    _, sender = _run(config, {"eleiloes": _source(many)})
    assert len(sender.sent) == 1 and sender.sent[0].startswith("11 novos imóveis")


def test_run_scrape_exactly_ten_new_listings_are_sent_individually(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing(external_id="seed")])})
    _, sender = _run(config, {"eleiloes": _source([make_listing(external_id=str(i)) for i in range(10)])})
    assert len(sender.sent) == 10


def test_run_scrape_listing_above_max_price_is_not_stored(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing(price=999999.0)])})
    assert _active_ids(config, "eleiloes") == []


def test_run_scrape_source_ok_to_broken_alerts_once_then_stays_quiet(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing()])})
    broken = {"eleiloes": _failing(RuntimeError("site changed"))}
    results, sender = _run(config, broken)
    assert results == {"eleiloes": False}
    assert len(sender.sent) == 1 and "ALERT" in sender.sent[0] and "site changed" in sender.sent[0]
    _, sender = _run(config, broken)
    assert sender.sent == []


def test_run_scrape_source_broken_to_ok_sends_recovered_message(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing()])})
    _run(config, {"eleiloes": _failing(RuntimeError("x"))})
    _, sender = _run(config, {"eleiloes": _source([make_listing()])})
    assert sender.sent == ["HouseTracker: source eleiloes recovered"]


def test_run_scrape_healthy_runs_send_no_status_messages(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing()])})
    _, sender = _run(config, {"eleiloes": _source([make_listing()])})
    assert sender.sent == []


def test_run_scrape_broken_source_does_not_deactivate_its_listings(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing(external_id="1"), make_listing(external_id="2")])})
    _run(config, {"eleiloes": _failing(RuntimeError("x"))})
    assert _active_ids(config, "eleiloes") == ["1", "2"]


def test_run_scrape_zero_raw_total_on_eleiloes_is_broken_and_keeps_listings(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing()])})
    results, sender = _run(config, {"eleiloes": _source([], raw=0)})
    assert results == {"eleiloes": False} and "ALERT" in sender.sent[0]
    assert _active_ids(config, "eleiloes") == ["A1"]


def test_run_scrape_zero_raw_total_on_leilosoc_is_broken(config):
    results, _ = _run(config, {"leilosoc": _source([], raw=0)})
    assert results == {"leilosoc": False}


def test_run_scrape_zero_raw_total_on_bank_is_ok_and_deactivates(config, make_listing):
    bank = make_listing(source="santander", external_id="S1", sale_type="venda direta")
    _run(config, {"santander": _source([bank])})
    results, sender = _run(config, {"santander": _source([], raw=0)})
    assert results == {"santander": True} and sender.sent == []
    assert _active_ids(config, "santander") == []


def test_run_scrape_successful_source_deactivates_unseen_listings(config, make_listing):
    _run(config, {"eleiloes": _source([make_listing(external_id="1"), make_listing(external_id="2")])})
    _run(config, {"eleiloes": _source([make_listing(external_id="1")])})
    assert _active_ids(config, "eleiloes") == ["1"]


def test_run_scrape_ssl_error_is_treated_as_broken_not_crash(config, make_listing):
    sources = {
        "eleiloes": _failing(requests.exceptions.SSLError("unable to get local issuer certificate")),
        "santander": _source([make_listing(source="santander", external_id="S")]),
    }
    results, sender = _run(config, sources)
    assert results == {"eleiloes": False, "santander": True}
    assert any("SSLError" in m for m in sender.sent)
    assert _active_ids(config, "santander") == ["S"]


def test_run_scrape_failing_source_does_not_stop_other_sources(config, make_listing):
    sources = {"eleiloes": _failing(ValueError("boom")),
               "santander": _source([make_listing(source="santander", external_id="S")])}
    _run(config, sources)
    conn = db.connect(config.db_path)
    statuses = {s.source: s.ok for s in db.all_statuses(conn)}
    conn.close()
    assert statuses == {"eleiloes": False, "santander": True}


def test_run_scrape_late_seeded_source_is_silent_after_initial_summary(config, make_listing):
    _run(config, {"eleiloes": _failing(RuntimeError("x")),
                  "santander": _source([make_listing(source="santander", external_id="S")])})
    items = [make_listing(external_id=str(i)) for i in range(3)]
    _, sender = _run(config, {"eleiloes": _source(items),
                              "santander": _source([make_listing(source="santander", external_id="S")])})
    assert not any(m.startswith("Novo imóvel") for m in sender.sent)


def test_notifier_without_token_skips_send_gracefully():
    assert Notifier("", "").send("hello") is False


def test_run_scheduled_beats_heartbeat_with_interval(config, monkeypatch, make_listing):
    calls = []
    monkeypatch.setattr(scraper, "_HEARTBEAT_AVAILABLE", True)
    monkeypatch.setattr(scraper, "_hb_beat", lambda *a, **k: calls.append((a, k)), raising=False)
    monkeypatch.setattr(scraper, "SOURCES", {"eleiloes": _source([make_listing()])})
    scraper.run_scheduled(config)
    (args, kwargs), = calls
    assert args == ("HouseTracker",) and kwargs["status"] == "ok"
    assert kwargs["next_in_seconds"] == 6 * 3600


def test_run_scheduled_broken_source_beats_warn(config, monkeypatch):
    calls = []
    monkeypatch.setattr(scraper, "_HEARTBEAT_AVAILABLE", True)
    monkeypatch.setattr(scraper, "_hb_beat", lambda *a, **k: calls.append(k), raising=False)
    monkeypatch.setattr(scraper, "SOURCES", {"eleiloes": _failing(RuntimeError("x"))})
    scraper.run_scheduled(config)
    assert calls[0]["status"] == "warn" and "eleiloes" in calls[0]["note"]


def test_manual_scrape_cli_does_not_beat_heartbeat(config, monkeypatch, make_listing):
    from housetracker import scrape
    calls = []
    monkeypatch.setattr(scraper, "_HEARTBEAT_AVAILABLE", True)
    monkeypatch.setattr(scraper, "_hb_beat", lambda *a, **k: calls.append(k), raising=False)
    monkeypatch.setattr(scraper, "SOURCES", {"eleiloes": _source([make_listing()])})
    monkeypatch.setattr(scrape, "load_config", lambda: config)
    scrape.main()
    assert calls == []
