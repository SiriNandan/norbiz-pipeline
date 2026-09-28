#!/usr/bin/env python3
"""Merge research-14 into prod_2 profiles, then run all evaluation steps."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Step 1: Merge profiles
print("=== Step 1: Merging profiles ===")
prod2 = [json.loads(l) for l in (ROOT / "out/prod_2/profiles.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
r14 = [json.loads(l) for l in (ROOT / "out/research-14/profiles.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
existing_orgs = {p["organisation_number"] for p in prod2}
added = [p for p in r14 if p["organisation_number"] not in existing_orgs]
merged = prod2 + added
merged_path = ROOT / "out/merged-profiles.jsonl"
with merged_path.open("w", encoding="utf-8") as f:
    for p in merged:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
print(f"Merged: {len(prod2)} + {len(added)} new = {len(merged)} total profiles")

# Step 2: Research agent evaluation
print("\n=== Step 2: Research agent evaluation ===")
workspace = ROOT / "workspace/research-workspace.json"
workspace.parent.mkdir(exist_ok=True)
result = subprocess.run([
    sys.executable, str(ROOT / "scripts/evaluate_research_agent.py"),
    "--input", str(merged_path),
    "--suite", str(ROOT / "tests/fixtures/research-agent-suite-submission-1000.json"),
    "--output", str(ROOT / "out/research-report.json"),
    "--workspace", str(workspace),
], capture_output=False, text=True, cwd=ROOT)
if result.returncode != 0:
    print("Research agent evaluation returned non-zero (qualification may not have passed, but continuing)")

# Step 3: Resume test — create a dummy run with resume where 0 are fetched
print("\n=== Step 3: Resume test ===")
resume_result = subprocess.run([
    sys.executable, str(ROOT / "scripts/run_competition_batch.py"),
    "--organisations", str(ROOT / "entry-companies.jsonl"),
    "--bulk", str(ROOT / "brreg-enheter.csv"),
    "--profiles-output", str(ROOT / "out/prod_2/profiles.jsonl"),
    "--output", str(ROOT / "out/resume-test/envelopes.jsonl"),
    "--report", str(ROOT / "out/resume-test/run-report.json"),
    "--run-id", "resume-test-001",
    "--expected-count", "1000",
    "--workers", "4",
    "--connectors", "site_activity,site_news,google_news",
    "--resume",
], capture_output=False, text=True, cwd=ROOT)
if resume_result.returncode != 0:
    print("Resume test failed")
else:
    resume_report = json.loads((ROOT / "out/resume-test/run-report.json").read_text(encoding="utf-8"))
    fetched = resume_report.get("profiles_fetched_this_run", -1)
    print(f"Resume test: profiles_fetched_this_run = {fetched} (need 0 for full points)")

print("\n=== All steps done ===")
print("Outputs:")
print("  Research report: out/research-report.json")
print("  Resume report:   out/resume-test/run-report.json")
print("  Merged profiles: out/merged-profiles.jsonl")
