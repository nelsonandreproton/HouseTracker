import json
import os
import tempfile
from pathlib import Path

import pytest
import requests

FIXTURES = Path(__file__).parent / "fixtures"

# Importing housetracker.web builds the module-level app; keep its DB out of the repo.
_import_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_import_db.close()
os.environ["DB_PATH"] = _import_db.name
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""

from housetracker import db  # noqa: E402
from housetracker.config import Config  # noqa: E402
from housetracker.models import Listing  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network_no_sleep(monkeypatch):
    """Tests never touch the network (`responses` re-patches this when active) or sleep."""
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a test")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked)
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda seconds: None)


@pytest.fixture
def config():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.init_db(tmp.name)
    yield Config("", "", 300000.0, 6.0, tmp.name)
    os.unlink(tmp.name)


@pytest.fixture
def make_listing():
    def factory(**overrides) -> Listing:
        values = dict(
            source="eleiloes", external_id="A1", title="Apartamento T2 em Sintra",
            district="Lisboa", municipality="Sintra", parish="Algueirão", typology="T2",
            kind="apartamento", price=150000.0, price_label="valor mínimo",
            sale_type="leilão", url="https://example.test/A1", end_date=None,
        )
        values.update(overrides)
        return Listing(**values)
    return factory


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
