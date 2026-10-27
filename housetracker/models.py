from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Listing:
    source: str
    external_id: str
    title: str
    district: str
    municipality: str
    parish: str | None
    typology: str
    kind: str  # "apartamento" | "moradia"
    price: float | None
    price_label: str  # what the price means: "valor mínimo", "valor base", ...
    sale_type: str  # "leilão" | "negociação particular" | "venda direta"
    url: str
    end_date: datetime | None = None  # timezone-aware
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    active: bool = True
