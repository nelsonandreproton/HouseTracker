import logging
import re

from bs4 import BeautifulSoup

from .. import http
from ..config import Config
from ..districts import normalize
from ..models import Listing

log = logging.getLogger(__name__)

SOURCE = "santander"
BASE = "https://imoveis.santander.pt"
SEARCH_URL = BASE + "/imoveis/1/0/-1/-1/-1/{region}/-1/-1/-1/{max_price}/-1/-1/-/-1/-1/"
REGIONS = {"Lisboa": 69, "Setúbal": 73}
KINDS = {"apartamento": "apartamento", "moradia": "moradia"}


def parse_cards(html: bytes | str, district: str) -> tuple[int, list[Listing]]:
    """Return (number of cards on the page, apartment/moradia listings)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.prop")
    if not cards and not soup.select_one("#print-results, .print-results-container"):
        raise ValueError("santander: results container not found")
    listings = []
    for card in cards:
        tag = card.select_one("div.prop-tag")
        type_text = tag.get_text(" ", strip=True).split("|")[0].strip() if tag else ""
        kind = KINDS.get(normalize(type_text))
        if kind is None:
            continue
        link = card.select_one("a[href*='/detalhe/']")
        match = re.search(r"/detalhe/(\d+)", link["href"] if link else "")
        if not match:
            raise ValueError("santander: card without /detalhe/ link")
        price_tag = card.select_one(".prop-tag-sub")
        digits = re.sub(r"\D", "", price_tag.get_text()) if price_tag else ""
        names = [s.get_text(strip=True) for s in card.select(".prop-title span")]
        concelho = names[0] if names else ""
        parish = names[1] if len(names) > 1 else None
        code = re.search(r"\bT(\d)\b", card.get_text(" "))
        if kind == "moradia":
            typology = f"Moradia T{code.group(1)}" if code else "Moradia"
        else:
            typology = f"T{code.group(1)}" if code else "Apartamento"
        ref = re.search(r"Ref\S*\s*(\d+)", tag.get_text(" ")) if tag else None
        title = f"{type_text}, {concelho}" + (f" - Ref {ref.group(1)}" if ref else "")
        listings.append(Listing(
            source=SOURCE,
            external_id=match.group(1),
            title=title,
            district=district,
            municipality=concelho,
            parish=parish,
            typology=typology,
            kind=kind,
            price=float(digits) if digits else None,
            price_label="preço venda direta",
            sale_type="venda direta",
            url=BASE + link["href"],
        ))
    return len(cards), listings


def fetch(config: Config) -> tuple[int, list[Listing]]:
    session = http.new_session()
    raw_total = 0
    listings: list[Listing] = []
    for district, region in REGIONS.items():
        url = SEARCH_URL.format(region=region, max_price=int(config.max_price))
        count, found = parse_cards(http.get(session, url).content, district)
        raw_total += count
        listings += found
    return raw_total, listings
