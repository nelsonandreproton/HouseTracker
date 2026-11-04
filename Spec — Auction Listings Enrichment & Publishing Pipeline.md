# Spec — Auction Listings Enrichment & Publishing Pipeline

Nov 4, 2026 · @Nelson André

## 1. Context and goals

Extend the existing auction scraper (e-leilões + bank property sites → website + Telegram alerts, deployed on Hetzner) into a pipeline that enriches each listing with verified facts and cost analysis, scores it, and queues ready-to-post drafts for human approval.

The scraper is a commodity; the product is the analysis. Every design choice in this spec protects two things: the existing alerts keep working, and no unverified claim reaches a public post.

**Goals (v1)**

- Enrich every new listing with structured facts, each carrying its source and a confidence level.
- Compute the true acquisition cost and a discount versus a local €/m² baseline, with deterministic code.
- Score listings and send only the worthwhile ones to Telegram as draft posts with Approve / Reject / Edit actions.
- Give each approved listing its own page on the existing website, which the X post links to.
- Record every human decision so scoring can be tuned from real data.

**Non-goals (v1)**

- No automated posting to X. The human posts manually from the approved draft.
- No paid product, user accounts, payments or newsletter yet. The page design only must not block adding them later.
- No new scraping sources. The four existing sources are enough for v1; new ones (Leilon, BidLeiloeira, Portal das Finanças) are later.
- No investigator / editor agents yet (see Phase 4).

**Language:** code, comments and this spec in English. All user-facing content (posts, pages, Telegram drafts) in European Portuguese (pt-PT).

## 2. Starting point: HouseTracker as it is today

The repo is [nelsonandreproton/HouseTracker](https://github.com/nelsonandreproton/HouseTracker). Read it fully before writing code; this spec matches its conventions and extends them, it does not replace them.

| Area | Today | Implication for this work |
| --- | --- | --- |
| Stack | Python, FastAPI + Jinja2, APScheduler `BackgroundScheduler` inside one uvicorn worker, `requests` + `tenacity`, SQLite, Docker Compose behind Caddy on Hetzner | Stay in this stack. No new services, queues or databases in v1. |
| Sources | `eleiloes` (JSON API), `leilosoc` (`__NEXT_DATA__`), `millennium`, `santander` | Banks are `venda direta`, not auctions; posts must say so. |
| Model | `Listing` dataclass, table `listings`, PK `(source, external_id)` | Enrichment goes in new tables keyed by the same PK; `listings` stays untouched. |
| Data kept | No area (m²), no description, no `valorBase`, no `lanceAtual`, no photo, no raw payload | Enrichment needs a per-listing detail fetch (section 5). |
| Filters | Lisboa + Setúbal, apartamento/moradia, price ≤ `MAX_PRICE` (300 000 €), partial-ownership titles excluded | This is a personal search config. The publishing scope is a separate decision (section 14). |
| Notify | `Notifier.send` posts text only; no inbound handling; first run per source seeds silently; >10 new → one grouped message | Approval buttons need inbound callbacks (section 8). Existing alerts must keep working. |
| Health | `source_status`, ok→broken alerts, `/health`, optional HetznerCheck heartbeat | Add enrichment health to the same mechanisms. |
| Tests | pytest, all HTTP mocked with `responses`, fixtures in `tests/fixtures` | Every new module gets tests in the same style; LLM calls mocked too. |

Before coding, write `docs/ENRICHMENT_PLAN.md` with: the exact files you will add or change, the e-leilões detail endpoint(s) you found and a saved fixture of their response, and any place where this spec conflicts with the code. Stop for approval.

**Hard constraints from the existing code:** keep a single uvicorn worker; never let enrichment, LLM or Telegram failures break or delay a scrape; never deactivate or modify `listings` rows from the new code; keep the e-leilões TLS handling (`certs/`, custom CA bundle) for any new e-leilões request.

## 3. Target architecture

Enrichment is a second, independent APScheduler job that drains a queue the scrape fills. The scrape job only gains one line: enqueue the new listings it already computes. Each stage is idempotent, writes its own table, and can be re-run for one listing from the CLI.

1. **Scrape** (existing, every `SCRAPE_INTERVAL_HOURS`). After `upsert_listings`, insert each new listing into `enrichment_jobs` with status `pending`. Seed runs enqueue nothing; backfill is a CLI command.
2. **Fetch detail** (new job, every `ENRICH_INTERVAL_MINUTES`, default 15, max `ENRICH_BATCH` listings per run, default 10). Fetch the source's detail data and store it raw in `listing_raw`.
3. **Extract** (LLM). Turn raw detail into `listing_facts`: one row per fact, with value, source excerpt and confidence.
4. **Compute** (plain Python). Total acquisition cost, €/m², discount versus the local baseline. Writes `listing_analysis`.
5. **Score** (plain Python, rules in config). Score 0–100 plus the reasons. Writes to `listing_analysis`.
6. **Draft**. If score ≥ `PUBLISH_MIN_SCORE`, render a pt-PT post draft from a template plus an LLM-written two-sentence summary, and send it to the review chat with buttons.
7. **Review** (Telegram webhook). Approve / Reject (with reason) / Edit. Approve publishes the listing page and returns copy-ready text for X.

Job status moves `pending → fetched → extracted → analysed → drafted | skipped | failed`. A stage that fails retries with backoff up to `ENRICH_MAX_ATTEMPTS` (default 3), then marks `failed` with the error and stops; failed jobs never block the queue.

**Existing alerts stay.** The current "Novo imóvel" messages keep going to `TELEGRAM_CHAT_ID`. Drafts go to a new `TELEGRAM_REVIEW_CHAT_ID` (can be the same chat). Add `RAW_ALERTS_ENABLED` (default `true`) so the old alerts can be switched off later without code changes.

**SQLite concurrency:** two scheduler jobs and the web app now write. Enable WAL mode in `connect()` and keep transactions short.

## 4. Data model

New tables (below, plus \`page\_views\` in section 9), keyed where relevant by `(source, external_id)` like `listings`, created in `db.SCHEMA` with `CREATE TABLE IF NOT EXISTS` so existing databases upgrade in place. Add a `schema_version` key to `meta` and a migration function for future changes. Store times as UTC ISO strings, as the existing code does.

```sql
CREATE TABLE IF NOT EXISTS enrichment_jobs (
    source TEXT NOT NULL, external_id TEXT NOT NULL,
    status TEXT NOT NULL,            -- pending|fetched|extracted|analysed|drafted|skipped|failed
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT, next_attempt_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);
CREATE TABLE IF NOT EXISTS listing_raw (
    source TEXT NOT NULL, external_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content_type TEXT NOT NULL,      -- json|html|text
    payload TEXT NOT NULL,           -- verbatim detail response
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY (source, external_id, fetched_at)
);
CREATE TABLE IF NOT EXISTS listing_facts (
    source TEXT NOT NULL, external_id TEXT NOT NULL,
    field TEXT NOT NULL,             -- see section 5
    value TEXT,                      -- NULL = unknown
    evidence TEXT,                   -- verbatim excerpt from listing_raw, ≤ 300 chars
    confidence TEXT NOT NULL,        -- source_field|extracted|unknown
    model TEXT, extracted_at TEXT NOT NULL,
    PRIMARY KEY (source, external_id, field)
);
CREATE TABLE IF NOT EXISTS listing_analysis (
    source TEXT NOT NULL, external_id TEXT NOT NULL,
    total_cost REAL, cost_breakdown TEXT,       -- JSON
    area_m2 REAL, price_per_m2 REAL,
    baseline_per_m2 REAL, baseline_ref TEXT,    -- which baseline row was used
    discount_pct REAL,
    score INTEGER, score_reasons TEXT,          -- JSON list
    summary_pt TEXT, analysed_at TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);
CREATE TABLE IF NOT EXISTS review_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, external_id TEXT NOT NULL,
    decision TEXT NOT NULL,          -- approved|rejected|edited
    reason TEXT,                     -- reject reason code
    edited_text TEXT,
    score_at_decision INTEGER,
    telegram_message_id INTEGER,
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, external_id TEXT, stage TEXT NOT NULL,
    model TEXT NOT NULL, ok INTEGER NOT NULL,
    latency_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER,
    error TEXT, called_at TEXT NOT NULL
);
```

Also extend the e-leilões parser to keep fields it already receives and discards: `valorBase`, `lanceAtual`, `capa` and the numeric `id`. Store them in `listing_raw` as part of the list payload rather than adding columns to `listings`.

The baseline table (section 6) is loaded from a file, not scraped:

```sql
CREATE TABLE IF NOT EXISTS price_baseline (
    level TEXT NOT NULL,             -- parish|municipality
    district TEXT NOT NULL, municipality TEXT NOT NULL, parish TEXT,
    median_per_m2 REAL NOT NULL,
    period TEXT NOT NULL,            -- e.g. 2026Q2
    source_url TEXT NOT NULL,
    PRIMARY KEY (level, district, municipality, parish, period)
);
```

## 5. Detail fetch and LLM extraction

The LLM reads; it never decides, calculates or invents. Every extracted value must point to verbatim evidence in the stored raw payload, and code checks that before anything is saved.

**Detail fetch, per source** (new `fetch_detail(listing) -> RawDetail` in each `sources/*.py`):

- `eleiloes`: find the event detail endpoint the website itself calls (inspect the network requests on an `/evento/{referencia}` page). Reuse `ca_bundle()` and the existing session/retry helpers. Save a real response as a test fixture.
- `leilosoc`: the lot's detail page `__NEXT_DATA__`.
- `millennium`, `santander`: the property detail page HTML, reduced to the main content block before storing.
- Attached documents (PDF notices, editais): store their URLs in the raw payload only. Parsing PDFs is Phase 4.
- Respect the sources: at most one detail request per 2 seconds per source, identify with the same User-Agent the scraper uses, and only for listings that passed the existing filters.

**Fields to extract** (one `listing_facts` row each; `value` is `NULL` when not stated):

| Field | Type | Notes |
| --- | --- | --- |
| `area_m2` | number | Prefer área bruta privativa; record which area in `evidence`. |
| `area_type` | enum | `bruta_privativa` \| `util` \| `bruta` \| `terreno` \| `other` |
| `typology` | string | Only to cross-check the scraped value. |
| `floor` | string |  |
| `year_built` | integer |  |
| `occupancy` | enum | `devoluto` \| `ocupado` \| `arrendado` \| `unknown`. Never infer from silence. |
| `condition` | enum | `bom` \| `obras` \| `ruina` \| `unknown` |
| `encumbrances` | text | Any mention of ónus, hipoteca, penhora, servidão, dívidas de condomínio, verbatim. |
| `deposit_pct` | number | Caução required to bid. |
| `payment_terms` | text | Deadlines for paying the price. |
| `visits` | text | Whether and how visits are possible. |
| `case_reference` | string | Process number, court, agente de execução / administrador. |
| `energy_cert` | string |  |

**Extraction contract:**

1. Input: the raw payload (trimmed to the relevant text, ≤ \~12k tokens) and the field list. Never the scraped price or title, to avoid anchoring.
2. Output: JSON matching a Pydantic model (Pydantic ships with FastAPI). Each field returns `{value, evidence}` or `{value: null, evidence: null}`.
3. Validation in code, before saving: the JSON parses; enums are valid; `evidence` is a verbatim substring of the payload (after whitespace normalisation); numbers are in plausible ranges (area 15–1 000 m², deposit 0–30%). A field that fails is saved as unknown with the failure reason logged; the rest of the record is kept.
4. Fields the source gives as structured data (e.g. a JSON `area` key) are taken directly with `confidence = source_field` and skip the LLM.
5. Temperature 0. Prompt text lives in `housetracker/prompts/` as versioned files; the version is stored with each fact via the `model` column (`model@prompt_v`).

**Summary (`summary_pt`):** a second, separate call that receives only the validated facts and the computed analysis, never the raw page. Two sentences, pt-PT, factual, no adjectives like "oportunidade", "imperdível" or "garantido". Code rejects summaries containing numbers that are not present in the facts/analysis, or any word on a banned list in config.

## 6. Deterministic calculations

All money and tax maths is plain, unit-tested Python in `housetracker/analysis.py`. No rate, bracket or fee is hardcoded in code: they live in versioned config files with a `valid_from` date and the official source URL, and the human verifies each value before first use.

**Price basis.** Auctions publish a starting value, not a final price. Calculate at the listed price (`valor mínimo`, `valor de abertura` or `preço venda direta`, as `price_label` already says), or at `lanceAtual` when it is higher, and always label the result "a partir de".

**Acquisition cost** (`cost_breakdown` JSON, every line with its basis):

- IMT: from `config/imt_<year>.yaml`, the brackets in the Código do IMT for urban dwellings, computed for the "habitação secundária / investimento" table by default, with the "habitação própria e permanente" value shown alongside. Mainland rates only; flag listings in the Autonomous Regions as unsupported.
- Imposto do Selo on the acquisition, rate from config.
- Registration and deed: one configurable estimate, shown as an estimate.
- Buyer's commission: per source in `config/sources.yaml`; `null` means unknown and is displayed as unknown, never as zero.
- Works: not estimated. If `condition` is `obras` or `ruina`, say so in words.

Acceptance test: the human supplies at least five worked examples from the Portal das Finanças IMT simulator; tests must reproduce them to the cent.

**Baseline €/m².** Use INE's quarterly median price per m² of dwellings sold, by parish and municipality, loaded with a CLI command (`python -m housetracker.baseline load <csv>`) from a CSV the human downloads. Lookup order: parish, then municipality, then none. Store which row was used in `baseline_ref`, and show the period and "INE" next to every comparison. If the baseline is older than 12 months, mark it stale on the page.

**Discount.** `discount_pct = 1 − price_per_m2 / baseline_per_m2`, only when `area_m2` is known with confidence `source_field` or `extracted` and the area type is not `terreno`. Otherwise `NULL`. Never compare a moradia's price against a land area.

## 7. Scoring and filtering

Scoring is a transparent, hand-tuned rule set in `config/scoring.yaml`, not a model. Its first job is to keep the review queue short; it gets better only from recorded review decisions.

**Hard gates** (job → `skipped`, reason stored, nothing sent):

- Bidding or sale ends in less than `MIN_HOURS_LEFT` (default 72 h).
- Price per m² can't be computed and the listing is not a `venda direta`.
- Validation failed on more than half of the extracted fields.

**Points** (0–100, every rule adds a line to `score_reasons` in pt-PT):

| Rule | Initial points |
| --- | --- |
| Discount vs baseline ≥ 40% / 25–40% / 10–25% / < 10% | 40 / 30 / 15 / 0 |
| Facts known (area, occupancy, condition, encumbrances) | 5 each, max 20 |
| Occupancy `devoluto` / `unknown` / `ocupado` or `arrendado` | +10 / 0 / −15 |
| Condition `bom` / `unknown` / `obras` / `ruina` | +10 / 0 / −5 / −15 |
| Encumbrance text present | −10 |
| 3–21 days left to bid | +10 |
| Judicial or insolvency sale (vs venda direta) | +10 |

`PUBLISH_MIN_SCORE` starts at 60. Every value above is config, not code.

**Feedback loop:** a CLI command `python -m housetracker.report` prints, for the last 30 days, approvals and rejections per score band and per reject reason. The human adjusts `scoring.yaml` from that; the system never changes its own weights.

## 8. Telegram approval workflow

Use a Telegram webhook on the existing FastAPI app, not long polling: Caddy already terminates HTTPS, and a polling thread would compete with the scheduler inside the single worker.

**Inbound endpoint.** `POST /telegram/webhook/{TELEGRAM_WEBHOOK_PATH_SECRET}`. Register it with `setWebhook` including `secret_token`, and reject any request whose `X-Telegram-Bot-Api-Secret-Token` header doesn't match. Accept updates only from `TELEGRAM_REVIEW_CHAT_ID` and from user ids in `TELEGRAM_ALLOWED_USER_IDS`. Answer `answerCallbackQuery` immediately, then do the work. A CLI command registers and inspects the webhook.

**Draft message** (sent by a new `Notifier.send_draft()` that supports `reply_markup` and returns the `message_id`):

```text
[score 72] T2 · Almada, Setúbal · leilão (e-leilões)
A partir de 98 500 € (valor mínimo) · custo total est. 106 900 €
78 m² (área bruta privativa) · 1 263 €/m² · −31% vs mediana INE freguesia 2026T2
Devoluto · obras: desconhecido · ónus: não mencionados
Termina 14/11 14:30

Razões: desconto 25–40% (+30); devoluto (+10); 10 dias (+10) ...

--- RASCUNHO PARA X ---
<post text>

Fonte: <official listing URL>
```

The example values above are illustrative only. Inline buttons: **Aprovar**, **Rejeitar**, **Editar**.

**Actions:**

- *Aprovar*: publish the listing page (section 9), record the decision, remove the buttons, and reply with the final post text in a monospace block for one-tap copy.
- *Rejeitar*: replace the buttons with reason buttons (`dados errados`, `desconto fraco`, `ocupado`, `localização`, `repetido`, `outro`), then record the decision with that reason.
- *Editar*: reply with a `force_reply` prompt; the human's reply becomes `edited_text`, is re-validated against the post rules, and then behaves like Aprovar.

Callback data must fit Telegram's 64-byte limit: use `{action}:{enrichment_jobs.rowid}`. Handlers are idempotent: a second tap on a decided draft just answers "já decidido". Drafts whose deadline has passed get their buttons removed by the enrichment job.

**Post text rules** (enforced in code, in `housetracker/posts.py`): pt-PT; ≤ 280 characters counting the link as 23; must contain the typology, municipality, sale type, "a partir de" price and the page link; discount only when computed, always with "vs mediana INE"; no words from the banned list; no claims about encumbrances or occupancy beyond the validated facts.

## 9. Website: one page per approved listing

The X post links to a page on this site, not to the auction site, so interest can be measured and a paid product can be added later. Only approved listings get a public page; the existing `/` list is unchanged.

**Route:** `GET /l/{source}/{external_id}`, rendered from a new `templates/listing.html`. Returns 404 unless the listing has an `approved` or `edited` decision. Slug-friendly URLs are optional; the key must stay stable.

**Page contents, in this order:**

1. Title line: typology, municipality, parish, sale type, source name.
2. Price block: "a partir de" price with its label, total estimated cost, and the cost breakdown table with each line's basis.
3. Comparison: €/m², baseline value with level (freguesia/concelho), period and "INE"; or "sem dados de área" when unknown.
4. Facts table: field, value, confidence ("indicado na fonte" / "extraído do anúncio" / "desconhecido"), and the evidence excerpt behind a disclosure.
5. Deadline and status. When the end date passes or the listing goes inactive, show "Terminado" at the top and keep the page.
6. A prominent button to the official listing, plus the case reference.
7. Disclaimer (section 12) and the analysis timestamp.

**Not in v1:** rehosted photos (link only), user accounts, email capture, payments. Keep the template structured so an email form can be added under the price block later.

**Measuring interest without cookies:** a `page_views` table (`day`, `path`, `count`), incremented server-side, excluding obvious bots by User-Agent. The `report` CLI shows views per approved listing next to its score.

**Meta tags:** `<title>`, description and OpenGraph text tags so X shows a sensible card. No OpenGraph image in v1.

## 10. LLM provider layer

All model calls go through one module, `housetracker/llm.py`, speaking the OpenAI-compatible chat completions API over the existing `requests` session helpers. Nothing else in the code knows which provider or model answered.

**Interface:** `complete_json(stage: str, messages: list, schema: type[BaseModel], listing_key: tuple | None) -> LLMResult` returning the parsed object plus model, latency and token counts, and writing one `llm_calls` row per attempt.

**Providers as an ordered list** in `config/llm.yaml`, each with `base_url`, an env var name for the key, a list of model ids and a daily request budget:

1. OpenRouter free models (`:free` ids), sent with OpenRouter's `models` fallback array in one request. Budget default 800/day, under the 1 000/day free cap that applies once the account has bought ≥ $10 of credits.
2. A cheap paid model (OpenRouter paid id or a direct provider key) as the last resort, with its own small daily budget.
3. Later: a local OpenAI-compatible server (llama.cpp or vLLM) on the human's own GPU, added as a new first entry with no code change.

Model ids are config only; the human chooses them. Do not hardcode any model name in code or tests. NVIDIA NIM's free endpoints are excluded: their terms allow trial use only, not production.

**Failure handling:** 429, 5xx, timeout or invalid JSON → try the next model/provider. If every provider is exhausted or over budget, leave the job `pending` with `next_attempt_at` set to the next budget reset and send one Telegram alert (ok→broken, recovered), reusing the existing source-status pattern under a pseudo-source `llm`.

**Golden set (regression guard):** `tests/golden/` holds 20 real listings (raw payload + human-verified expected facts), chosen across all four sources. `python -m housetracker.golden` runs extraction with the current provider config and prints per-field accuracy and any evidence-check failures. Run it before every change to `llm.yaml` or the prompts; CI tests mock the LLM, the golden run is manual.

**Not in v1:** the Claude Agent SDK. It returns in Phase 4 for open-ended investigation, behind the same budget and logging rules.

## 11. Deployment and operations

Deployment stays `docker compose up -d --build` on the Hetzner VPS behind Caddy. The only infrastructure change is exposing the webhook path through Caddy, which already serves the site over HTTPS.

**New configuration** (add to `Config`, `.env.example` and the README):

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENRICH_ENABLED` | `false` | Master switch; off means the app behaves exactly as today. |
| `ENRICH_INTERVAL_MINUTES` | `15` | Enrichment job interval. |
| `ENRICH_BATCH` | `10` | Listings per run. |
| `ENRICH_MAX_ATTEMPTS` | `3` | Retries before `failed`. |
| `PUBLISH_MIN_SCORE` | `60` | Threshold to send a draft. |
| `MIN_HOURS_LEFT` | `72` | Hard gate on deadline. |
| `RAW_ALERTS_ENABLED` | `true` | Keep today's "Novo imóvel" messages. |
| `TELEGRAM_REVIEW_CHAT_ID` | = `TELEGRAM_CHAT_ID` | Where drafts go. |
| `TELEGRAM_ALLOWED_USER_IDS` | empty (= reject all) | Who may press buttons. |
| `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_PATH_SECRET` | none | Webhook verification. |
| `PUBLIC_BASE_URL` | none | Used to build listing page links. |
| `OPENROUTER_API_KEY` (+ any provider keys named in `llm.yaml`) | none | LLM access. |

**CLI commands** (`python -m housetracker.<cmd>`, matching the existing `scrape` entry point): `enrich --one SOURCE EXTERNAL_ID [--stage ...]`, `enrich --backfill [--limit N]`, `baseline load <csv>`, `golden`, `report [--days 30]`, `webhook set|info|delete`.

**Observability:**

- `/health` adds `enrichment`: counts per job status, oldest pending age, LLM status, drafts sent and decided in the last 24 h.
- If `heartbeat` is importable, the enrichment job beats as `HouseTracker-enrich`, exactly like the scrape job does today.
- Telegram alerts only on state flips (ok→broken, recovered), reusing the existing pattern: LLM unavailable, more than 20 jobs `failed` in 24 h, oldest pending job older than 24 h.
- Never log the bot token or API keys; extend the existing redaction in `Notifier` to cover new secrets.

**Backups:** the database now holds human decisions that can't be re-scraped. Add a daily job writing an SQLite online backup (`conn.backup()`) into the data volume, keeping the last 14.

**Tests:** same style as today (pytest, `responses`, fixtures, no network). Required: each detail parser against saved fixtures; evidence-substring validation; every analysis function against the IMT worked examples; scoring rules; post text rules; webhook auth, idempotency and every button path; the scrape job still passing all existing tests with `ENRICH_ENABLED` on and off.

## 12. Guardrails

People may bid six-figure sums based on these pages, so the rules below are requirements, enforced in code where possible, not style advice.

**Truthfulness**

- No fact is shown unless it passed validation in section 5. Unknown is displayed as "desconhecido", never omitted in a way that implies "none" (for example, missing encumbrance text is "ónus: não mencionados no anúncio", not "sem ónus").
- Every comparison names its baseline and period; every cost says "estimativa".
- Nothing is published without a human approval recorded in `review_decisions`. There is no auto-approve path, not even behind a flag.

**Disclaimer** (on every listing page, and in the X account bio, set by the human):

> Informação compilada automaticamente a partir de fontes públicas e revista manualmente. Não constitui aconselhamento financeiro, jurídico ou fiscal. Confirme todos os dados no anúncio oficial e junto de um profissional antes de licitar.

**Personal data (GDPR).** Judicial sale notices can name the debtor (executado) or other private individuals. Never store names of private individuals in `listing_facts`, summaries, posts or pages: `case_reference` keeps only the process number, court and the agente de execução / administrador de insolvência as a professional role. Add a test that a fixture containing a debtor name produces no output containing it.

**Sources.** Before enabling detail fetching for a source, record in `docs/SOURCES.md` its robots.txt rules and any terms of use relevant to automated access and reuse; skip any source whose terms forbid it. Link to listings; never rehost their photos or copy their descriptions verbatim onto public pages (short evidence excerpts on the listing page are fine).

**Accounts and keys.** The project uses only the human's personal accounts, API keys and infrastructure.

## 13. Delivery plan

Build e-leilões end to end first, then add the other sources. Each phase ends with a stop for the human to review; do not start the next phase without approval.

**Phase 1 — Foundations (e-leilões only)**

- [ ] `docs/ENRICHMENT_PLAN.md` written and approved (section 2).
- [ ] Schema, migration, WAL mode, `enrichment_jobs` queue, enqueue hook in `run_scrape`.
- [ ] e-leilões detail fetch storing `listing_raw`; fixture saved.
- [ ] `ENRICH_ENABLED`, enrichment job, `/health` additions, heartbeat.
- [ ] Accepted when: with the flag off, all existing tests pass and behaviour is unchanged; with it on, every new e-leilões listing gets a raw payload within one enrichment interval.

**Phase 2 — Extraction, analysis, scoring**

- [ ] `llm.py` with provider fallback, budgets and `llm_calls` logging.
- [ ] Extraction with evidence validation; summary with number and banned-word checks.
- [ ] `analysis.py` with IMT, Imposto do Selo, fees from config; baseline loader.
- [ ] Scoring from `scoring.yaml`; `golden` and `report` CLIs.
- [ ] Accepted when: IMT tests match the human's simulator examples to the cent; on the golden set, `area_m2` and `occupancy` are correct or correctly unknown in at least 18 of 20 listings, and no value is ever saved with evidence that isn't in the payload.

**Phase 3 — Review and publishing**

- [ ] Webhook endpoint, draft messages, Aprovar / Rejeitar / Editar flows.
- [ ] Listing pages, `page_views`, post text rules.
- [ ] Accepted when: one real listing goes scrape → draft → approve → public page → copy-ready post with no manual database edits, and every webhook test passes.

**Phase 3b — Remaining sources.** Detail fetch and extraction for Leilosoc, Millennium and Santander, each with fixtures and added to the golden set.

**Then: run it for 6 weeks before building more.** The decision gate is demand, not impressions: replies or DMs asking about specific listings or regions, and listing page views per post, from the `report` CLI.

**Phase 4 — Later, only after the gate:** an investigator agent (Claude Agent SDK) that reads attached PDF notices for high-scoring listings with unknowns; new sources; a paid digest or alerts product.

## 14. Open questions for the human

Claude Code: ask these before the phase that needs them; do not guess.

- [ ] **Publishing scope (before Phase 1).** Today's filters (Lisboa + Setúbal, ≤ 300 000 €) look like a personal house search. Should publishing use the same scope, or a separate `PUBLISH_*` filter set, with the personal alerts unchanged?
- [ ] **Is `/` public today (before Phase 3)?** If listing pages go public, should the full list stay public, move behind basic auth in Caddy, or become the public index of approved listings only?
- [ ] **Review chat (before Phase 3).** Same chat as today's alerts, or a separate one?
- [ ] **Baseline data (before Phase 2).** Confirm the INE file to use (parish-level median €/m² of dwellings sold) and its latest period.
- [ ] **Tax values (before Phase 2).** Provide the current IMT brackets, Imposto do Selo rate and five simulator examples.
- [ ] **Reject reasons (before Phase 3).** Confirm or edit the list in section 8.
- [ ] **X account (before going live).** Account name, bio with the disclaimer, and whether posts link to the listing page (recommended) or straight to the source.
