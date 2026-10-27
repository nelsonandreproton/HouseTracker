import logging
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .. import http
from ..config import Config
from ..models import Listing

log = logging.getLogger(__name__)

SOURCE = "millennium"
BASE = "https://millenniumimoveis.janeladigital.com"
SEARCH_URL = BASE + "/Search.aspx"
DISTRICTS = {"Lisboa": 11, "Setúbal": 15}
KINDS = {1: "apartamento", 2: "moradia"}


def _field(card, label: str) -> str | None:
    for strong in card.select("div.propertyDataColumn strong"):
        if strong.get_text(strip=True) == label:
            return strong.parent.get_text(" ", strip=True).split(":", 1)[-1].strip() or None
    return None


def parse_cards(html: bytes | str, district: str, kind: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    if not (soup.select_one("div.searchResultHeader") or soup.select_one("div.searchResultPaginator")):
        raise ValueError("millennium: results container not found")
    if len(soup.select("div.searchResultPaginator a[href]")) > 0:
        log.warning("millennium: multiple result pages detected, only page 1 is read")
    listings = []
    for card in soup.select("div.searchResultProperty"):
        link = card.select_one('a[id*="aContent"]')
        if link is None:
            raise ValueError("millennium: card without detail link")
        uid = parse_qs(urlparse(link["href"]).query).get("UID", [None])[0]
        price_text = card.select_one("span.propertyPrice")
        digits = re.sub(r"\D", "", price_text.get_text() if price_text else "")
        nature = " ".join((card.select_one("span.propertyNature") or card).get_text().split())
        match = re.search(r"\bT(\d)\b", nature)
        code = f"T{match.group(1)}" if match else None
        if kind == "moradia":
            typology = f"Moradia {code}" if code else "Moradia"
        else:
            typology = code or "Apartamento"
        title = " ".join(link.get("title", "").split()).removeprefix("Ver ") or nature
        listings.append(Listing(
            source=SOURCE,
            external_id=uid,
            title=title,
            district=district,
            municipality=_field(card, "Concelho") or "",
            parish=_field(card, "Freguesia"),
            typology=typology,
            kind=kind,
            price=float(digits) if digits else None,
            price_label="preço venda direta",
            sale_type="venda direta",
            url=f"{BASE}/Detail.aspx?UID={uid}",
        ))
    return listings


def fetch(config: Config) -> tuple[int, list[Listing]]:
    session = http.new_session()
    listings: list[Listing] = []
    for district, code in DISTRICTS.items():
        for mca, kind in KINDS.items():
            response = http.get(session, SEARCH_URL, params={
                "buf": 1, "sa": code, "mca": mca, "gp": int(config.max_price)})
            listings += parse_cards(response.content, district, kind)
    return len(listings), listings
