import logging

from . import http

log = logging.getLogger(__name__)

MAX_MESSAGE = 4000  # Telegram limit is 4096 characters


def format_price(price: float | None) -> str:
    return "?" if price is None else f"{price:,.0f}".replace(",", " ") + " €"


class Notifier:
    def __init__(self, token: str, chat_id: str):
        self._token = token
        self._chat_id = chat_id

    def send(self, text: str) -> bool:
        if not self._token or not self._chat_id:
            log.info("Telegram not configured, skipping message: %s", text.splitlines()[0])
            return False
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            http.request(http.new_session(), "POST", url,
                         json={"chat_id": self._chat_id, "text": text[:MAX_MESSAGE],
                               "disable_web_page_preview": True})
            return True
        except Exception as exc:  # never let a notification failure break a scrape
            # exception messages embed the URL, which contains the bot token
            log.error("Telegram send failed: %s", str(exc).replace(self._token, "<token>"))
            return False
