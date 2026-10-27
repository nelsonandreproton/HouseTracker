import logging
import unicodedata

log = logging.getLogger(__name__)

LISBOA_CONCELHOS = [
    "Alenquer", "Amadora", "Arruda dos Vinhos", "Azambuja", "Cadaval", "Cascais",
    "Lisboa", "Loures", "Lourinhã", "Mafra", "Odivelas", "Oeiras", "Sintra",
    "Sobral de Monte Agraço", "Torres Vedras", "Vila Franca de Xira",
]
SETUBAL_CONCELHOS = [
    "Alcácer do Sal", "Alcochete", "Almada", "Barreiro", "Grândola", "Moita",
    "Montijo", "Palmela", "Santiago do Cacém", "Seixal", "Sesimbra", "Setúbal", "Sines",
]


def normalize(text: str | None) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


_CONCELHO_TO_DISTRICT = {normalize(c): "Lisboa" for c in LISBOA_CONCELHOS}
_CONCELHO_TO_DISTRICT.update({normalize(c): "Setúbal" for c in SETUBAL_CONCELHOS})
_DISTRICTS = {normalize("Lisboa"): "Lisboa", normalize("Setúbal"): "Setúbal"}


def district_for(concelho: str | None) -> str | None:
    return _CONCELHO_TO_DISTRICT.get(normalize(concelho))


def canonical_district(name: str | None) -> str | None:
    """'SETUBAL' -> 'Setúbal'; None if the district is not one we track."""
    return _DISTRICTS.get(normalize(name))


def log_unmatched(concelhos: set[str], source: str) -> None:
    if concelhos:
        log.info("%s: %d concelhos outside Lisboa/Setúbal ignored: %s",
                 source, len(concelhos), ", ".join(sorted(concelhos)[:20]))
