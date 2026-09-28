# NorBiz — Norwegian Company Intelligence Pipeline

> End-to-end data pipeline that takes a Norwegian organisation number and produces a fully sourced, auditable company intelligence profile. Built for the [Signalpost 2026 challenge](https://builderr.ai/challenges/signalpost).

---

## The Problem

Public data about Norwegian companies is scattered and unverified. The Brønnøysund Registry holds the authoritative ground truth — but it is a raw data dump, not an intelligence layer.

A business analyst who wants to understand a company must manually cross-check the registry, find the real website, look for news, and reconcile conflicting information across sources. That doesn't scale.

**This pipeline solves it.** Given only an organisation number, it automatically anchors legal identity, fetches official financials, discovers and verifies the external web presence, and collects signals from seven parallel sources — all with full source attribution and zero hallucination.

---

## What It Builds

```
Organisation numbers  →  Verified intelligence profiles  →  NorBiz dashboard
      (JSONL)                   (profiles.jsonl)               (dashboard.html)
```

Each profile contains:

- Legal identity anchored against the official bulk registry
- Annual accounts (revenue, operating profit, net result, assets, debt)
- Board members and executive roles with tenure dates
- Registered business locations
- Verified company website with entity-match evidence
- External signals: site activity, news, job postings, workforce reports, reviews, video
- A structured change log from snapshot diffing
- Full source attribution on every field

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  1. Identity anchor          ← Brønnøysund bulk CSV (148 MB)│
│  2. Official data modules    ← BRREG REST API               │
│     • Registry (live record)                                │
│     • Annual accounts + 3-year history                      │
│     • Roles and board composition                           │
│     • Registered business premises                          │
│  3. Website identity gate    ← Crawl + strict entity match  │
│  4. External connectors      ← 7 parallel sources           │
│     site_activity · site_news · google_news                 │
│     nav_jobs · annual_workforce · fagfolkguiden · youtube   │
│  5. Snapshot diff engine     ← Refresh / change detection   │
│  6. Research agent           ← Deterministic Q&A layer      │
└─────────────────────────────────────────────────────────────┘
```

**Identity gate is hard.** A discovered website, news article, or review must be provably about the same legal entity. One wrong-company match disqualifies the profile. No soft signals accepted.

---

## Repository Structure

```
signalpost-starter-kit/
│
├── src/norway_company_agent/        # Core library
│   ├── batch.py                     # Concurrent runner with 25-company checkpoints
│   ├── identity.py                  # Entity identity gate
│   ├── official.py                  # BRREG registry + financials modules
│   ├── website.py                   # Website crawl and entity match
│   ├── external_footprint.py        # External signal aggregation
│   ├── external_tasks.py            # Per-connector task definitions
│   ├── external_control.py          # Rate limiting and policy enforcement
│   ├── discovery.py                 # Website candidate discovery
│   ├── evidence.py                  # Evidence record structure and hashing
│   ├── research.py                  # Research agent (deterministic Q&A)
│   ├── refresh.py                   # Snapshot diff and change detection
│   ├── snapshots.py                 # Snapshot store and retrieval
│   ├── crawl_events.py              # HTTP event recording
│   ├── http.py                      # Shared HTTP client with retries
│   ├── operations.py                # Operation counters and latency tracking
│   ├── sampling.py                  # Universe sampling utilities
│   ├── scrapy_crawler.py            # Playwright-backed JS rendering (optional)
│   ├── sentiment.py                 # Sentiment model — HuggingFace (optional)
│   └── workspace.py                 # Run workspace management
│
├── scripts/                         # Runnable entry points
│   ├── run_competition_batch.py     # ★ Main production entry point
│   ├── run_refresh_replay.py        # Deterministic refresh demo
│   ├── evaluate_research_agent.py   # Research Q&A evaluation suite
│   ├── evaluate_external_footprint.py
│   ├── score_competition_v3.py      # Local 100-point proxy scorer
│   ├── run_brave_discovery.py       # Brave Search API connector
│   ├── run_google_news_rss_connector.py
│   ├── run_nav_jobs_connector.py
│   ├── run_annual_report_workforce_connector.py
│   ├── run_fagfolkguiden_reviews_connector.py
│   ├── run_youtube_search_connector.py
│   ├── run_sentiment_model.py
│   ├── ask_agent.py                 # Interactive CLI research agent
│   └── ...                          # Identity, normalisation, scoring utilities
│
├── tests/                           # 104 tests, 5 subtests — all passing
│   ├── test_poc.py
│   └── fixtures/                    # Saved responses for offline replay
│
├── docs/                            # Architecture and design notes
│   ├── competition-control-loop.md
│   ├── external-connectors.md
│   ├── external-footprint-loop.md
│   ├── norway-sources.md
│   └── ...
│
├── out/                             # Pipeline output
│   ├── prod_2/                      # ★ Production run — 1,000 companies
│   │   ├── envelopes.jsonl          # Submission envelopes (claims + evidence)
│   │   ├── profiles.jsonl           # Structured intelligence profiles
│   │   └── run-report.json          # Runtime stats and validation report
│   ├── research-14/                 # Research agent evaluation profiles
│   ├── dashboard.html               # Intelligence dashboard (204 KB, standalone)
│   ├── norbiz.html                  # NorBiz SPA (React + htm, no build needed)
│   ├── proxy-score.json             # Local 100-point proxy score
│   ├── research-report.json         # Research agent evaluation (10.5/12)
│   ├── external-report.json         # External footprint coverage
│   ├── ux-report.json               # UX evaluation (7/8)
│   └── refresh-demo.json            # Refresh replay qualification artefact
│
├── data/
│   └── universe-metadata.json       # Universe size and sample statistics
│
├── workspace/                       # Connector caches (runtime, gitignored)
│
├── build_dashboard.py               # Build out/dashboard.html from profiles
├── merge_and_evaluate.py            # Merge runs and run all evaluators
├── select_entry_batch.py            # Sample 1,000 companies from universe
├── filter_entry.py                  # Filter companies needing manual research
├── first_run.py                     # Offline smoke test — no network needed
├── OUTPUT_CONTRACT.md               # Envelope schema specification
├── pyproject.toml                   # Python dependencies
├── uv.lock                          # Pinned dependency tree
└── entry-companies.jsonl            # 1,000 selected organisation numbers (run manifest)
```

---

## Quick Start

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

### Offline smoke test (no downloads, no API keys)

```bash
uv sync
python first_run.py
```

Opens `out/refresh-demo.json` — the refresh engine replays a saved snapshot and
reports two expected changes, zero false changes. Runs entirely from saved responses.

### Full production run

```bash
# 1. Download registry data (~148 MB plain CSV)
curl -L "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv" \
  -o brreg-enheter.csv

# 2. Download company universe (~12 MB gzip)
curl -L "https://builderr.ai/signalpost-company-universe-2025.jsonl.gz" \
  -o signalpost-universe.jsonl.gz

# 3. Select 1,000 companies
uv run python select_entry_batch.py \
  --universe signalpost-universe.jsonl.gz \
  --count 1000 \
  --output entry-companies.jsonl

# 4. Ten-company smoke test first
head -n 10 entry-companies.jsonl > smoke-companies.jsonl

uv run python scripts/run_competition_batch.py \
  --organisations smoke-companies.jsonl \
  --bulk brreg-enheter.csv \
  --profiles-output out/smoke-profiles.jsonl \
  --output out/smoke-envelopes.jsonl \
  --report out/smoke-report.json \
  --run-id smoke-001 \
  --expected-count 10

# 5. Full run (all 7 connectors, 16 workers, ~60 min)
uv run python scripts/run_competition_batch.py \
  --organisations entry-companies.jsonl \
  --bulk brreg-enheter.csv \
  --profiles-output out/profiles.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --run-id local-001 \
  --expected-count 1000 \
  --workers 16 \
  --connectors site_activity,site_news,google_news,nav_jobs,annual_workforce,fagfolkguiden,youtube
```

### Resume an interrupted run

The pipeline checkpoints every 25 companies. To resume where it stopped:

```bash
uv run python scripts/run_competition_batch.py \
  --organisations entry-companies.jsonl \
  --bulk brreg-enheter.csv \
  --profiles-output out/profiles.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --run-id local-001 \
  --expected-count 1000 \
  --workers 16 \
  --resume
```

### Run evaluators

```bash
# Merge prod_2 + research-14, run all evaluators, update score
uv run python merge_and_evaluate.py

# View proxy score
cat out/proxy-score.json
```

### Tests

```bash
uv run --with pytest pytest -q
```

---

## Output Contract

Every run emits one JSON envelope per input organisation number:

```json
{
  "organisation_number": "985589003",
  "run": {
    "run_id": "prod-2",
    "started_at": "2026-09-17T07:26:45Z",
    "completed_at": "2026-09-17T07:48:49Z",
    "terminal_status": "completed"
  },
  "claims": [
    {
      "field": "official_website",
      "value": "https://example.no/",
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-1"]
    }
  ],
  "evidence": [
    {
      "id": "ev-1",
      "source_url": "https://example.no/",
      "source_class": "company_owned",
      "retrieved_at": "2026-09-17T07:30:00Z",
      "content_sha256": "...",
      "claim_span": "Example AS, org 985 589 003"
    }
  ],
  "changes": [],
  "errors": [],
  "operations": {
    "requests": 7,
    "runtime_ms": 1320,
    "third_party_cost_usd": 0
  }
}
```

---

## Pipeline Design Decisions

**Identity before everything.** The website identity gate is strict: legal name on the page, organisation number verifiable. One wrong-company match disqualifies the profile.

**Source attribution on every claim.** Every field carries a `source_url`, `source_class`, retrieval timestamp, and content hash. Nothing is inferred without evidence.

**Deterministic resume and replay.** Checkpoints every 25 companies. Refresh replays a saved snapshot and emits only materially changed fields.

**Zero cost by default.** No paid APIs required for baseline coverage. External coverage scales with additional connectors (Brave Search, etc.).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.13, uv |
| HTTP | httpx (async), with retry + rate limiting |
| HTML parsing | trafilatura, BeautifulSoup4, extruct |
| JS rendering | Scrapy + Playwright (optional) |
| PDF extraction | pypdf |
| Video | yt-dlp |
| Validation | Pydantic v2 |
| Tests | pytest (104 tests) |

**Optional modules** (installed separately):

| Module | Purpose | Install |
|---|---|---|
| `sentiment` | Employer sentiment from reviews | `uv sync --extra sentiment` |
| `crawler` | JS-rendered pages via Playwright | `uv sync --extra crawler` |

---

## Results

| Metric | Value |
|---|---|
| Companies processed | 1,000 / 1,000 ✓ |
| Official data completeness | 14.99 / 15 pts |
| Daily extensibility & refresh | 12.0 / 12 pts (perfect) |
| Product UX | 7 / 8 pts |
| External observations | 49 companies with verified external intelligence |
| Pipeline proxy score | 38.99 / 100 |
| p50 request latency | ~1,260 ms |
| Cost per 100-company run | $0 |
| Test suite | 104 tests, 5 subtests — all passing |

---

## NorBiz Frontend

A companion intelligence dashboard is built separately in [`../builder-agent-native-starter-250/`](../builder-agent-native-starter-250/):

- React 19 + TypeScript + Vite + Tailwind CSS v4
- Company card grid with search, filter, and sort
- Detail drawer: Overview · Financials · People · Sources · Ask
- Registry-bounded Q&A chat (no hallucination)

```bash
cd ../builder-agent-native-starter-250
pnpm install
pnpm dev
```

---

## Author

**Siri Nandan Chilamkurthy** — built as part of a personal data engineering and AI research portfolio.
