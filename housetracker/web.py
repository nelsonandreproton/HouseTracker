import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import db
from .config import Config, load_config
from .notifier import format_price
from .scraper import run_scheduled

log = logging.getLogger(__name__)

LISBON = ZoneInfo("Europe/Lisbon")
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DISTRICTS = ("Lisboa", "Setúbal")
KINDS = ("apartamento", "moradia")
SALE_TYPES = ("leilão", "negociação particular", "venda direta")
NEW_BADGE_HOURS = 48
STARTUP_DELAY_SECONDS = 30


def build_scheduler(config: Config) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_scheduled, "interval", args=[config], hours=config.scrape_interval_hours,
        next_run_time=datetime.now(UTC) + timedelta(seconds=STARTUP_DELAY_SECONDS),
        max_instances=1, coalesce=True, id="scrape",
    )
    return scheduler


def _local(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    return value.astimezone(LISBON).strftime(fmt) if value else ""


def _allowed(value: str | None, allowed: tuple[str, ...]) -> str | None:
    return value if value in allowed else None


def create_app(config: Config | None = None, start_scheduler: bool = True) -> FastAPI:
    config = config or load_config()
    db.init_db(config.db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler = build_scheduler(config) if start_scheduler else None
        if scheduler:
            scheduler.start()
        yield
        if scheduler:
            scheduler.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))  # autoescape on for .html
    templates.env.filters["price"] = format_price
    templates.env.filters["local"] = _local

    @app.get("/health")
    def health() -> JSONResponse:
        conn = db.connect(config.db_path)
        try:
            sources = {
                s.source: {"ok": s.ok, "last_run": s.last_run.isoformat(),
                           "last_raw_total": s.last_raw_total, "last_error": s.last_error}
                for s in db.all_statuses(conn)
            }
            return JSONResponse({"status": "ok", "listings": db.count_active(conn),
                                 "sources": sources})
        finally:
            conn.close()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, district: str | None = None, kind: str | None = None,
              sale_type: str | None = None):
        district = _allowed(district, DISTRICTS)
        kind = _allowed(kind, KINDS)
        sale_type = _allowed(sale_type, SALE_TYPES)
        now = datetime.now(UTC)
        conn = db.connect(config.db_path)
        try:
            listings = db.list_active(conn, now, district, kind, sale_type)
            statuses = db.all_statuses(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(request, "index.html", {
            "listings": listings, "statuses": statuses, "now": now,
            "new_since": now - timedelta(hours=NEW_BADGE_HOURS),
            "district": district, "kind": kind, "sale_type": sale_type,
            "districts": DISTRICTS, "kinds": KINDS, "sale_types": SALE_TYPES,
        })

    return app


app = create_app()
