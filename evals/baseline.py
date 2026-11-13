"""Naive baseline: the 'pull a list and import it' process the agent replaces.

- keeps rows whose country is in the event regions or blank;
- drops rows whose raw email (not normalised) is on the suppression list;
- matches the CRM on the raw email string (so case/whitespace differences create duplicates);
- no customer, deal-owner, consent, persona, segment, competitor/internal or free-text checks;
- enriches every row with a missing field (no gating first);
- post-event: associates every attendance row, overwrites first touch, sets last touch for everyone.
"""
from event_agent.ingest import COUNTRY_MAP


def target(cand: dict, crm: dict, event: dict) -> dict:
    raw = cand["email"]
    if "@" not in raw or "." not in raw.split("@")[-1]:
        return {"decision": "QUARANTINE_INVALID_EMAIL", "creates_duplicate": False}
    if raw in crm["suppression"]:
        return {"decision": "EXCLUDE_SUPPRESSED", "creates_duplicate": False}
    ctry = COUNTRY_MAP.get(cand["country"].strip().lower(), cand["country"].strip().upper())
    if ctry and ctry not in event["regions"]:
        return {"decision": "EXCLUDE_REGION", "creates_duplicate": False}
    exists_raw = raw in crm["contacts"]
    exists_norm = raw.strip().lower() in crm["contacts"]
    return {"decision": "INVITE", "creates_duplicate": exists_norm and not exists_raw}


def attribute(case: dict) -> dict:
    return {"decision": "PROPOSE", "associate": True, "first_touch": "set", "last_touch": True}
