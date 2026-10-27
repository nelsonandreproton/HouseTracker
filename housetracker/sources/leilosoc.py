import json
import logging
import re
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from .. import http
from ..config import Config
from ..districts import district_for, log_unmatched, normalize
from ..models import Listing

log = logging.getLogger(__name__)

SOURCE = "leilosoc"
LIST_URL = "https://leilosoc.com/pt-PT/category/5-imoveis/"
LOT_URL = "https://leilosoc.com/pt-PT/lot/{auction}/{batch}-{slug}/"
KINDS = {1: "apartamento", 6: "moradia"}
SALE_TYPES = {"leilao_online": "leilão", "negociacao_particular": "negociação particular"}
MAX_PAGES = 50


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")


def extract_lots(html: str) -> dict:
    tag = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if tag is None or not tag.string:
        raise ValueError("leilosoc: __NEXT_DATA__ not found")
    try:
        lots = json.loads(tag.string)["props"]["pageProps"]["lots"]
        lots["items"], lots["totalCount"], lots["totalPages"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"leilosoc: unexpected __NEXT_DATA__ shape ({exc!r})") from exc
    return lots


def _typology(item: dict, kind: str) -> str:
    base = kind.capitalize()
    for key, value in (item.get("customFieldsValues") or {}).items():
        if key.endswith("_type") and isinstance(value, str) and re.fullmatch(r"t\d", value.lower()):
            code = value.upper()
            return f"{base} {code}" if kind == "moradia" else code
    match = re.search(r"\bT(\d)\b", item.get("title") or "")
    if match:
        return f"{base} T{match.group(1)}" if kind == "moradia" else f"T{match.group(1)}"
    return base


def parse_item(item: dict, now: datetime, unmatched: set[str]) -> Listing | None:
    kind = KINDS.get(item.get("batchSubCategoryId"))
    if kind is None:
        return None
    end_raw = item.get("auctionEndDate")
    end_date = datetime.fromisoformat(end_raw) if end_raw else None
    if end_date is not None and end_date <= now:
        return None
    concelho = item.get("addressLocation") or ""
    district = district_for(concelho)
    if district is None:
        unmatched.add(concelho)
        return None
    if item.get("valueOpen"):
        price, label = item["valueOpen"], "valor de abertura"
    else:
        price, label = item.get("valueBase"), "valor base"
    title = item.get("title") or str(item["batchId"])
    return Listing(
        source=SOURCE,
        external_id=str(item["batchId"]),
        title=title,
        district=district,
        municipality=concelho,
        parish=None,
        typology=_typology(item, kind),
        kind=kind,
        price=price,
        price_label=label,
        sale_type=SALE_TYPES.get(item.get("auctionTypeCode"), "leilão"),
        url=LOT_URL.format(auction=item["auctionId"], batch=item["batchId"], slug=slugify(title)),
        end_date=end_date,
    )


def _page(session, number: int) -> dict:
    response = http.get(session, LIST_URL, params={"slug": "5-imoveis", "page": number})
    response.encoding = "utf-8"
    return extract_lots(response.text)


def fetch(config: Config) -> tuple[int, list[Listing]]:
    session = http.new_session()
    now = datetime.now(UTC)
    unmatched: set[str] = set()
    first = _page(session, 1)
    raw_total = int(first["totalCount"])
    pages = [first]
    for number in range(2, min(int(first["totalPages"]), MAX_PAGES) + 1):
        pages.append(_page(session, number))
    listings = [p for page in pages for it in page["items"] if (p := parse_item(it, now, unmatched))]
    log_unmatched(unmatched, SOURCE)
    return raw_total, listings
