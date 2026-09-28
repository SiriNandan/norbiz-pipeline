#!/usr/bin/env python3
"""NAV Stillingsregisteret job-posting connector.

Fetches active job postings for a company from the Norwegian Labour and
Welfare Administration (NAV) public job registry.

Licence:   NLOD (Norwegian Open Government Data) — free, no auth required.
Platform:  job_board
Endpoint:  https://arbeidsplassen.nav.no/public-feed/api/v1/ads
Identity:  employer.orgnr direct-filter  → exact_entity = True
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

UA = "SignalpostResearchPOC/1.0 (https://builderr.ai; bounded qualification run)"
_BASE = "https://arbeidsplassen.nav.no/public-feed/api/v1/ads"


def fetch(profile: dict, limit: int = 10) -> tuple[list[dict], dict]:
    """Return (observations, status) for one company."""
    org = str(profile["organisation_number"])
    params = urllib.parse.urlencode({"employer.orgnr": org, "size": min(limit, 100), "page": 0})
    url = f"{_BASE}?{params}"
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(1_000_000)
        data = json.loads(raw)
        content = data.get("content") or []
        observations = []
        for job in content[:limit]:
            uuid = str(job.get("uuid") or "").strip()
            title = str(job.get("title") or "").strip()
            if not uuid or not title:
                continue
            emp = job.get("employer") or {}
            emp_orgnr = str(emp.get("orgnr") or "").strip()
            if emp_orgnr and emp_orgnr != org:
                continue
            source_url = f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
            published = str(job.get("published") or "").strip() or None
            expires = str(job.get("expires") or "").strip() or None
            digest = hashlib.sha256(f"{org}|{uuid}|{title}".encode()).hexdigest()
            observations.append({
                "id": f"nav-job-{org}-{hashlib.sha256(uuid.encode()).hexdigest()[:20]}",
                "organisation_number": org,
                "platform": "job_board",
                "signal_type": "job_posting",
                "source_url": source_url,
                "retrieved_at": retrieved_at,
                "published_at": published,
                "content_sha256": digest,
                "exact_entity": True,
                "identity_proof": [
                    {"type": "employer_orgnr_direct_filter", "value": org},
                    {"type": "employer_name", "value": str(emp.get("name") or "")},
                ],
                "acquisition_mode": "official_api",
                "rights_status": "approved",
                "source_class": "job_board",
                "evidence_span": title,
                "text": title,
                "expires_at": expires,
                "strategy": "nav_stillingsregisteret_direct",
            })
        return observations, {
            "organisation_number": org,
            "total_available": int(data.get("totalElements") or 0),
            "accepted": len(observations),
        }
    except Exception as exc:
        return [], {
            "organisation_number": org,
            "total_available": 0,
            "accepted": 0,
            "error": f"{type(exc).__name__}: {str(exc)[:180]}",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="NAV Stillingsregisteret job-posting connector.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--organisations", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--per-company", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    wanted = [line.strip() for line in Path(args.organisations).read_text(encoding="utf-8").splitlines() if line.strip()]
    profiles = {
        str(row["organisation_number"]): row
        for row in (json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip())
    }

    observations: list[dict] = []
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, 16)) as pool:
        futures = {pool.submit(fetch, profiles[org], args.per_company): org for org in wanted if org in profiles}
        for future in as_completed(futures):
            rows, status = future.result()
            observations.extend(rows)
            results.append(status)

    order = {org: idx for idx, org in enumerate(wanted)}
    observations.sort(key=lambda r: (order.get(r["organisation_number"], 9999), r.get("published_at") or "", r["id"]))
    results.sort(key=lambda r: order.get(r["organisation_number"], 9999))

    Path(args.output).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in observations), encoding="utf-8"
    )
    report = {
        "connector": "nav_stillingsregisteret_v1",
        "licence": "NLOD",
        "companies": len(wanted),
        "companies_with_jobs": len({r["organisation_number"] for r in observations}),
        "observations": len(observations),
        "errors": sum("error" in r for r in results),
        "company_results": results,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "company_results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
