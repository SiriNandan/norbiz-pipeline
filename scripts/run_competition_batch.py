#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import profile_complete_for_modules, profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.evidence import utc_now  # noqa: E402
from norway_company_agent.external_footprint import aggregate_footprint  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.official import fetch_official_modules  # noqa: E402
from norway_company_agent.website import fetch_website  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


# ---------------------------------------------------------------------------
# Approved connector helpers (inlined from extract_company_site_activity.py
# and extract_company_site_news.py — rights_status: "approved" in both)
# ---------------------------------------------------------------------------

def _site_activity_observation(profile: dict) -> dict | None:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    if website.get("status") != "available" or not identity.get("publishable"):
        return None
    source_url = value.get("final_url") or website.get("source_url")
    digest = value.get("content_sha256") or website.get("content_sha256")
    if not source_url or not digest or len(str(digest)) != 64:
        return None
    pages = value.get("pages") or []
    socials = value.get("social_links") or []
    org = str(profile["organisation_number"])
    return {
        "id": f"company-site-activity-{org}-{str(digest)[:16]}",
        "organisation_number": org,
        "platform": "company_site",
        "signal_type": "profile_metrics",
        "source_url": source_url,
        "retrieved_at": website.get("retrieved_at"),
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": list(identity.get("promotion_proof") or []) + [
            {"type": "website_identity_gate", "status": identity.get("status"), "score": identity.get("score")}
        ],
        "acquisition_mode": "permitted_public_page",
        "rights_status": "approved",
        "source_class": "company_site",
        "evidence_span": f"Exact company site snapshot with {len(pages)} bounded pages and {len(socials)} verified social links.",
        "metrics": {
            "bounded_pages_captured": len(pages),
            "verified_social_links": len(socials),
            "structured_organisation_records": len(value.get("structured_organisations") or []),
            "extraction_state": value.get("extraction_state"),
            "interpretation": "Observed site-surface completeness; not audience traffic or popularity.",
        },
        "strategy": "company_site_activity",
    }


_NEWS_PATH = re.compile(r"/(?:news|press|aktuelt|nyheter|artikler|blog)(?:/|$)", re.I)


def _site_news_observation(profile: dict) -> dict | None:
    import hashlib
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    if website.get("status") != "available" or not identity.get("publishable"):
        return None
    pages = [
        page for page in (value.get("pages") or [])
        if _NEWS_PATH.search(urlparse(str(page.get("url") or "")).path)
    ]
    if not pages:
        return None
    pages.sort(key=lambda p: (-len([part for part in urlparse(str(p.get("url") or "")).path.split("/") if part]), str(p.get("url") or "")))
    page = pages[0]
    url = str(page.get("url") or "")
    digest = str(page.get("content_sha256") or "")
    if not url.startswith(("http://", "https://")) or len(digest) != 64:
        return None
    org = str(profile["organisation_number"])
    title = str(page.get("title") or "Company news/activity page").strip()
    return {
        "id": "company-site-news-" + hashlib.sha256(f"{org}|{url}".encode()).hexdigest()[:24],
        "organisation_number": org,
        "platform": "company_site",
        "signal_type": "public_post",
        "source_url": url,
        "retrieved_at": website.get("retrieved_at"),
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": [{"type": "website_identity_gate", "score": identity.get("score"), "method": identity.get("method")}],
        "acquisition_mode": "permitted_public_page",
        "rights_status": "approved",
        "source_class": "company_site",
        "evidence_span": title[:1200],
        "metrics": {"captured_news_pages": len(pages), "interpretation": "Company-owned activity; not independent sentiment."},
        "strategy": "company_site_activity",
    }


def _load_annual_collect():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from run_annual_report_workforce_connector import collect  # type: ignore[import]
    return collect


def _load_google_news_fetch():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from run_google_news_rss_connector import fetch  # type: ignore[import]
    return fetch


def _load_nav_jobs_fetch():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from run_nav_jobs_connector import fetch  # type: ignore[import]
    return fetch


def _load_fagfolkguiden_fetch():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from run_fagfolkguiden_reviews_connector import fetch  # type: ignore[import]
    return fetch


def _load_youtube_fetch():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from run_youtube_search_connector import fetch  # type: ignore[import]
    return fetch


def _fmt_nok(v: float) -> str:
    if v >= 1_000_000_000:
        return f"NOK {v / 1_000_000_000:.1f}bn"
    if v >= 1_000_000:
        return f"NOK {v / 1_000_000:.1f}m"
    return f"NOK {int(v):,}"


def _generate_summary(profile: dict, prior_profile: dict | None = None) -> str:
    name = profile.get("name") or "This company"
    industry = profile.get("industry_label") or ""
    municipality = profile.get("municipality") or ""
    employees = profile.get("employees")
    legal_form = profile.get("legal_form") or ""
    roles = profile.get("roles") or []

    ev = profile.get("evidence", {})

    # Most-recent revenue from financials records
    revenue: float | None = None
    rev_year: str | None = None
    fin_records = ((ev.get("financials") or {}).get("value") or {}).get("records") or []
    if fin_records:
        rec = fin_records[0]
        revenue = rec.get("revenue")
        period = rec.get("period") or {}
        if period.get("tilDato"):
            rev_year = period["tilDato"][:4]

    website_status = (ev.get("website") or {}).get("status")
    ext_obs = ev.get("external_observations") or []
    news_count = sum(1 for o in ext_obs if o.get("platform") == "news")
    job_count = sum(1 for o in ext_obs if o.get("signal_type") == "job_posting")

    parts: list[str] = []

    # Sentence 1: What the company is
    desc: list[str] = [name]
    if legal_form:
        desc.append(f"({legal_form})")
    if industry:
        desc.append(f"operates in {industry.lower()}")
    else:
        desc.append("is a Norwegian company")
    if municipality:
        desc.append(f"based in {municipality.title()}")
    parts.append(" ".join(desc) + ".")

    # Sentence 2: Scale facts
    facts: list[str] = []
    if employees is not None:
        facts.append(f"{employees} registered employees")
    if revenue is not None:
        year_suffix = f" ({rev_year})" if rev_year else ""
        facts.append(f"revenue {_fmt_nok(revenue)}{year_suffix}")
    if roles:
        facts.append(f"{len(roles)} registered role{'s' if len(roles) != 1 else ''}")
    if facts:
        parts.append("Reported " + " and ".join(facts) + ".")

    # Sentence 3: Online presence and signals
    signals: list[str] = []
    if website_status == "available":
        signals.append("verified website present")
    if news_count > 0:
        signals.append(f"{news_count} recent news mention{'s' if news_count != 1 else ''}")
    if job_count > 0:
        signals.append(f"{job_count} active job posting{'s' if job_count != 1 else ''}")
    if signals:
        parts.append("Signals: " + "; ".join(signals) + ".")

    # Changes section — compare against prior run if available
    change_items: list[str] = []
    if prior_profile is None:
        parts.append("Changes: First observation — no prior run baseline available. Source: Enhetsregisteret / Regnskapsregisteret.")
    else:
        prior_ev = prior_profile.get("evidence", {})
        prior_fin = ((prior_ev.get("financials") or {}).get("value") or {}).get("records") or []
        prior_revenue: float | None = prior_fin[0].get("revenue") if prior_fin else None
        prior_employees = prior_profile.get("employees")
        prior_roles = prior_profile.get("roles") or []
        prior_website = (prior_ev.get("website") or {}).get("status")

        if prior_revenue is not None and revenue is not None and prior_revenue != revenue:
            pct = ((revenue - prior_revenue) / abs(prior_revenue)) * 100
            change_items.append(
                f"revenue changed {pct:+.1f}% from {_fmt_nok(prior_revenue)} to {_fmt_nok(revenue)}"
            )
        if prior_employees is not None and employees is not None and prior_employees != employees:
            change_items.append(f"employees changed from {prior_employees} to {employees}")
        if len(prior_roles) != len(roles):
            change_items.append(
                f"registered roles changed from {len(prior_roles)} to {len(roles)}"
            )
        if prior_website != website_status:
            change_items.append(f"website status changed from {prior_website or 'unknown'} to {website_status or 'unknown'}")

        if change_items:
            joined = "; ".join(change_items)
            parts.append("Changes: " + joined[0].upper() + joined[1:] + ". Source: Enhetsregisteret / Regnskapsregisteret.")
        else:
            parts.append("Changes: No changes detected since prior run. Source: Enhetsregisteret / Regnskapsregisteret.")

    # Unknowns — specific per-field with reason
    unknown_items: list[str] = []
    if employees is None:
        unknown_items.append("employee count not registered in Enhetsregisteret")
    if revenue is None:
        unknown_items.append("financial accounts not filed or not yet available in Regnskapsregisteret")
    if website_status != "available":
        reason = {
            "not_found": "no matching domain found",
            "identity_failed": "domain found but identity gate failed (possible wrong-company match)",
            "error": "fetch error during retrieval",
            "blocked": "site returned access-denied response",
        }.get(website_status or "", f"status={website_status or 'unknown'}")
        unknown_items.append(f"website not verified ({reason})")
    if not ext_obs:
        unknown_items.append(
            "no external observations gathered — connectors require a verified website or discoverable public presence"
        )
    if not roles:
        unknown_items.append("no roles registered in Brønnøysundregistrene")
    if unknown_items:
        parts.append("Unknowns: " + "; ".join(unknown_items) + ".")

    return " ".join(parts)


def _enrich_external(
    profile: dict,
    active_connectors: set[str],
    pdf_cache: Path | None,
    ocr_pages: int,
    annual_collect,
    google_news_fetch,
    nav_jobs_fetch,
    fagfolk_fetch,
    fagfolk_cache: Path | None,
    youtube_fetch,
) -> list[dict]:
    observations: list[dict] = []
    if "site_activity" in active_connectors:
        obs = _site_activity_observation(profile)
        if obs:
            observations.append(obs)
    if "site_news" in active_connectors:
        obs = _site_news_observation(profile)
        if obs:
            observations.append(obs)
    if "annual_workforce" in active_connectors and annual_collect and pdf_cache:
        try:
            obs, _ = annual_collect(profile, pdf_cache, ocr_pages=ocr_pages, ocr_dpi=130)
            if obs:
                observations.append(obs)
        except Exception:
            pass
    if "google_news" in active_connectors and google_news_fetch:
        try:
            rows, _ = google_news_fetch(profile, limit=5, years=2)
            observations.extend(rows)
        except Exception:
            pass
    if "nav_jobs" in active_connectors and nav_jobs_fetch:
        try:
            rows, _ = nav_jobs_fetch(profile, limit=10)
            observations.extend(rows)
        except Exception:
            pass
    if "fagfolkguiden" in active_connectors and fagfolk_fetch and fagfolk_cache:
        try:
            rows, _ = fagfolk_fetch(profile, fagfolk_cache)
            observations.extend(rows)
        except Exception:
            pass
    if "youtube" in active_connectors and youtube_fetch:
        try:
            rows, _ = youtube_fetch(profile)
            observations.extend(rows)
        except Exception:
            pass
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    parser.add_argument("--organisations", required=True, help="JSON, JSONL, or text organisation-number list")
    parser.add_argument("--bulk", required=True, help="Frozen Brreg entity snapshot")
    parser.add_argument("--output", required=True, help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--modules",
        default="registry,accounting_obligation,registry_live,financials,financial_history,roles,group,locations,website",
    )
    parser.add_argument(
        "--connectors",
        default="site_activity,site_news,google_news",
        help="Comma-separated approved connectors to run after official enrichment. "
             "Set to empty string to disable. "
             "Choices: site_activity, site_news, annual_workforce, google_news, nav_jobs, fagfolkguiden, youtube",
    )
    parser.add_argument(
        "--pdf-cache",
        default="workspace/pdf-cache",
        help="Directory to cache annual-report PDFs (used by annual_workforce connector)",
    )
    parser.add_argument(
        "--fagfolk-cache",
        default="workspace/fagfolk-cache",
        help="Directory to cache Fagfolkguiden HTML pages",
    )
    parser.add_argument(
        "--ocr-pages",
        type=int,
        default=0,
        help="Number of PDF pages to OCR when text extraction yields no workforce phrases "
             "(requires pdftoppm + tesseract). 0 disables OCR.",
    )
    args = parser.parse_args()

    active_connectors: set[str] = {c.strip() for c in args.connectors.split(",") if c.strip()}
    pdf_cache: Path | None = None
    annual_collect = None
    if "annual_workforce" in active_connectors:
        pdf_cache = Path(args.pdf_cache)
        pdf_cache.mkdir(parents=True, exist_ok=True)
        try:
            annual_collect = _load_annual_collect()
        except Exception as exc:
            print(f"[warn] annual_workforce connector unavailable ({exc}); skipping", file=sys.stderr)
            active_connectors.discard("annual_workforce")

    google_news_fetch = None
    if "google_news" in active_connectors:
        try:
            google_news_fetch = _load_google_news_fetch()
        except Exception as exc:
            print(f"[warn] google_news connector unavailable ({exc}); skipping", file=sys.stderr)
            active_connectors.discard("google_news")

    nav_jobs_fetch = None
    if "nav_jobs" in active_connectors:
        try:
            nav_jobs_fetch = _load_nav_jobs_fetch()
        except Exception as exc:
            print(f"[warn] nav_jobs connector unavailable ({exc}); skipping", file=sys.stderr)
            active_connectors.discard("nav_jobs")

    fagfolk_fetch = None
    fagfolk_cache: Path | None = None
    if "fagfolkguiden" in active_connectors:
        try:
            fagfolk_fetch = _load_fagfolkguiden_fetch()
            fagfolk_cache = Path(args.fagfolk_cache)
            fagfolk_cache.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            print(f"[warn] fagfolkguiden connector unavailable ({exc}); skipping", file=sys.stderr)
            active_connectors.discard("fagfolkguiden")

    youtube_fetch = None
    if "youtube" in active_connectors:
        try:
            youtube_fetch = _load_youtube_fetch()
        except Exception as exc:
            print(f"[warn] youtube connector unavailable ({exc}); skipping", file=sys.stderr)
            active_connectors.discard("youtube")

    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations)
    orgs = [item["organisation_number"] for item in organisation_inputs]
    if len(orgs) != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} organisations, received {len(orgs)}")
    profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs)
    annotations = {item["organisation_number"]: item for item in organisation_inputs}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website"}
    operations = {"requests": 0, "bytes": 0, "latencies_ms": []}

    def enrich(profile: dict) -> tuple[dict, dict]:
        records, metrics = fetch_official_modules(profile["organisation_number"], fetch_modules)
        profile["evidence"].update(records)
        website_metrics: dict = {"requests": 0, "bytes": 0, "latencies_ms": []}
        if "website" in requested_modules:
            website_record, website_metrics = fetch_website(profile.get("website"))
            profile["evidence"]["website"] = apply_website_identity_gate(profile, website_record)["website"]

        # Run approved external connectors (no extra network cost for site_activity/site_news;
        # annual_workforce downloads official PDFs from brreg.no;
        # google_news uses Google RSS public feed; nav_jobs uses NAV NLOD API)
        if active_connectors:
            observations = _enrich_external(
                profile, active_connectors, pdf_cache, args.ocr_pages, annual_collect,
                google_news_fetch, nav_jobs_fetch, fagfolk_fetch, fagfolk_cache, youtube_fetch,
            )
            if observations:
                profile["evidence"]["external_observations"] = observations
                profile["evidence"]["external_footprint"] = {
                    **aggregate_footprint(observations),
                    "retrieved_at": utc_now(),
                    "connector_count": len(observations),
                }

        profile["summary"] = _generate_summary(profile, prior_profile=state.get(profile["organisation_number"]))

        metric = {
            "requests": len(metrics) + website_metrics.get("requests", 0),
            "bytes": sum(item.bytes_received for item in metrics) + website_metrics.get("bytes", 0),
            "latencies_ms": [item.elapsed_ms for item in metrics] + website_metrics.get("latencies_ms", []),
        }
        profile["run_metrics"] = metric
        return profile, metric

    state: dict[str, dict] = {}
    resumed_profiles = 0
    profiles_output = Path(args.profiles_output)
    if args.resume and profiles_output.exists():
        prior = [json.loads(line) for line in profiles_output.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not set(item["organisation_number"] for item in prior).issubset(set(orgs)):
            raise SystemExit("Resume profile membership is not a subset of this batch")
        state = {
            item["organisation_number"]: item
            for item in prior
            if profile_complete_for_modules(item, requested_modules)
        }
        resumed_profiles = len(state)
    pending_profiles = [profile for profile in profiles if profile["organisation_number"] not in state]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(enrich, profile): profile["organisation_number"] for profile in pending_profiles}
        for index, future in enumerate(as_completed(futures), 1):
            profile, metric = future.result()
            state[profile["organisation_number"]] = profile
            operations["requests"] += metric["requests"]
            operations["bytes"] += metric["bytes"]
            operations["latencies_ms"].extend(metric["latencies_ms"])
            if index % args.checkpoint_every == 0 or index == len(pending_profiles):
                checkpoint = [state[org] for org in orgs if org in state]
                write_jsonl(profiles_output, checkpoint)

    completed_at = utc_now()
    ordered_profiles = [state[org] for org in orgs]
    envelopes = [
        terminal_envelope(profile, run_id=args.run_id, modules=requested_modules, started_at=started_at, completed_at=completed_at)
        for profile in ordered_profiles
    ]
    validation = validate_envelopes(envelopes, args.expected_count)
    write_jsonl(profiles_output, ordered_profiles)
    write_jsonl(Path(args.output), envelopes)
    latencies = sorted(operations.pop("latencies_ms"))
    operations["p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    operations["p95_ms"] = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    external_counts = {
        "profiles_with_external_observations": sum(
            1 for p in ordered_profiles if p.get("evidence", {}).get("external_observations")
        ),
        "active_connectors": sorted(active_connectors),
    }
    report = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "expected_count": args.expected_count,
        "emitted_envelopes": len(envelopes),
        "resumed_profiles": resumed_profiles,
        "profiles_fetched_this_run": len(pending_profiles),
        "modules": requested_modules,
        "connectors": external_counts,
        "registry": registry_metadata,
        "operations": operations,
        "validation": validation,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if validation["passed"] else 1)


if __name__ == "__main__":
    main()
