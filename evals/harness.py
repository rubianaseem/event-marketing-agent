"""Runs cases_v1 through the agent's deterministic decision path and the naive baseline."""
from __future__ import annotations

import csv
import io
import json
import tempfile
from pathlib import Path

from event_agent import attribution as attr_mod
from event_agent import enrichment as enr
from event_agent.dedupe import match_crm
from event_agent.ingest import load_attendance, load_candidates, load_crm, load_json
from event_agent.targeting import gate_post, gate_pre, missing_fields

from . import baseline

ROOT = Path(__file__).resolve().parents[1]
HARD = {"EXCLUDE_SUPPRESSED", "EXCLUDE_CUSTOMER", "HOLD_OWNER_APPROVAL", "HOLD_CONSENT", "EXCLUDE_COMPETITOR",
        "EXCLUDE_INTERNAL", "QUARANTINE_UNTRUSTED_TEXT", "QUARANTINE_INVALID_EMAIL", "REVIEW_POSSIBLE_DUPLICATE"}
FIELDS = ["source", "first_name", "last_name", "email", "company", "title", "country", "employees", "industry",
          "marketing_opt_in", "notes"]


def _crm():
    return load_crm(ROOT / "sample_crm_contacts.csv", ROOT / "sample_crm_companies.csv", ROOT / "sample_crm_deals.csv",
                    ROOT / "sample_owners.csv", ROOT / "sample_suppression.csv")


def _row(cand: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader(); w.writerow(cand)
    return load_candidates(fh.name)[0]


def agent_target(case: dict, crm, event, policy) -> dict:
    r = _row(case["candidate"])
    match_crm(r, crm, policy)
    gate_pre(r, crm, event, policy)
    planned = False
    if not r["decision"] and missing_fields(r, policy):
        planned = True
        r["enrichment_status"] = "planned"
        dom = r["email"].split("@")[1]
        res = {"results": {dom: case["enrichment"]}} if "enrichment" in case else None
        enr.apply([r], res)
    gate_post(r, crm, event, policy)
    return {"decision": r["decision"] or "INVITE", "enrichment_planned": planned,
            "creates_duplicate": False, "description_quarantined": "enrichment_description_quarantined" in r["dq"]}


def run_targeting(cases, crm, event, policy):
    out = []
    for c in cases:
        a = agent_target(c, crm, event, policy)
        b = baseline.target(c["candidate"], crm, event)
        out.append({"id": c["id"], "gold": c["gold"], "tags": c["tags"], "agent": a, "baseline": b})
    return out


def score_targeting(res, who):
    n = len(res)
    exact = sum(r[who]["decision"] == r["gold"] for r in res)
    tp = sum(r[who]["decision"] == "INVITE" and r["gold"] == "INVITE" for r in res)
    pred = sum(r[who]["decision"] == "INVITE" for r in res)
    gold = sum(r["gold"] == "INVITE" for r in res)
    viol = [r["id"] for r in res if r[who]["decision"] == "INVITE" and r["gold"] in HARD]
    viol_designed = [i for i in viol if "held_out" not in next(x["tags"] for x in res if x["id"] == i)]
    dup = sum(bool(r[who].get("creates_duplicate")) for r in res)
    misses = [f"{r['id']}:{r['gold']}->{r[who]['decision']}" for r in res if r[who]["decision"] != r["gold"]]
    return {"cases": n, "exact_accuracy": round(exact / n, 3), "invite_precision": round(tp / pred, 3) if pred else None,
            "invite_recall": round(tp / gold, 3) if gold else None, "hard_control_violations": len(viol),
            "violation_ids": viol, "hard_control_violations_excluding_held_out": len(viol_designed), "duplicate_creates": dup, "misses": misses}


def run_attribution(cases, event, policy):
    crm = _crm()
    res = []
    for i, c in enumerate(cases):
        email = c.get("email") or f"attendee{i}@attrco.example"
        crm_c = dict(crm)
        contacts = dict(crm["contacts"])
        if c["crm"] is not None:
            contacts[email] = {"record_id": f"9{i:03d}", "email": email, "first_touch_source": c["crm"]["first_touch_source"],
                               "campaign_set": {x for x in c["crm"]["campaign_ids"].split(";") if x}}
        crm_c["contacts"] = contacts
        buf = "email,name,status,checked_in_at,company\n" + f"{email},X,{c['status']},{c['checked_in_at']},Co\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(buf)
        att = load_attendance(fh.name)
        d = attr_mod.decide(att, crm_c, event, policy, {})[0]
        ft = "set" if "first_touch_set_was_empty" in d["reasons"] else "preserve" if "first_touch_preserved" in d["reasons"] else "none"
        agent = {"decision": d["decision"], "associate": d["associate"], "first_touch": ft,
                 "last_touch": "last_touch_event" in d["reasons"]}
        res.append({"id": c["id"], "gold": c["gold"], "agent": agent, "baseline": baseline.attribute(c),
                    "crm_first_touch_present": bool(c["crm"] and c["crm"]["first_touch_source"])})
    return res


def score_attribution(res, who):
    n = len(res)
    exact = sum(all(r[who][k] == r["gold"][k] for k in ("decision", "associate", "first_touch", "last_touch")) for r in res)
    overwrites = sum(r[who]["first_touch"] == "set" and r["crm_first_touch_present"] for r in res)
    wrong_assoc = sum(r[who]["associate"] and not r["gold"]["associate"] for r in res)
    missed_assoc = sum(r["gold"]["associate"] and not r[who]["associate"] for r in res)
    lt_wrong = sum(r[who]["last_touch"] != r["gold"]["last_touch"] for r in res)
    return {"cases": n, "all_fields_correct": round(exact / n, 3), "first_touch_overwrites": overwrites,
            "wrong_campaign_associations": wrong_assoc, "missed_associations": missed_assoc, "last_touch_errors": lt_wrong}


def credits_on_sample(event, policy):
    """Enrichment credits the agent requests on sample_candidates.csv vs enriching every incomplete row."""
    from event_agent.dedupe import dedupe_in_file
    crm = _crm()
    rows, _ = dedupe_in_file(load_candidates(ROOT / "sample_candidates.csv"))
    naive_rows = load_candidates(ROOT / "sample_candidates.csv")
    cpf = policy["enrichment"]["credits_per_field"]
    naive = sum(sum(cpf.get(f, 1) for f in missing_fields(r, policy)) for r in naive_rows)
    for r in rows:
        match_crm(r, crm, policy); gate_pre(r, crm, event, policy)
    req = enr.plan(rows, policy, event, missing_fields)
    return {"agent_estimated_credits": req["estimated_credits_total"], "agent_domains": req["domains"],
            "naive_estimated_credits": naive, "naive_rows": sum(bool(missing_fields(r, policy)) for r in naive_rows)}
