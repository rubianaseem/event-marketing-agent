"""Orchestration: plan -> (approve + release enrichment request) -> enrich -> build -> attribute.

Each stage writes into one run directory. State between stages is a checkpoint file (state.json)
plus input-file hashes; `build` refuses to continue if an input changed after `plan`. Re-running a
stage with the same inputs reproduces the same artifacts (replay). Nothing here writes to a CRM,
calls Clay or sends anything.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import attribution as attr_mod
from . import enrichment as enr
from .audit import AuditLog
from .dedupe import dedupe_in_file, match_crm
from .ingest import load_attendance, load_candidates, load_crm, load_json
from .personalize import draft_for, render_drafts
from .targeting import DECISION_HELP, REVIEW_DECISIONS, allocate, gate_post, gate_pre, missing_fields
from .util import iso, parse_dt, sha256_bytes, sha256_file, write_csv


class PipelineError(Exception):
    pass


IMPORT_FIELDS = ["Record ID", "Email", "First Name", "Last Name", "Job Title", "Company Name",
                 "Company Domain Name", "Country/Region", "event_attendance_status", "event_invite_tier", "import_action"]
TARGET_FIELDS = ["row", "merged_rows", "first_name", "last_name", "email", "company", "title", "source",
                 "decision", "reasons", "tier", "score", "rank", "company_match", "crm_record_id",
                 "enrichment_status", "dq"]
REVIEW_FIELDS = ["row", "email", "company", "decision", "why", "suggested_action", "detail"]
SUGGESTED = {
    "HOLD_OWNER_APPROVAL": "Ask the deal owner (draft in owner_requests.md). Add to a later run if they agree.",
    "HOLD_CONSENT": "Decide lawful basis for a personal address, or ask the person to register via the form.",
    "HOLD_MISSING_DATA": "Fill country/size by hand or leave out.",
    "HOLD_NEEDS_ENRICHMENT": "Approve and run the enrichment request, then run build again.",
    "REVIEW_POSSIBLE_DUPLICATE": "Merge in HubSpot first or confirm they are different people.",
    "REVIEW_AMBIGUOUS_COMPANY": "Pick the right company record.",
    "QUARANTINE_INVALID_EMAIL": "Fix the address at source (suggested domain in detail) and re-run.",
    "QUARANTINE_UNTRUSTED_TEXT": "Read the source row yourself. The text was not passed to any model.",
    "DESCRIPTION_QUARANTINED": "Enrichment text was dropped as untrusted; decision used structured fields only.",
}


def pseudo(email: str) -> str:
    return "e_" + sha256_bytes(("event-agent|" + (email or "")).encode())[:16]


def _now(as_of: str | None) -> datetime:
    from datetime import timezone
    return parse_dt(as_of) if as_of else datetime.now(timezone.utc)


def _inputs(paths: dict, run_dir: Path | None = None) -> dict:
    """Input hashes. Paths are stored relative to the run directory so manifests are portable."""
    out = {}
    for k, v in paths.items():
        rel = os.path.relpath(Path(v).resolve(), Path(run_dir).resolve()) if run_dir else str(v)
        out[k] = {"path": rel, "sha256": sha256_file(v)}
    return out


def _write_manifest(run_dir: Path, m: dict) -> None:
    (run_dir / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True))


def _add_artifact(run_dir: Path, m: dict, name: str, approvable: bool) -> None:
    m["artifacts"][name] = {"sha256": sha256_file(run_dir / name), "approvable": approvable}


def _manifest_base(run_id, event, policy, crm_manifest, now) -> dict:
    exported = parse_dt(crm_manifest.get("exported_at"))
    age = None if exported is None else round((now - exported).total_seconds() / 3600, 1)
    return {"run_id": run_id, "event_id": event["event_id"], "policy_version": policy["policy_version"],
            "exported_at": crm_manifest.get("exported_at"), "as_of": iso(now), "snapshot_age_hours": age,
            "stale": age is None or age > policy["snapshot_max_age_hours"], "artifacts": {},
            "writes_performed": "none (local files only)"}


def _trace(run_dir: Path, now: datetime) -> AuditLog:
    return AuditLog(run_dir / "decisions.jsonl", clock=lambda: now)


def _jsonable(r: dict) -> dict:
    keep = {k: v for k, v in r.items() if k not in ("crm_contact", "notes_screen")}
    keep["crm_record_id"] = (r.get("crm_contact") or {}).get("record_id", "")
    keep["notes_status"] = (r.get("notes_screen") or {}).get("status", "")
    return keep


# ---------------------------------------------------------------- plan
def plan(paths: dict, run_dir, config_dir, as_of: str | None = None, run_id: str | None = None) -> dict:
    run_dir = Path(run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    cfg = Path(config_dir)
    event, policy = load_json(cfg / "event.json"), load_json(cfg / "policy.json")
    now = _now(as_of)
    crm_manifest = load_json(paths["crm_manifest"])
    run_id = run_id or "run_" + sha256_bytes(json.dumps({k: v["sha256"] for k, v in _inputs(paths).items()}, sort_keys=True).encode()
                                             + policy["policy_version"].encode())[:12]
    m = _manifest_base(run_id, event, policy, crm_manifest, now)
    m["inputs"] = _inputs(paths, run_dir)
    trace = _trace(run_dir, now)
    trace.append({"event": "plan_started", "run_id": run_id, "policy_version": policy["policy_version"],
                  "snapshot_age_hours": m["snapshot_age_hours"], "stale": m["stale"]})
    crm = load_crm(paths["crm_contacts"], paths["crm_companies"], paths["crm_deals"], paths["owners"], paths["suppression"])
    rows = load_candidates(paths["candidates"])
    rows, merges = dedupe_in_file(rows)
    for mg in merges:
        trace.append({"event": "merged_in_file", **mg})
    for r in rows:
        match_crm(r, crm, policy)
        gate_pre(r, crm, event, policy)
        trace.append({"event": "pre_gate", "row": r["row"], "subject": pseudo(r["email"]),
                      "decision": r["decision"] or "continue", "reasons": r["reasons"],
                      "company_match": r["company_match"]})
    request = enr.plan(rows, policy, event, missing_fields)
    (run_dir / "enrichment_request.json").write_text(json.dumps(request, indent=2, sort_keys=True))
    _add_artifact(run_dir, m, "enrichment_request.json", approvable=bool(request["items"]))
    trace.append({"event": "enrichment_planned", "domains": request["domains"],
                  "estimated_credits": request["estimated_credits_total"], "escalation_required": request["escalation_required"]})
    (run_dir / "state.json").write_text(json.dumps({"stage": "planned", "rows": [_jsonable(r) for r in rows],
                                                     "merges": merges}, indent=1, sort_keys=True, default=list))
    _write_manifest(run_dir, m)
    if os.environ.get("EMA_FAIL_AFTER") == "plan":
        raise PipelineError("simulated crash after plan (EMA_FAIL_AFTER=plan)")
    return {"run_dir": str(run_dir), "run_id": run_id, "request": request, "stale": m["stale"]}


# ---------------------------------------------------------------- enrich
def enrich(run_dir, enrichment_source_path, state_dir, config_dir, provider=None, sleep=None,
           crash_after=None, as_of: str | None = None) -> dict:
    run_dir = Path(run_dir)
    policy = load_json(Path(config_dir) / "policy.json")
    from .ingest import load_enrichment_source
    provider = provider or enr.MockEnrichmentProvider(load_enrichment_source(enrichment_source_path))
    trace = _trace(run_dir, _now(as_of))
    kw = {"log": trace.append, "crash_after": crash_after}
    if sleep:
        kw["sleep"] = sleep
    try:
        out = enr.run(run_dir, provider, Path(state_dir) / "enrichment_cache.json", policy, **kw)
    except enr.EnrichmentGateError as exc:
        trace.append({"event": "enrichment_refused", "why": str(exc)})
        raise
    trace.append({"event": "enrichment_done", "provider_calls": out["provider_calls"],
                  "credits_charged_this_run": out["credits_charged_this_run"],
                  "statuses": dict(Counter(v["status"] for v in out["results"].values()))})
    return out


# ---------------------------------------------------------------- build
def build(run_dir, config_dir, state_dir, client=None, as_of: str | None = None, llm_sleep=None) -> dict:
    run_dir = Path(run_dir)
    cfg = Path(config_dir)
    event, policy, picklists = load_json(cfg / "event.json"), load_json(cfg / "policy.json"), load_json(cfg / "picklists.json")
    now = _now(as_of)
    st_path = run_dir / "state.json"
    if not st_path.exists():
        raise PipelineError("no state.json: run `plan` first")
    m = json.loads((run_dir / "manifest.json").read_text())
    paths = {k: str((run_dir / v["path"]).resolve()) for k, v in m["inputs"].items()}
    for k, v in m["inputs"].items():
        if not Path(paths[k]).exists() or sha256_file(paths[k]) != v["sha256"]:
            raise PipelineError(f"input '{k}' changed since plan; run plan again")
    crm = load_crm(paths["crm_contacts"], paths["crm_companies"], paths["crm_deals"], paths["owners"], paths["suppression"])
    state = json.loads(st_path.read_text())
    rows = state["rows"]
    for r in rows:
        r["crm_contact"] = crm["contacts"].get(r["email"]) if r.get("email_valid") else None
        r.setdefault("dq", [])
    results = None
    res_path = run_dir / "enrichment_results.json"
    if res_path.exists():
        results = json.loads(res_path.read_text())
        if results.get("request_sha256") != m["artifacts"]["enrichment_request.json"]["sha256"]:
            raise PipelineError("enrichment results were produced for a different request")
    enr.apply(rows, results)
    trace = _trace(run_dir, now)
    for r in rows:
        gate_post(r, crm, event, policy)
    allocate(rows, event, policy)
    ledger_path = Path(state_dir) / "invite_ledger.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    for r in rows:
        if r["decision"] == "INVITE":
            key = sha256_bytes(f"{event['event_id']}|{r['email']}".encode())[:24]
            prev = ledger.get(key)
            if prev and prev != m["run_id"]:
                r["decision"], r["reasons"] = "ALREADY_PROPOSED", ["already_proposed_in:" + prev]
            else:
                ledger[key] = m["run_id"]
        trace.append({"event": "decision", "row": r["row"], "subject": pseudo(r["email"]), "decision": r["decision"],
                      "reasons": r["reasons"], "tier": r.get("tier"), "score": r.get("score"),
                      "enrichment_status": r.get("enrichment_status")})
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=1, sort_keys=True))
    invites = sorted([r for r in rows if r["decision"] == "INVITE"], key=lambda r: r["rank"])
    for r in invites:
        r["draft"] = draft_for(r, event, client=client, log=trace.append, sleep=llm_sleep)
        trace.append({"event": "draft", "subject": pseudo(r["email"]), "source": r["draft"]["source"],
                      "validator": r["draft"]["validator"]})
    # artifacts
    target = []
    for r in sorted(rows, key=lambda r: r["row"]):
        target.append({**{k: r.get(k, "") for k in TARGET_FIELDS},
                       "merged_rows": ";".join(map(str, r["merged_rows"])), "reasons": ";".join(r["reasons"]),
                       "crm_record_id": (r.get("crm_contact") or {}).get("record_id", ""), "dq": ";".join(r["dq"])})
    write_csv(run_dir / "target_list.csv", target, TARGET_FIELDS)
    review = []
    for r in sorted(rows, key=lambda r: r["row"]):
        if r["decision"] in REVIEW_DECISIONS:
            review.append({"row": r["row"], "email": r["email"], "company": r["company"], "decision": r["decision"],
                           "why": DECISION_HELP[r["decision"]], "suggested_action": SUGGESTED[r["decision"]],
                           "detail": ";".join(r["reasons"][1:])})
        if "enrichment_description_quarantined" in r["dq"]:
            review.append({"row": r["row"], "email": r["email"], "company": r["company"], "decision": r["decision"],
                           "why": "Enrichment description contained instruction-like text.",
                           "suggested_action": SUGGESTED["DESCRIPTION_QUARANTINED"], "detail": "enrichment_description_quarantined"})
    write_csv(run_dir / "review_queue.csv", review, REVIEW_FIELDS)
    imp = []
    for r in invites:
        comp = crm["companies"].get(r.get("company_id") or "", {})
        work = r.get("domain_class") == "work"
        imp.append({"Record ID": (r.get("crm_contact") or {}).get("record_id", ""), "Email": r["email"],
                    "First Name": r["first_name"], "Last Name": r["last_name"], "Job Title": r["title"],
                    "Company Name": comp.get("name") or r["company"],
                    "Company Domain Name": comp.get("domain") or (r["email"].split("@")[1] if work else ""),
                    "Country/Region": r["country"], "event_attendance_status": "Invited",
                    "event_invite_tier": r["tier"], "import_action": "update" if r.get("crm_contact") else "create"})
    bad = [x for x in imp if x["event_attendance_status"] not in picklists["event_attendance_status"]]
    if bad:
        raise PipelineError("import uses a picklist value that is not allowed")
    write_csv(run_dir / "hubspot_import_proposed.csv", imp, IMPORT_FIELDS)
    (run_dir / "invite_drafts.md").write_text(render_drafts(invites, event))
    (run_dir / "owner_requests.md").write_text(_owner_requests(rows, event))
    counts = Counter(r["decision"] for r in rows)
    (run_dir / "brief.md").write_text(_brief(event, m, counts, rows, invites, results, review))
    for name, appr in [("target_list.csv", False), ("review_queue.csv", False), ("hubspot_import_proposed.csv", bool(imp)),
                       ("invite_drafts.md", False), ("owner_requests.md", False), ("brief.md", False)]:
        _add_artifact(run_dir, m, name, appr)
    m["counts"] = dict(sorted(counts.items()))
    _write_manifest(run_dir, m)
    state["stage"] = "built"
    st_path.write_text(json.dumps(state, indent=1, sort_keys=True, default=list))
    trace.append({"event": "build_done", "counts": m["counts"], "import_rows": len(imp), "review_rows": len(review)})
    return {"counts": dict(counts), "invites": len(invites), "review": len(review), "import_rows": len(imp), "stale": m["stale"]}


def _owner_requests(rows, event) -> str:
    out = [f"# Owner requests: {event['name']}", "", "DRAFTS. The agent sends nothing. Post or forward these yourself.", ""]
    holds = [r for r in rows if r["decision"] == "HOLD_OWNER_APPROVAL"]
    fyi = [r for r in rows if r["decision"] == "INVITE" and r.get("deal_context")]
    if holds:
        out.append("## Needs owner approval before inviting")
        for r in holds:
            for d in r["deal_context"]:
                if d["team"] not in event["owning_teams"]:
                    out.append(f"- To {d['owner_name']} ({d['team']}): {r['first_name']} {r['last_name']} at {r['company']} "
                               f"is on the {event['name']} list and has open deal {d['deal_id']}. OK to invite? Reply yes/no.")
        out.append("")
    if fyi:
        out.append("## FYI to owning-team deal owners (invite proposed)")
        for r in fyi:
            for d in r["deal_context"]:
                out.append(f"- To {d['owner_name']}: {r['first_name']} {r['last_name']} at {r['company']} is proposed for the invite list (open deal {d['deal_id']}).")
        out.append("")
    if not holds and not fyi:
        out.append("No owner requests in this run.")
    return "\n".join(out) + "\n"


def _brief(event, m, counts, rows, invites, results, review) -> str:
    lines = [f"# Event targeting brief: {event['name']} ({event['event_date']})", "",
             f"Run {m['run_id']}, policy {m['policy_version']}, CRM snapshot {m['exported_at']} "
             f"({m['snapshot_age_hours']} h old{', STALE: cannot be approved' if m['stale'] else ''}).", "",
             f"Candidates after in-file dedupe: {len(rows)}. Proposed invites: {len(invites)} of capacity {event['invite_capacity']}. "
             f"Review queue: {len(review)} rows.", "", "## Decisions", ""]
    for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- {k}: {v}")
    if results:
        st = Counter(v["status"] for v in results["results"].values())
        lines += ["", "## Enrichment (mock provider)", "",
                  f"- domains looked up: {len(results['results'])} ({', '.join(f'{k} {v}' for k, v in sorted(st.items()))})",
                  f"- credits charged this run (your per-field assumption): {results['credits_charged_this_run']}"]
    else:
        lines += ["", "## Enrichment", "", "- not run in this build; affected rows are HOLD_NEEDS_ENRICHMENT"]
    lines += ["", "## Proposed invites (ranked)", ""]
    for r in invites:
        lines.append(f"{r['rank']}. {r['first_name']} {r['last_name']}, {r['title']}, {r['company']} (tier {r['tier']}, score {r['score']})")
    lines += ["", "## Next step", "",
              "Review hubspot_import_proposed.csv and review_queue.csv. Approve the import with its SHA-256 "
              "(`python agent.py show` then `approve`). Release copies it to released/. You import it in HubSpot yourself."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- attribute
def attribute(paths: dict, run_dir, config_dir, plan_run: str | None = None, as_of: str | None = None) -> dict:
    run_dir = Path(run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    cfg = Path(config_dir)
    event, policy, picklists = load_json(cfg / "event.json"), load_json(cfg / "policy.json"), load_json(cfg / "picklists.json")
    now = _now(as_of)
    crm_manifest = load_json(paths["crm_manifest"])
    run_id = "attr_" + sha256_bytes(json.dumps({k: v["sha256"] for k, v in _inputs(paths).items()}, sort_keys=True).encode())[:12]
    m = _manifest_base(run_id, event, policy, crm_manifest, now)
    m["inputs"] = _inputs(paths, run_dir)
    trace = _trace(run_dir, now)
    crm = load_crm(paths["crm_contacts"], paths["crm_companies"], paths["crm_deals"], paths["owners"], paths["suppression"])
    invited_new = {}
    if plan_run:
        pr = Path(plan_run)
        rel, meta = pr / "released" / "hubspot_import_proposed.csv", pr / "released" / "hubspot_import_proposed.csv.release.json"
        if rel.exists() and meta.exists() and sha256_file(rel) == json.loads(meta.read_text())["sha256"]:
            from .util import read_csv
            invited_new = {x["Email"]: x for x in read_csv(rel) if x.get("import_action") == "create"}
            m["plan_run"] = {"path": os.path.relpath(pr.resolve(), run_dir.resolve()), "released_import_sha256": sha256_file(rel)}
        else:
            trace.append({"event": "plan_run_ignored", "why": "import not released or hash mismatch"})
    att = load_attendance(paths["attendance"])
    att, dupes = attr_mod.dedupe_attendance(att)
    decisions = attr_mod.decide(att, crm, event, policy, invited_new)
    for d in decisions:
        trace.append({"event": "attribution_decision", "row": d["row"], "subject": pseudo(d["email"]),
                      "decision": d["decision"], "associate": d["associate"], "reasons": d["reasons"]})
    errors = attr_mod.validate_picklists(decisions, picklists)
    assoc = attr_mod.association_rows(decisions, event)
    write_csv(run_dir / "campaign_associations_proposed.csv", assoc, attr_mod.ASSOC_FIELDS)
    review = [{"row": d["row"], "email": d["email"], "decision": d["decision"], "detail": ";".join(d["reasons"])}
              for d in decisions if d["decision"].startswith("REVIEW")]
    write_csv(run_dir / "attribution_review.csv", review, ["row", "email", "decision", "detail"])
    _add_artifact(run_dir, m, "attribution_review.csv", False)
    if errors:
        (run_dir / "blockers.md").write_text("# Blocked\n\nAttribution file not produced:\n\n" + "\n".join(f"- {e}" for e in errors) + "\n")
        trace.append({"event": "attribution_blocked", "errors": len(errors)})
        _add_artifact(run_dir, m, "campaign_associations_proposed.csv", False)
        m["blocked"] = errors
    else:
        rows = attr_mod.attribution_rows(decisions, policy)
        write_csv(run_dir / "attribution_updates_proposed.csv", rows, attr_mod.ATTR_FIELDS)
        _add_artifact(run_dir, m, "attribution_updates_proposed.csv", bool(rows))
        _add_artifact(run_dir, m, "campaign_associations_proposed.csv", bool(assoc))
    stats = {
        "attendance_rows_after_dedupe": len(att), "duplicate_rows_merged": dupes,
        "associations_proposed": len(assoc),
        "already_associated_skipped": sum("already_associated" in d["reasons"] for d in decisions),
        "first_touch_set": sum("first_touch_set_was_empty" in d["reasons"] for d in decisions),
        "first_touch_preserved": sum("first_touch_preserved" in d["reasons"] for d in decisions),
        "first_touch_unverified": sum("first_touch_unverified_reexport_crm" in d["reasons"] for d in decisions),
        "last_touch_event": sum("last_touch_event" in d["reasons"] for d in decisions),
        "review": len(review), "blocked": bool(errors),
        "decisions": dict(Counter(d["decision"] for d in decisions)),
    }
    m["stats"] = stats
    (run_dir / "attribution_brief.md").write_text(_attr_brief(event, m, stats, review))
    _add_artifact(run_dir, m, "attribution_brief.md", False)
    _write_manifest(run_dir, m)
    trace.append({"event": "attribution_done", **{k: v for k, v in stats.items() if k != "decisions"}})
    return stats


def _attr_brief(event, m, s, review) -> str:
    lines = [f"# Post-event attribution brief: {event['name']}", "",
             f"Run {m['run_id']}, CRM snapshot {m['exported_at']} ({m['snapshot_age_hours']} h old).", "",
             f"- attendance rows after dedupe: {s['attendance_rows_after_dedupe']} ({s['duplicate_rows_merged']} duplicate rows merged)",
             f"- campaign associations proposed: {s['associations_proposed']} (already associated, skipped: {s['already_associated_skipped']})",
             f"- first touch set because it was empty: {s['first_touch_set']}; existing first touch preserved: {s['first_touch_preserved']}",
             f"- first touch left blank because the contact is not in the CRM snapshot yet (re-export after import, then re-run): {s['first_touch_unverified']}",
             f"- last touch moved to the event (attended only): {s['last_touch_event']}",
             f"- rows for review: {s['review']}", ""]
    if s["blocked"]:
        lines.append("BLOCKED: see blockers.md. No attribution file was produced.")
    lines += ["HubSpot note: campaign influence on a contact cannot be removed once set, so associations are approval-gated.",
              "Native Original/Latest Traffic Source, lifecycle stage and owner are never written by this agent."]
    return "\n".join(lines) + "\n"
