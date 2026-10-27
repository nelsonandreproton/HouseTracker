"""Manual one-off scrape: python -m housetracker.scrape (no heartbeat)."""
import logging

from . import db
from .config import load_config, setup_logging
from .scraper import run_scrape


def main() -> None:
    setup_logging()
    config = load_config()
    db.init_db(config.db_path)
    results = run_scrape(config)
    for name, ok in results.items():
        logging.info("%s: %s", name, "ok" if ok else "BROKEN")


if __name__ == "__main__":
    main()
