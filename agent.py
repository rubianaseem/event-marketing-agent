"""Command line for the event marketing agent. Local files only: no CRM writes, sends or Clay calls.

  python agent.py plan      --out outputs/run_demo              # pre-gates, dedupe, enrichment request
  python agent.py show      --run-dir outputs/run_demo           # artifacts and SHA-256 to review
  python agent.py approve   --run-dir ... --artifact enrichment_request.json --sha256 <hash> --approver rubia
  python agent.py release   --run-dir ... --artifact enrichment_request.json
  python agent.py enrich    --run-dir ...                        # mock provider, only after release
  python agent.py build     --run-dir ...                        # targeting, import proposal, drafts
  python agent.py attribute --out outputs/attr_demo --plan-run outputs/run_demo
  python agent.py verify    --run-dir ...                        # check hash-chained logs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from event_agent import approval, pipeline
from event_agent.audit import verify
from event_agent.ingest import load_json
from event_agent.llm import get_client

HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "candidates": "sample_candidates.csv", "crm_contacts": "sample_crm_contacts.csv",
    "crm_companies": "sample_crm_companies.csv", "crm_deals": "sample_crm_deals.csv", "owners": "sample_owners.csv",
    "suppression": "sample_suppression.csv", "crm_manifest": "sample_export_manifest.json",
}


def _paths(a, keys):
    return {k: str(HERE / getattr(a, k)) if not Path(getattr(a, k)).is_absolute() else getattr(a, k) for k in keys}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["plan", "show", "approve", "release", "enrich", "build", "attribute", "verify"])
    p.add_argument("--out"); p.add_argument("--run-dir"); p.add_argument("--config", default=str(HERE / "config"))
    p.add_argument("--state", default=str(HERE / "state")); p.add_argument("--as-of")
    for k, v in DEFAULTS.items():
        p.add_argument("--" + k.replace("_", "-"), dest=k, default=v)
    p.add_argument("--enrichment-source", default="sample_enrichment.csv")
    p.add_argument("--attendance", default="sample_attendance.csv"); p.add_argument("--plan-run")
    p.add_argument("--artifact"); p.add_argument("--sha256"); p.add_argument("--approver")
    a = p.parse_args(argv)
    cfg = Path(a.config)
    try:
        if a.command == "plan":
            res = pipeline.plan(_paths(a, DEFAULTS), a.out or "outputs/run_latest", cfg, as_of=a.as_of)
            r = res["request"]
            print(f"planned {res['run_id']} -> {res['run_dir']}")
            print(f"enrichment request: {r['domains']} domains, estimated {r['estimated_credits_total']} credits "
                  f"(your per-field assumption){'; ESCALATION REQUIRED' if r['escalation_required'] else ''}")
            print("next: review enrichment_request.json, approve + release it, then `enrich`; or run `build` to skip enrichment")
        elif a.command == "show":
            m = json.loads((Path(a.run_dir) / "manifest.json").read_text())
            print(f"run {m['run_id']}  snapshot {m['exported_at']}  stale={m['stale']}")
            for name, art in sorted(m["artifacts"].items()):
                print(f"  {'APPROVABLE ' if art['approvable'] else '           '}{name}  {art['sha256']}")
        elif a.command == "approve":
            now = pipeline._now(a.as_of)
            rec = approval.approve(a.run_dir, a.artifact, a.sha256, a.approver or "", now,
                                   load_json(cfg / "approvers.json"), load_json(cfg / "policy.json"))
            print(f"approved {a.artifact} by {rec['approver_name']} until {rec['expires_at']}")
        elif a.command == "release":
            out = approval.release(a.run_dir, a.artifact, pipeline._now(a.as_of), load_json(cfg / "policy.json"))
            print(f"released -> {out}")
        elif a.command == "enrich":
            out = pipeline.enrich(a.run_dir, HERE / a.enrichment_source, a.state, cfg, as_of=a.as_of)
            print(f"enrichment: {len(out['results'])} domains, provider calls {out['provider_calls']}, "
                  f"credits charged this run {out['credits_charged_this_run']}")
        elif a.command == "build":
            res = pipeline.build(a.run_dir, cfg, a.state, client=get_client(), as_of=a.as_of)
            print(json.dumps(res, indent=2, sort_keys=True))
        elif a.command == "attribute":
            keys = ["crm_contacts", "crm_companies", "crm_deals", "owners", "suppression", "crm_manifest", "attendance"]
            res = pipeline.attribute(_paths(a, keys), a.out or "outputs/attr_latest", cfg, plan_run=a.plan_run, as_of=a.as_of)
            print(json.dumps(res, indent=2, sort_keys=True))
        elif a.command == "verify":
            rd = Path(a.run_dir)
            for f in ("decisions.jsonl", "approvals.jsonl"):
                if (rd / f).exists():
                    ok, why = verify(rd / f)
                    print(f"{f}: {why}")
                    if not ok:
                        return 1
    except (approval.ApprovalError, pipeline.PipelineError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        from event_agent.enrichment import EnrichmentGateError
        if isinstance(exc, EnrichmentGateError):
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
