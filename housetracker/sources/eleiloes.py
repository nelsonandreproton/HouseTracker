import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import certifi

from .. import http
from ..config import Config
from ..districts import canonical_district
from ..models import Listing

log = logging.getLogger(__name__)

SOURCE = "eleiloes"
API_URL = "https://www.e-leiloes.pt/api/Eventos/"
EVENT_URL = "https://www.e-leiloes.pt/evento/{}"
DISTRICTS = ("Lisboa", "Setúbal")
PAGE_SIZE = 12
LISBON = ZoneInfo("Europe/Lisbon")
INTERMEDIATE_PEM = Path(__file__).resolve().parents[2] / "certs" / "sectigo-dv-r36.pem"

KINDS = {1: "apartamento", 2: "moradia"}
TYPOLOGY = {2: "T0", 3: "T1", 4: "T2", 5: "T3", 6: "T4", 7: "T5", 8: "T6+"}
SALE_TYPES = {1: "leilão", 2: "negociação particular"}


def ca_bundle(config: Config) -> str:
    """certifi roots + the Sectigo intermediate the server fails to send (this session only)."""
    parent = Path(config.db_path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        parent = Path(tempfile.gettempdir())
    target = parent / "ca-bundle-eleiloes.pem"
    content = (Path(certifi.where()).read_text(encoding="ascii")
               + "\n" + INTERMEDIATE_PEM.read_text(encoding="ascii"))
    if not target.exists() or target.read_text(encoding="ascii") != content:
        target.write_text(content, encoding="ascii")
    return str(target)


def parse_end_date(value: str | None) -> datetime | None:
    """dataFim is local Lisbon time without tz info."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=LISBON)
    except ValueError:
        return None


def parse_item(item: dict, fallback_district: str, now: datetime) -> Listing | None:
    kind = KINDS.get(item.get("subtipoId"))
    if kind is None or item.get("cancelado") or item.get("terminado"):
        return None
    end_date = parse_end_date(item.get("dataFim"))
    if end_date is not None and end_date <= now:
        return None
    typology_code = TYPOLOGY.get(item.get("tipologiaId"))
    if kind == "moradia":
        typology = f"Moradia {typology_code}" if typology_code else "Moradia"
    else:
        typology = typology_code or "Apartamento"
    reference = item["referencia"]
    return Listing(
        source=SOURCE,
        external_id=reference,
        title=item.get("titulo") or reference,
        district=canonical_district(item.get("moradaDistrito")) or fallback_district,
        municipality=item.get("moradaConcelho") or "",
        parish=item.get("moradaFreguesia") or None,
        typology=typology,
        kind=kind,
        price=item.get("valorMinimo"),
        price_label="valor mínimo",
        sale_type=SALE_TYPES.get(item.get("modalidadeId"), "leilão"),
        url=EVENT_URL.format(reference),
        end_date=end_date,
    )


def _table_params(district: str, first: int) -> str:
    return json.dumps({
        "first": first, "rows": PAGE_SIZE, "sortField": "dataFim", "sortOrder": 1,
        "filters": {"tipo": {"value": 1, "matchMode": "equals"},
                    "distrito": {"value": district, "matchMode": "equals"}},
    }, separators=(",", ":"), ensure_ascii=False)


def fetch(config: Config) -> tuple[int, list[Listing]]:
    session = http.new_session()
    session.verify = ca_bundle(config)
    now = datetime.now(UTC)
    raw_total = 0
    listings: list[Listing] = []
    for district in DISTRICTS:
        first = 0
        while True:
            response = http.get(session, API_URL, headers={"Accept": "application/json"},
                                params={"tableParams": _table_params(district, first)})
            data = response.json()
            try:
                items = data["list"]
                total = int(data["pagination"]["total"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"e-leiloes: unexpected response shape ({exc!r})") from exc
            if first == 0:
                raw_total += total
            listings += [p for it in items if (p := parse_item(it, district, now))]
            first += PAGE_SIZE
            if first >= total or not items:
                break
    return raw_total, listings
