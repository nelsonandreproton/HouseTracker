import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from housetracker import db
from housetracker.web import build_scheduler, create_app


@pytest.fixture
def client(config):
    return TestClient(create_app(config, start_scheduler=False))


def _seed(config, listings, now=None):
    conn = db.connect(config.db_path)
    db.upsert_listings(conn, listings, now or datetime.now(UTC))
    db.set_status(conn, "eleiloes", True, None, 10, datetime.now(UTC))
    db.set_status(conn, "leilosoc", False, "boom", None, datetime.now(UTC))
    conn.close()


def test_health_returns_200_with_counts_and_sources(client, config, make_listing):
    _seed(config, [make_listing()])
    response = client.get("/health")
    body = response.json()
    assert response.status_code == 200 and body["status"] == "ok" and body["listings"] == 1
    assert body["sources"]["eleiloes"]["ok"] is True and body["sources"]["leilosoc"]["ok"] is False


def test_index_empty_database_renders_empty_message(client):
    response = client.get("/")
    assert response.status_code == 200 and "Sem imóveis" in response.text


def test_index_lists_listing_fields_sorted_by_price(client, config, make_listing):
    _seed(config, [make_listing(external_id="1", price=185000.0, title="Cara"),
                   make_listing(external_id="2", price=90000.0, municipality="Almada",
                                district="Setúbal", url="https://example.test/cheap")])
    html = client.get("/").text
    assert html.index("90 000 €") < html.index("185 000 €")
    assert "valor mínimo" in html and "Ver anúncio" in html
    assert 'href="https://example.test/cheap" target="_blank" rel="noopener noreferrer"' in html
    assert "Algueirão" in html and "novo" in html


def test_index_title_html_is_escaped(client, config, make_listing):
    _seed(config, [make_listing(municipality="<script>alert(1)</script>",
                                url='https://example.test/"><img src=x>')])
    html = client.get("/").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '"><img src=x>' not in html


def test_index_hides_ended_and_old_listings_get_no_new_badge(client, config, make_listing):
    old = datetime.now(UTC) - timedelta(days=5)
    _seed(config, [make_listing(external_id="old", municipality="Velha")], now=old)
    conn = db.connect(config.db_path)
    db.upsert_listings(conn, [make_listing(external_id="gone", municipality="Terminada",
                                           end_date=datetime.now(UTC) - timedelta(hours=1))], old)
    conn.close()
    html = client.get("/").text
    assert "Velha" in html and "Terminada" not in html
    assert 'class="badge"' not in html


def test_index_shows_broken_source_warning(client, config, make_listing):
    _seed(config, [make_listing()])
    assert "com problemas" in client.get("/").text


def test_index_filters_by_query_params_and_ignores_unknown_values(client, config, make_listing):
    _seed(config, [make_listing(external_id="1", municipality="Sintra"),
                   make_listing(external_id="2", municipality="Almada", district="Setúbal")])
    assert "Almada" not in client.get("/?district=Lisboa").text
    assert "Sintra" not in client.get("/?district=Set%C3%BAbal").text
    both = client.get("/?district=Porto&kind=castelo&sale_type=x'; DROP").text
    assert "Sintra" in both and "Almada" in both


def test_build_scheduler_single_instance_interval_job(config):
    scheduler = build_scheduler(config)
    (job,) = scheduler.get_jobs()
    assert job.max_instances == 1
    assert job.trigger.interval == timedelta(hours=6)
    assert job.next_run_time > datetime.now(UTC)
    assert job.args == (config,)


def test_create_app_under_uvicorn_logs_app_info_to_stderr(config):
    # Subprocess: pytest attaches its own root handlers, which would hide a missing setup.
    code = (
        "import logging\n"
        "from housetracker.config import Config\n"
        "from housetracker.web import create_app\n"
        f"create_app(Config(db_path={config.db_path!r}, telegram_bot_token='', telegram_chat_id='',"
        " max_price=300000, scrape_interval_hours=6), start_scheduler=False)\n"
        "logging.getLogger('housetracker.scraper').info('scraper-line')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "DB_PATH": config.db_path},
                            timeout=60)
    assert "scraper-line" in result.stderr, result.stderr


def test_index_lists_unscraped_sources_as_external_links(client):
    html = client.get("/").text
    for url in ("https://vendas.portaldasfinancas.gov.pt/vendasat", "https://www.caixaimobiliario.pt/",
                "https://www.imoveismontepio.pt/", "https://www.lcpremium.pt/pt/electronic-auctions"):
        assert f'href="{url}"' in html
