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
GROUP_THRESHOLD = 10


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
    new_listings = []
    seeded_any = False
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
            new = db.upsert_listings(conn, filters.filter_listings(listings, config.max_price), now)
            db.deactivate_unseen(conn, name, now)
            if db.get_meta(conn, f"seeded:{name}") is None:
                db.set_meta(conn, f"seeded:{name}", now.isoformat())
                seeded_any = True  # first run of this source: seed silently
            else:
                new_listings += new
        if seeded_any and db.get_meta(conn, "start_summary_sent") is None:
            db.set_meta(conn, "start_summary_sent", now.isoformat())
            sender.send(f"HouseTracker started: {db.count_active(conn)} listings tracked")
        _notify_new(sender, new_listings)
    finally:
        conn.close()
    return results


def _notify_new(sender: notifier.Notifier, new_listings: list) -> None:
    if not new_listings:
        return
    if len(new_listings) > GROUP_THRESHOLD:
        sender.send(notifier.format_grouped(new_listings))
        return
    for item in new_listings:
        sender.send(notifier.format_listing(item))


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
