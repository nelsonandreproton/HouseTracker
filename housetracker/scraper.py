import logging
from datetime import UTC, datetime

from . import db, filters, notifier
from .config import Config
from .sources import eleiloes, leilosoc, millennium, santander

try:
    from heartbeat import beat as _hb_beat  # mounted at /hetznercheck via PYTHONPATH
    _HEARTBEAT_AVAILABLE = True
except ImportError:
    _HEARTBEAT_AVAILABLE = False

log = logging.getLogger(__name__)

SOURCES = {
    "eleiloes": eleiloes.fetch,
    "leilosoc": leilosoc.fetch,
    "millennium": millennium.fetch,
    "santander": santander.fetch,
}
# Auction aggregators always have stock; 0 raw results means the source is broken.
# Banks may legitimately have nothing in the two districts.
ZERO_IS_BROKEN = {"eleiloes", "leilosoc"}


def _fetch_source(name: str, fetch, config: Config) -> tuple[int | None, list, str | None]:
    """Run one source; never raises. Returns (raw_total, listings, error)."""
    try:
        raw_total, listings = fetch(config)
    except Exception as exc:
        log.exception("Source %s failed", name)
        return None, [], f"{type(exc).__name__}: {exc}"[:300]
    if name in ZERO_IS_BROKEN and raw_total == 0:
        return 0, [], "source returned 0 listings"
    return raw_total, listings, None


def run_scrape(config: Config, sender: notifier.Notifier | None = None,
               sources: dict | None = None) -> dict:
    """One full scrape across all sources. Returns {source: ok}."""
    sender = sender or notifier.Notifier(config.telegram_bot_token, config.telegram_chat_id)
    sources = SOURCES if sources is None else sources
    now = datetime.now(UTC)
    results: dict[str, bool] = {}
    conn = db.connect(config.db_path)
    try:
        for name, fetch in sources.items():
            raw_total, listings, error = _fetch_source(name, fetch, config)
            ok = error is None
            results[name] = ok
            previous = db.get_status(conn, name)
            db.set_status(conn, name, ok, error, raw_total, now)
            was_ok = previous.ok if previous else True
            if was_ok and not ok:
                sender.send(f"HouseTracker ALERT: source {name} is broken\n{error}")
            elif ok and previous and not previous.ok:
                sender.send(f"HouseTracker: source {name} recovered")
            if not ok:
                continue  # never touch listings of a broken source
            db.upsert_listings(conn, filters.filter_listings(listings, config.max_price), now)
            db.deactivate_unseen(conn, name, now)
    finally:
        conn.close()
    return results


def run_scheduled(config: Config) -> None:
    """Scheduled scrape: never raises, reports a heartbeat afterwards."""
    try:
        results = run_scrape(config)
    except Exception:
        log.exception("Scheduled scrape crashed")
        results = {}
    if not _HEARTBEAT_AVAILABLE:
        return
    broken = [name for name, ok in results.items() if not ok]
    status = "ok" if results and not broken else "warn"
    note = f"broken: {', '.join(broken)}" if broken else f"{len(results)} sources ok"
    try:
        _hb_beat("HouseTracker", status=status, note=note,
                 next_in_seconds=int(config.scrape_interval_hours * 3600))
    except Exception:
        log.exception("Heartbeat failed")
