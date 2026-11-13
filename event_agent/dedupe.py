"""In-file dedupe and CRM matching (deterministic).

HubSpot itself deduplicates contacts by email and companies by primary domain, so the import
file must agree with that: one row per email, company resolved by domain. Event lists also hold
personal addresses, so for a personal-domain email the company is matched on normalised company
name instead, and a person who already exists under a different email at the same company is a
possible duplicate for a human to merge, never a silent create.
"""
from __future__ import annotations

from .ingest import email_domain, email_valid, norm_name

MERGE_FIELDS = ["company", "company_norm", "title", "country", "employees", "industry"]


def _restrictive_opt_in(a, b):
    # False (explicit no) beats True; True beats unknown (None).
    if a is False or b is False:
        return False
    if a is True or b is True:
        return True
    return None


def dedupe_in_file(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge rows sharing a normalised email. Returns (unique_rows, merge_events)."""
    by_email: dict[str, dict] = {}
    unique, merges = [], []
    for r in rows:
        key = r["email"]
        if not key or key not in by_email:
            unique.append(r)
            if key:
                by_email[key] = r
            continue
        keep = by_email[key]
        for f in MERGE_FIELDS:
            if not keep.get(f) and r.get(f):
                keep[f] = r[f]
        keep["marketing_opt_in"] = _restrictive_opt_in(keep["marketing_opt_in"], r["marketing_opt_in"])
        keep["notes"] = " | ".join(x for x in (keep["notes"], r["notes"]) if x)
        keep["merged_rows"].append(r["row"])
        if r["source"] not in keep["source"].split("+"):
            keep["source"] += "+" + r["source"]
        merges.append({"kept_row": keep["row"], "merged_row": r["row"]})
    return unique, merges


def classify_domain(email: str, policy: dict) -> str:
    d = email_domain(email)
    if not d:
        return "none"
    if d in policy["internal_domains"]:
        return "internal"
    if d in policy["competitor_domains"]:
        return "competitor"
    if d in policy["personal_email_domains"]:
        return "personal"
    return "work"


def suggest_domain(email: str, crm: dict) -> str | None:
    """'tom@brightline' -> 'brightline.example' when a CRM company domain starts with it."""
    d = email_domain(email)
    if not d or "." in d:
        return None
    for dom in crm["by_domain"]:
        if dom.split(".")[0] == d:
            return dom
    return None


def match_crm(r: dict, crm: dict, policy: dict) -> dict:
    """Attach CRM context: contact record, company, how it matched, possible duplicates."""
    r["email_valid"] = email_valid(r["email"])
    r["domain_class"] = classify_domain(r["email"], policy) if r["email_valid"] else "invalid"
    r["crm_contact"] = crm["contacts"].get(r["email"]) if r["email_valid"] else None
    r["company_id"], r["company_match"] = None, "none"
    if r["crm_contact"] and r["crm_contact"].get("company_id"):
        r["company_id"], r["company_match"] = r["crm_contact"]["company_id"], "contact_record"
    elif r["domain_class"] == "work" and email_domain(r["email"]) in crm["by_domain"]:
        r["company_id"], r["company_match"] = crm["by_domain"][email_domain(r["email"])], "domain"
    elif r["company_norm"]:
        ids = crm["by_name"].get(r["company_norm"], [])
        if len(ids) == 1:
            r["company_id"], r["company_match"] = ids[0], "company_name"
        elif len(ids) > 1:
            r["company_match"] = "ambiguous_name"
        else:  # prefix match ("northwind" vs "northwind analytics") only for the same email domain
            dom = email_domain(r["email"])
            for cid, c in crm["companies"].items():
                if c["name_norm"].startswith(r["company_norm"] + " ") and c.get("domain") == dom:
                    r["company_id"], r["company_match"] = cid, "domain"
    r["possible_duplicates"] = []
    if not r["crm_contact"] and r["company_id"]:
        fn, ln = norm_name(r["first_name"]), norm_name(r["last_name"])
        for c in crm["people_by_company"].get(r["company_id"], []):
            if c["email"] != r["email"] and norm_name(c.get("lastname")) == ln and \
                    (norm_name(c.get("firstname")) == fn or norm_name(c.get("firstname"))[:1] == fn[:1]):
                r["possible_duplicates"].append(c["record_id"])
    if r["company_id"]:
        comp = crm["companies"][r["company_id"]]
        backfilled = []
        if not r["country"] and comp.get("country_n"):
            r["country"] = comp["country_n"]; backfilled.append("country")
        if r["employees"] is None and comp.get("employees_n") is not None:
            r["employees"] = comp["employees_n"]; backfilled.append("employees")
        if not r["industry"] and comp.get("industry"):
            r["industry"] = comp["industry"].lower(); backfilled.append("industry")
        if backfilled:
            r["dq"].append("backfilled_from_crm:" + ",".join(backfilled))
    return r
