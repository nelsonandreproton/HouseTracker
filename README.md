# HouseTracker

Every `SCRAPE_INTERVAL_HOURS` hours (default 6) it scrapes Portuguese property auction and bank-sale sources, keeps apartments and moradias in the districts of Lisboa and Setúbal priced at or below `MAX_PRICE` (default 300 000 EUR), stores them in SQLite, serves a one-page list at `/` and sends Telegram messages for new listings. `/health` returns JSON for monitoring.

## Sources included

| Source | Type | Price used |
|---|---|---|
| e-leiloes.pt (JSON API, tipo=1 per district) | leilão / negociação particular | `valorMinimo` ("valor mínimo") |
| Leilosoc (`__NEXT_DATA__` of the imóveis category) | leilão / negociação particular | `valueOpen` ("valor de abertura"), else `valueBase` ("valor base") |
| Millennium bcp (janeladigital.com) | venda direta | listed price ("preço venda direta") |
| Santander (imoveis.santander.pt) | venda direta | listed price ("preço venda direta") |

Partial-ownership lots (fractions like 1/4, proporções, nua propriedade, usufruto, direito de superfície, meação, quotas, fração/parte ideal) are excluded by title.

## Sources excluded

- **Portal das Finanças**: requires an Autenticação.Gov login.
- **CGD**: WAF returns 403 to automated access.
- **Montepio**: no residential stock.
- **LC Premium**: lists whole auction events without per-lot price/type; currently 0 in Lisboa/Setúbal.

## Health and alerts

A source is "broken" if its fetch raised (HTTP/TLS errors, missing `__NEXT_DATA__`, missing JSON keys or HTML container) or, for e-leiloes and Leilosoc, returned 0 raw results (banks may legitimately return 0). A Telegram alert is sent only when a source flips ok -> broken, and a message when it recovers. Listings of a broken source are never deactivated. The first successful run seeds silently and sends one "started" summary; more than 10 new listings in a run are sent as one grouped message. Without `TELEGRAM_BOT_TOKEN` messages are only logged.

If `heartbeat` is importable (HetznerCheck mounted via `PYTHONPATH`), each scheduled scrape beats `HouseTracker` with `next_in_seconds`; the manual CLI never does.

## TLS note (e-leiloes)

e-leiloes.pt sends an incomplete certificate chain (leaf only). The missing intermediate "Sectigo Public Server Authentication CA DV R36" is committed in `certs/sectigo-dv-r36.pem` and appended to the certifi bundle (written to `<DB dir>/ca-bundle-eleiloes.pem`) for the e-leiloes session only. Verification is never disabled. The server's leaf certificate expires on 2026-12-20; if Sectigo changes the chain, update `certs/`.

## Run

```
cp .env.example .env        # fill in Telegram values
pip install -r requirements.txt
uvicorn housetracker.web:app --port 8080 --workers 1   # single worker: no duplicate scrapes
python -m housetracker.scrape                          # one manual scrape
```

Config (env / `.env`): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MAX_PRICE`, `SCRAPE_INTERVAL_HOURS`, `DB_PATH`.

## Test

```
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests mock all HTTP (`responses`) and never touch the network.

## Deploy

`docker compose up -d --build` (port 127.0.0.1:8080, data in the `housetracker_data` volume). On the Hetzner VPS put Caddy in front of it and add the service to the homeserver compose/deploy flow.
