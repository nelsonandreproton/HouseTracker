import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    max_price: float
    scrape_interval_hours: float
    db_path: str


def load_config() -> Config:
    load_dotenv()
    return Config(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        max_price=float(os.environ.get("MAX_PRICE", "300000")),
        scrape_interval_hours=float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6")),
        db_path=os.environ.get("DB_PATH", "data/housetracker.db"),
    )
