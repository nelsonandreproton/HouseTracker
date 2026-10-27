import logging
import re

from .districts import canonical_district, normalize
from .models import Listing

log = logging.getLogger(__name__)

KINDS = {"apartamento", "moradia"}

# Partial-ownership lots, matched on accent/case-normalized titles: fractions such as
# "1/4", "proporção/proporções", bare rights (nua propriedade/raiz, usufruto, direito de superfície, meação),
# quotas and "fração/parte ideal".
PARTIAL_OWNERSHIP_RE = re.compile(
    r"\b\d+\s*/\s*\d+\b"
    r"|\bproporc(?:ao|oes)\b|nua propriedade|nua raiz|usufruto|direito de superficie|meacao"
    r"|quota|fracao ideal|parte ideal"
)


def is_partial_ownership(title: str) -> bool:
    return bool(PARTIAL_OWNERSHIP_RE.search(normalize(title)))


def filter_listings(listings: list[Listing], max_price: float) -> list[Listing]:
    kept = []
    partial = 0
    for item in listings:
        if canonical_district(item.district) is None or item.kind not in KINDS:
            continue
        if item.price is None or not 0 < item.price <= max_price:
            continue
        if is_partial_ownership(item.title):
            partial += 1
            continue
        kept.append(item)
    if partial:
        log.info("Excluded %d partial-ownership listings", partial)
    return kept
