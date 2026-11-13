"""Hard gates, segment/persona checks and ranking. Deterministic: no model reads these rules.

Gates run in two passes. The first pass runs BEFORE enrichment so nobody spends enrichment
credits on a contact who would be excluded anyway (suppressed, customer, competitor, out of
region). The second pass runs after enrichment results are applied.
"""
from __future__ import annotations

import re

from .ingest import email_domain
from .untrusted import screen

REQUIRED_FOR_TARGETING = ("country", "employees")

DECISION_HELP = {
    "INVITE": "Eligible and within capacity. Goes into the proposed HubSpot import.",
    "WAITLIST_CAPACITY": "Eligible but below the invite capacity cut-off.",
    "WAITLIST_ACCOUNT_CAP": "Eligible but the account already has the maximum invites.",
    "HOLD_OWNER_APPROVAL": "Open deal owned by a team outside the event's owning teams. Needs that owner's OK.",
    "HOLD_CONSENT": "Personal email address with no recorded opt-in. Needs a lawful basis decision.",
    "HOLD_MISSING_DATA": "Country or company size still unknown after enrichment.",
    "HOLD_NEEDS_ENRICHMENT": "Missing data and enrichment has not been approved/run.",
    "REVIEW_POSSIBLE_DUPLICATE": "Same person may already exist in the CRM under another email.",
    "REVIEW_AMBIGUOUS_COMPANY": "Company name matches more than one CRM company.",
    "QUARANTINE_INVALID_EMAIL": "Email address is not valid.",
    "QUARANTINE_UNTRUSTED_TEXT": "Free text looks like instructions to a model or system.",
    "EXCLUDE_SUPPRESSED": "Suppressed, unsubscribed, opted out or asked not to be emailed.",
    "EXCLUDE_CUSTOMER": "Current customer and the event is not customer-only.",
    "EXCLUDE_NOT_CUSTOMER": "Customer-only event and this account is not a customer.",
    "EXCLUDE_REGION": "Outside the event regions.",
    "EXCLUDE_SEGMENT": "Company size outside the event segments.",
    "EXCLUDE_PERSONA": "Role or seniority outside the event audience.",
    "EXCLUDE_INTERNAL": "Internal address.",
    "EXCLUDE_COMPETITOR": "Competitor domain.",
}
REVIEW_DECISIONS = {"HOLD_OWNER_APPROVAL", "HOLD_CONSENT", "HOLD_MISSING_DATA", "HOLD_NEEDS_ENRICHMENT",
                    "REVIEW_POSSIBLE_DUPLICATE", "REVIEW_AMBIGUOUS_COMPANY", "QUARANTINE_INVALID_EMAIL",
                    "QUARANTINE_UNTRUSTED_TEXT"}


def seniority(title: str, policy: dict) -> int:
    t = (title or "").lower()
    best = -1
    ranks = policy["seniority_rank"]
    for key, val in ranks.items():
        if re.search(r"\b" + re.escape(key) + r"\b", t):
            best = max(best, val)
    if re.search(r"\bhead of\b", t):
        best = max(best, ranks.get("head", 4))
    return best


def persona_match(title: str, event: dict) -> bool:
    t = (title or "").lower()
    return any(re.search(r"\b" + re.escape(p) + r"\b", t) for p in event["target_personas"])


def segment_of(employees: int | None, policy: dict) -> str | None:
    if employees is None:
        return None
    s = policy["segments"]
    if employees <= s["smb_max_employees"]:
        return "smb"
    if employees <= s["mid_market_max_employees"]:
        return "mid_market"
    return "enterprise"


def _decide(r, code, *reasons):
    r["decision"] = code
    r["reasons"] = [code.lower()] + [x for x in reasons if x]
    return r


def gate_pre(r: dict, crm: dict, event: dict, policy: dict) -> dict:
    """First pass. Sets r['decision'] or leaves it None for the next step."""
    r["decision"], r["reasons"] = None, []
    r["notes_screen"] = screen(r["notes"])
    if not r["email_valid"]:
        from .dedupe import suggest_domain
        sug = suggest_domain(r["email"], crm)
        return _decide(r, "QUARANTINE_INVALID_EMAIL", f"suggest:{sug}" if sug else None)
    if r["notes_screen"]["status"] == "quarantined":
        return _decide(r, "QUARANTINE_UNTRUSTED_TEXT", "notes_injection_markers")
    if r["domain_class"] == "internal":
        return _decide(r, "EXCLUDE_INTERNAL")
    if r["domain_class"] == "competitor":
        return _decide(r, "EXCLUDE_COMPETITOR")
    c = r["crm_contact"]
    if r["email"] in crm["suppression"]:
        return _decide(r, "EXCLUDE_SUPPRESSED", f"suppression_list:{crm['suppression'][r['email']]}")
    if c and c["unsubscribed_b"]:
        return _decide(r, "EXCLUDE_SUPPRESSED", "crm_unsubscribed")
    if r["notes_screen"]["status"] == "opt_out":
        return _decide(r, "EXCLUDE_SUPPRESSED", "opt_out_in_notes")
    if r["marketing_opt_in"] is False or (c and c["opt_in_b"] is False):
        return _decide(r, "EXCLUDE_SUPPRESSED", "explicit_opt_out")
    comp = crm["companies"].get(r["company_id"]) if r["company_id"] else None
    is_customer = bool(comp and comp["is_customer_b"]) or bool(c and c.get("lifecyclestage") == "customer")
    if is_customer and not event.get("customer_only"):
        return _decide(r, "EXCLUDE_CUSTOMER")
    if event.get("customer_only") and not is_customer:
        return _decide(r, "EXCLUDE_NOT_CUSTOMER")
    if r["possible_duplicates"]:
        return _decide(r, "REVIEW_POSSIBLE_DUPLICATE", "crm_records:" + ";".join(r["possible_duplicates"]))
    if r["company_match"] == "ambiguous_name":
        return _decide(r, "REVIEW_AMBIGUOUS_COMPANY")
    if r["domain_class"] == "personal" and policy.get("personal_email_requires_opt_in", True):
        if r["marketing_opt_in"] is not True and not (c and c["opt_in_b"] is True):
            return _decide(r, "HOLD_CONSENT", "personal_email_no_opt_in")
    if r["country"] and r["country"] not in event["regions"]:
        return _decide(r, "EXCLUDE_REGION", f"country:{r['country']}")
    return r


def missing_fields(r: dict, policy: dict) -> list[str]:
    need = policy["enrichment"]["fields_needed_for_targeting"]
    return [f for f in need if r.get(f) in (None, "")]


def gate_post(r: dict, crm: dict, event: dict, policy: dict) -> dict:
    if r.get("decision"):
        return r
    miss = [f for f in REQUIRED_FOR_TARGETING if r.get(f) in (None, "")]
    if miss:
        code = "HOLD_NEEDS_ENRICHMENT" if r.get("enrichment_status") in (None, "not_run") else "HOLD_MISSING_DATA"
        return _decide(r, code, "missing:" + ",".join(miss))
    if r["country"] not in event["regions"]:
        return _decide(r, "EXCLUDE_REGION", f"country:{r['country']}")
    r["segment"] = segment_of(r["employees"], policy)
    if r["segment"] not in event["segments"]:
        return _decide(r, "EXCLUDE_SEGMENT", f"segment:{r['segment']}", f"employees:{r['employees']}")
    r["seniority"] = seniority(r["title"], policy)
    min_rank = policy["seniority_rank"].get(event.get("min_seniority", "manager"), 2)
    if not persona_match(r["title"], event):
        return _decide(r, "EXCLUDE_PERSONA", "persona_not_matched")
    if r["seniority"] < min_rank:
        return _decide(r, "EXCLUDE_PERSONA", f"seniority_rank:{r['seniority']}<{min_rank}")
    r["deal_context"] = []
    for d in crm["deals_by_company"].get(r["company_id"] or "", []):
        if not d["open"]:
            continue
        owner = crm["owners"].get(d.get("owner_id"), {})
        team = owner.get("team", "unknown")
        r["deal_context"].append({"deal_id": d["deal_id"], "owner_id": d.get("owner_id"),
                                  "owner_name": owner.get("name", ""), "team": team})
        if team not in event["owning_teams"]:
            return _decide(r, "HOLD_OWNER_APPROVAL", f"open_deal:{d['deal_id']}", f"owner_team:{team}")
    return r


def score(r: dict, event: dict, policy: dict) -> int:
    w = policy["tier_weights"]
    pts, why = 0, []
    if r.get("segment") == "enterprise":
        pts += w["enterprise"]; why.append("enterprise")
    elif r.get("segment") == "mid_market":
        pts += w["mid_market"]; why.append("mid_market")
    if any(i in (r.get("industry") or "") for i in policy["icp_industries"]):
        pts += w["icp_industry"]; why.append("icp_industry")
    if r.get("seniority", -1) >= policy["seniority_rank"]["director"]:
        pts += w["seniority_director_plus"]; why.append("director_plus")
    if "past_event" in r["source"]:
        pts += w["prior_event_attended"]; why.append("prior_event")
    if "sales_nominated" in r["source"]:
        pts += w["sales_nominated"]; why.append("sales_nominated")
    if r.get("deal_context"):
        pts += w["open_deal_allowed_team"]; why.append("open_deal_owning_team")
    r["score"], r["score_reasons"] = pts, why
    r["tier"] = "A" if pts >= policy["tier_a_min"] else "B" if pts >= policy["tier_b_min"] else "C"
    return pts


def account_key(r: dict) -> str:
    if r.get("company_id"):
        return r["company_id"]
    if r.get("domain_class") == "work":
        return email_domain(r["email"])
    return "name:" + (r.get("company_norm") or r["email"])


def allocate(rows: list[dict], event: dict, policy: dict) -> None:
    """Rank eligible rows and apply account cap then capacity. Deterministic tie-breaks."""
    eligible = [r for r in rows if not r.get("decision")]
    for r in eligible:
        score(r, event, policy)
    eligible.sort(key=lambda r: (-r["score"], 0 if "sales_nominated" in r["source"] else 1, r["row"]))
    per_account: dict[str, int] = {}
    invited = 0
    for r in eligible:
        k = account_key(r)
        if per_account.get(k, 0) >= event["max_invites_per_account"]:
            _decide(r, "WAITLIST_ACCOUNT_CAP", f"account:{k}")
        elif invited >= event["invite_capacity"]:
            _decide(r, "WAITLIST_CAPACITY", f"rank_after:{event['invite_capacity']}")
        else:
            _decide(r, "INVITE", f"tier:{r['tier']}", *r["score_reasons"])
            per_account[k] = per_account.get(k, 0) + 1
            invited += 1
        r["rank"] = eligible.index(r) + 1
