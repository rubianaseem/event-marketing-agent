"""Run the versioned evaluation set and the failure suite. Writes evals/results.json and evals/report.md.

    python run_evals.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from evals import failure_suite, harness
from event_agent.ingest import load_json

ROOT = Path(__file__).resolve().parent


def main() -> int:
    cases = load_json(ROOT / "evals" / "cases_v1.json")
    event, policy = load_json(ROOT / "config" / "event.json"), load_json(ROOT / "config" / "policy.json")
    crm = harness._crm()
    t = harness.run_targeting(cases["targeting"], crm, event, policy)
    a = harness.run_attribution(cases["attribution"], event, policy)
    out = {
        "version": cases["version"], "policy_version": policy["policy_version"],
        "targeting": {"agent": harness.score_targeting(t, "agent"), "baseline": harness.score_targeting(t, "baseline")},
        "attribution": {"agent": harness.score_attribution(a, "agent"), "baseline": harness.score_attribution(a, "baseline")},
        "injected_enrichment_text_quarantined": sum(r["agent"]["description_quarantined"] for r in t),
        "credits_on_sample": harness.credits_on_sample(event, policy),
        "failure_suite": failure_suite.run(),
    }
    (ROOT / "evals" / "results.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    ta, tb = out["targeting"]["agent"], out["targeting"]["baseline"]
    aa, ab = out["attribution"]["agent"], out["attribution"]["baseline"]
    fs = out["failure_suite"]
    cr = out["credits_on_sample"]
    lines = [f"# Evaluation report ({out['version']}, policy {out['policy_version']})", "",
             "Synthetic cases only. Reproduce with `python run_evals.py`.", "",
             "## Targeting decisions", "", "| Measure | Agent | Naive baseline |", "|---|---|---|",
             f"| Cases | {ta['cases']} | {tb['cases']} |",
             f"| Exact decision accuracy | {ta['exact_accuracy']} | {tb['exact_accuracy']} |",
             f"| Invite precision | {ta['invite_precision']} | {tb['invite_precision']} |",
             f"| Invite recall | {ta['invite_recall']} | {tb['invite_recall']} |",
             f"| Hard-control violations (invited when gold is suppress/customer/owner hold/consent hold/competitor/internal/quarantine/duplicate review) | {ta['hard_control_violations']} | {tb['hard_control_violations']} |",
             f"| Of which on held-out cases written after the rules | {ta['hard_control_violations'] - ta['hard_control_violations_excluding_held_out']} | {tb['hard_control_violations'] - tb['hard_control_violations_excluding_held_out']} |",
             f"| Duplicate contacts the import would create | {ta['duplicate_creates']} | {tb['duplicate_creates']} |", "",
             f"Agent misses: {', '.join(ta['misses']) or 'none'}", "",
             "## Post-event attribution", "", "| Measure | Agent | Naive baseline |", "|---|---|---|",
             f"| Cases | {aa['cases']} | {ab['cases']} |",
             f"| All fields correct | {aa['all_fields_correct']} | {ab['all_fields_correct']} |",
             f"| First-touch overwrites | {aa['first_touch_overwrites']} | {ab['first_touch_overwrites']} |",
             f"| Wrong campaign associations | {aa['wrong_campaign_associations']} | {ab['wrong_campaign_associations']} |",
             f"| Missed associations | {aa['missed_associations']} | {ab['missed_associations']} |",
             f"| Last-touch errors | {aa['last_touch_errors']} | {ab['last_touch_errors']} |", "",
             "## Enrichment spend on sample_candidates.csv (your per-field credit assumption)", "",
             f"Agent requests {cr['agent_estimated_credits']} credits for {cr['agent_domains']} domains after gating and CRM backfill; "
             f"enriching every incomplete row would request {cr['naive_estimated_credits']} credits for {cr['naive_rows']} rows.", "",
             f"Injected text in enrichment descriptions quarantined: {out['injected_enrichment_text_quarantined']} of 1 case.", "",
             f"## Failure suite: {fs['passed']}/{fs['total']} passed", ""]
    for s in fs["scenarios"]:
        lines.append(f"- {'PASS' if s['passed'] else 'FAIL'} {s['id']}: {s['name']}")
    (ROOT / "evals" / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if fs["passed"] == fs["total"] and ta["hard_control_violations_excluding_held_out"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
