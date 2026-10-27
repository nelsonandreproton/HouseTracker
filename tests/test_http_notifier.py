import logging

import pytest
import requests
import responses

from housetracker import http
from housetracker.notifier import Notifier, format_grouped, format_listing, format_price

URL = "https://example.test/x"


@responses.activate
def test_get_server_error_is_retried_three_times():
    responses.add(responses.GET, URL, status=500)
    with pytest.raises(requests.HTTPError):
        http.get(http.new_session(), URL)
    assert len(responses.calls) == 3


@responses.activate
def test_get_rate_limit_429_is_never_retried():
    responses.add(responses.GET, URL, status=429)
    with pytest.raises(requests.HTTPError):
        http.get(http.new_session(), URL)
    assert len(responses.calls) == 1


@responses.activate
def test_get_transient_failure_then_success_returns_response():
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, body="ok")
    assert http.get(http.new_session(), URL).text == "ok"


def test_format_price_groups_thousands_with_spaces():
    assert format_price(185000.0) == "185 000 €" and format_price(None) == "?"


def test_format_listing_contains_key_fields(make_listing):
    text = format_listing(make_listing())
    for part in ("Apartamento T2 em Sintra", "Sintra, Lisboa", "T2", "150 000 €",
                 "valor mínimo", "leilão", "https://example.test/A1"):
        assert part in text


def test_format_grouped_is_truncated_to_telegram_limit(make_listing):
    items = [make_listing(external_id=str(i), url="https://example.test/" + "x" * 200) for i in range(60)]
    assert len(format_grouped(items)) <= 4000


@responses.activate
def test_notifier_posts_to_bot_api_with_chat_id():
    responses.add(responses.POST, "https://api.telegram.org/botTOKEN/sendMessage", json={"ok": True})
    assert Notifier("TOKEN", "42").send("hi") is True
    assert b'"chat_id": "42"' in responses.calls[0].request.body or b'"chat_id":"42"' in responses.calls[0].request.body


@responses.activate
def test_notifier_failure_returns_false_and_never_logs_token(caplog):
    responses.add(responses.POST, "https://api.telegram.org/botSECRET123/sendMessage", status=400)
    with caplog.at_level(logging.ERROR):
        assert Notifier("SECRET123", "42").send("hi") is False
    assert "SECRET123" not in caplog.text and "Telegram send failed" in caplog.text
