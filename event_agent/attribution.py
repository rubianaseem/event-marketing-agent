"""Post-event: campaign associations and touch attribution proposals (deterministic).

Rules (from context/attribution-rules in the starter spec, enforced in code):
  - every attendee/registrant in the configured statuses gets the event campaign proposed;
    HubSpot says campaign influence on a contact cannot be removed once set, so this is gated;
  - first touch is set ONLY if the CRM snapshot shows it empty, never overwritten and never
    guessed for contacts missing from the snapshot;
  - last touch moves to the event only for statuses in last_touch_statuses (attended by default);
  - picklist values must exist in config/picklists.json, otherwise the artifact is blocked;
  - native HubSpot traffic-source, lifecycle and owner properties are never written.
"""
from __future__ import annotations

from datetime import datetime

from .dedupe import classify_domain
from .ingest import email_valid
from .util import parse_dt

PRIORITY = {"attended": 3, "registered_no_show": 2, "cancelled": 1, "unknown": 0}
ATTR_FIELDS = ["record_id", "email", "match", "event_attendance_status", "event_source_detail",
               "first_touch_source", "first_touch_campaign", "last_touch_source", "last_touch_campaign", "change_summary"]
ASSOC_FIELDS = ["record_id", "email", "match", "campaign_id", "campaign_name", "status", "reason"]


class PicklistError(Exception):
    pass


def dedupe_attendance(rows: list[dict]) -> tuple[list[dict], int]:
    best: dict[str, dict] = {}
    dupes = 0
    for r in rows:
        k = r["email"] or f"row{r['row']}"
        if k in best:
            dupes += 1
            if PRIORITY[r["status"]] > PRIORITY[best[k]["status"]]:
                best[k] = r
        else:
            best[k] = r
    return list(best.values()), dupes


def decide(rows: list[dict], crm: dict, event: dict, policy: dict, invited_new: dict) -> list[dict]:
    pol = policy["attribution"]
    event_day = parse_dt(event["event_date"]).date()
    out = []
    for a in rows:
        d = {"row": a["row"], "email": a["email"], "status": a["status"], "walk_in": a["walk_in"],
             "associate": False, "attr": None, "decision": None, "reasons": []}
        out.append(d)
        if not email_valid(a["email"]):
            d["decision"] = "REVIEW_INVALID_EMAIL"; continue
        if classify_domain(a["email"], policy) == "internal":
            d["decision"] = "EXCLUDE_INTERNAL"; continue
        if a["status"] == "unknown":
            d["decision"] = "REVIEW_UNKNOWN_STATUS"; d["reasons"].append(f"status_raw:{a['status_raw']}"); continue
        if a["status"] == "cancelled":
            d["decision"] = "NO_ACTION_CANCELLED"; continue
        if a["checked_in_at"]:
            ts = parse_dt(a["checked_in_at"])
            if ts is None or ts.date() != event_day:
                d["decision"] = "REVIEW_CHECKIN_DATE"; d["reasons"].append(f"checked_in_at:{a['checked_in_at']}"); continue
        c = crm["contacts"].get(a["email"])
        if c:
            d["match"], d["record_id"] = "crm_email", c["record_id"]
        elif a["email"] in invited_new:
            d["match"], d["record_id"], c = "released_invite_import", "", {}
        else:
            d["decision"] = "REVIEW_UNKNOWN_ATTENDEE"
            d["reasons"].append("walk_in" if a["walk_in"] else "not_in_crm_or_invite_list")
            continue
        if a["status"] in pol["associate_statuses"]:
            if event["hubspot_campaign_id"] in c.get("campaign_set", set()):
                d["reasons"].append("already_associated")
            else:
                d["associate"] = True
        changes = {"event_attendance_status": "Attended" if a["status"] == "attended" else "No show",
                   "event_source_detail": event["event_source_value"]}
        summary = ["attendance_status"]
        if d["match"] == "released_invite_import":
            # Not in the CRM snapshot, so we cannot prove first touch is empty: never guess.
            summary.append("first_touch_unverified_reexport_crm")
        elif not (c.get("first_touch_source") or "").strip():
            changes["first_touch_source"] = event["touch_source_value"]
            changes["first_touch_campaign"] = event["event_source_value"]
            summary.append("first_touch_set_was_empty")
        else:
            summary.append("first_touch_preserved")
        if a["status"] in pol["last_touch_statuses"]:
            changes["last_touch_source"] = event["touch_source_value"]
            changes["last_touch_campaign"] = event["event_source_value"]
            summary.append("last_touch_event")
        d["attr"] = changes
        d["decision"] = "PROPOSE"
        d["reasons"] += summary
    return out


def validate_picklists(decisions: list[dict], picklists: dict) -> list[str]:
    errors = []
    for d in decisions:
        for prop, val in (d.get("attr") or {}).items():
            if prop in picklists and val not in picklists[prop]:
                errors.append(f"row {d['row']}: {prop}='{val}' is not an allowed picklist value")
    return errors


def association_rows(decisions: list[dict], event: dict) -> list[dict]:
    return [{"record_id": d.get("record_id", ""), "email": d["email"], "match": d.get("match", ""),
             "campaign_id": event["hubspot_campaign_id"], "campaign_name": event["hubspot_campaign_name"],
             "status": d["status"], "reason": "event_" + d["status"]}
            for d in decisions if d["associate"]]


def attribution_rows(decisions: list[dict], policy: dict) -> list[dict]:
    forbidden = set(policy["attribution"]["never_write_properties"])
    bad = forbidden & set(ATTR_FIELDS)
    if bad:  # defensive: a config or code change must never add native properties to the file
        raise PicklistError(f"forbidden properties in attribution file: {sorted(bad)}")
    rows = []
    for d in decisions:
        if d["decision"] != "PROPOSE":
            continue
        a = d["attr"]
        rows.append({"record_id": d.get("record_id", ""), "email": d["email"], "match": d.get("match", ""),
                     **{k: a.get(k, "") for k in ATTR_FIELDS[3:9]},
                     "change_summary": ";".join(r for r in d["reasons"] if r != "already_associated")})
    return rows
