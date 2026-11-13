"""End-to-end demo on synthetic data. Approvals here are SIMULATED to show the gates working;
they are not permission to touch a real CRM. Writes outputs/sample_run and outputs/sample_attribution.

    python run_demo.py
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from event_agent import approval, enrichment as enr, pipeline
from event_agent.audit import verify
from event_agent.ingest import load_enrichment_source, load_json
from event_agent.util import parse_dt

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "config"
PLAN_AS_OF, ATTR_AS_OF = "2026-11-13T12:00:00Z", "2026-12-04T10:00:00Z"
OUT, ATTR = ROOT / "outputs" / "sample_run", ROOT / "outputs" / "sample_attribution"
LOG: list[str] = []


def say(msg=""):
    print(msg)
    LOG.append(msg)


def h(rd, name):
    return json.loads((rd / "manifest.json").read_text())["artifacts"][name]["sha256"]


def main():
    for d in (OUT, ATTR):
        shutil.rmtree(d, ignore_errors=True)
    state = Path(tempfile.mkdtemp(prefix="ema_demo_state_"))
    approvers, policy = load_json(CFG / "approvers.json"), load_json(CFG / "policy.json")
    names = ["candidates", "crm_contacts", "crm_companies", "crm_deals", "owners", "suppression", "crm_manifest"]
    files = ["sample_candidates.csv", "sample_crm_contacts.csv", "sample_crm_companies.csv", "sample_crm_deals.csv",
             "sample_owners.csv", "sample_suppression.csv", "sample_export_manifest.json"]
    paths = {k: str(ROOT / f) for k, f in zip(names, files)}
    now = parse_dt(PLAN_AS_OF)

    say("== 1. plan: dedupe, CRM match, hard gates, enrichment request")
    res = pipeline.plan(paths, OUT, CFG, as_of=PLAN_AS_OF)
    r = res["request"]
    say(f"run {res['run_id']}: {r['domains']} domains need enrichment, estimated {r['estimated_credits_total']} credits (your per-field assumption)")

    say("\n== 2. try to enrich before approval")
    try:
        pipeline.enrich(OUT, ROOT / "sample_enrichment.csv", state, CFG)
    except enr.EnrichmentGateError as exc:
        say(f"REFUSED: {exc}")

    say("\n== 3. approval attempts on enrichment_request.json (simulated reviewer)")
    for who, sha in (("agent", h(OUT, "enrichment_request.json")), ("rubia", "0" * 64)):
        try:
            approval.approve(OUT, "enrichment_request.json", sha, who, now, approvers, policy)
        except approval.ApprovalError as exc:
            say(f"REFUSED ({who}): {exc}")
    rec = approval.approve(OUT, "enrichment_request.json", h(OUT, "enrichment_request.json"), "rubia", now, approvers, policy)
    approval.release(OUT, "enrichment_request.json", now, policy)
    say(f"approved by {rec['approver_name']} with sha256 {rec['sha256'][:12]}..., released")

    say("\n== 4. enrich with the MOCK provider (429 and 5xx injected; sleeps skipped in the demo)")
    prov = enr.MockEnrichmentProvider(load_enrichment_source(ROOT / "sample_enrichment.csv"))
    out = pipeline.enrich(OUT, None, state, CFG, provider=prov, sleep=lambda s: None)
    for dom, v in sorted(out["results"].items()):
        say(f"  {dom}: {v['status']} after {v['attempts']} attempt(s)")
    say(f"provider calls {out['provider_calls']}, credits charged {out['credits_charged_this_run']}")
    again = enr.MockEnrichmentProvider(load_enrichment_source(ROOT / "sample_enrichment.csv"))
    pipeline.enrich(OUT, None, state, CFG, provider=again, sleep=lambda s: None)
    say(f"re-run: provider calls {again.calls}, credits charged {again.credits_charged} (cache, no double spend)")

    say("\n== 5. build: post-enrichment gates, ranking, capacity, drafts, import proposal")
    b = pipeline.build(OUT, CFG, state, as_of=PLAN_AS_OF)
    for k, v in sorted(b["counts"].items(), key=lambda kv: (-kv[1], kv[0])):
        say(f"  {k}: {v}")
    say(f"import rows {b['import_rows']}, review rows {b['review']}")

    say("\n== 6. approve and release the import (simulated)")
    approval.approve(OUT, "hubspot_import_proposed.csv", h(OUT, "hubspot_import_proposed.csv"), "rubia", now, approvers, policy)
    approval.release(OUT, "hubspot_import_proposed.csv", now, policy)
    say("released outputs/sample_run/released/hubspot_import_proposed.csv; the HubSpot import itself is a manual step")

    say("\n== 7. after the event: attribution proposals")
    ap = {k: paths[k] for k in names[1:6]}
    ap["crm_manifest"] = str(ROOT / "sample_export_manifest_post_event.json")
    ap["attendance"] = str(ROOT / "sample_attendance.csv")
    s = pipeline.attribute(ap, ATTR, CFG, plan_run=str(OUT), as_of=ATTR_AS_OF)
    for k in ("associations_proposed", "already_associated_skipped", "first_touch_set", "first_touch_preserved",
              "first_touch_unverified", "last_touch_event", "review"):
        say(f"  {k}: {s[k]}")
    later = parse_dt(ATTR_AS_OF)
    for art in ("campaign_associations_proposed.csv", "attribution_updates_proposed.csv"):
        approval.approve(ATTR, art, h(ATTR, art), "rubia", later, approvers, policy)
        approval.release(ATTR, art, later, policy)
    say("associations and attribution updates approved and released (simulated); applying them in HubSpot is manual")

    say("\n== 8. verify hash-chained logs")
    for d in (OUT, ATTR):
        for f in ("decisions.jsonl", "approvals.jsonl"):
            ok, why = verify(d / f)
            say(f"  {d.name}/{f}: {why}")
    shutil.rmtree(state, ignore_errors=True)
    (ROOT / "outputs" / "demo_transcript.txt").write_text("\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
