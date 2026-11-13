"""Load and normalise the input files.

Everything here is deterministic. Messy values are normalised to a small vocabulary; anything
that cannot be read safely becomes a data-quality note rather than a guess.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import read_csv, truthy

EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")

COUNTRY_MAP = {
    "uk": "UK", "u.k.": "UK", "united kingdom": "UK", "gb": "UK", "great britain": "UK", "england": "UK",
    "scotland": "UK", "wales": "UK", "northern ireland": "UK", "britain": "UK",
    "ie": "IE", "ireland": "IE", "republic of ireland": "IE", "eire": "IE",
    "us": "US", "usa": "US", "united states": "US", "united states of america": "US",
    "de": "DE", "germany": "DE", "fr": "FR", "france": "FR", "nl": "NL", "netherlands": "NL",
}
COMPANY_SUFFIXES = re.compile(r"\b(ltd|limited|plc|inc|llc|gmbh|co|corp|corporation|group|holdings)\b\.?", re.I)


def norm_email(v: str | None) -> str:
    return (v or "").strip().strip("<>").lower()


def email_valid(e: str) -> bool:
    return bool(EMAIL_RE.match(e or ""))


def email_domain(e: str) -> str:
    return e.split("@", 1)[1] if "@" in (e or "") else ""


def norm_country(v: str | None) -> str:
    t = (v or "").strip().lower()
    if not t:
        return ""
    return COUNTRY_MAP.get(t, t.upper() if len(t) == 2 else "OTHER:" + t)


def norm_company(v: str | None) -> str:
    t = COMPANY_SUFFIXES.sub(" ", (v or "").lower())
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_name(v: str | None) -> str:
    return re.sub(r"[^a-z]", "", (v or "").lower())


def parse_employees(v: str | None) -> int | None:
    t = (v or "").strip().lower().replace(",", "")
    if not t:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)k", t)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", t)
    if m:  # band: use the upper bound conservatively for segment checks
        return int(m.group(2))
    m = re.fullmatch(r"(\d+)\+?", t)
    return int(m.group(1)) if m else None


def load_json(path) -> dict:
    return json.loads(Path(path).read_text())


def load_candidates(path) -> list[dict]:
    out = []
    for i, r in enumerate(read_csv(path), start=1):
        email_raw = r.get("email", "")
        email = norm_email(email_raw)
        out.append({
            "row": i,
            "source": (r.get("source") or "").strip().lower() or "unknown",
            "first_name": (r.get("first_name") or "").strip().title(),
            "last_name": (r.get("last_name") or "").strip().title(),
            "email": email,
            "email_raw_changed": email != (email_raw or ""),
            "company": (r.get("company") or "").strip(),
            "company_norm": norm_company(r.get("company")),
            "title": (r.get("title") or "").strip(),
            "country": norm_country(r.get("country")),
            "employees": parse_employees(r.get("employees")),
            "industry": (r.get("industry") or "").strip().lower(),
            "marketing_opt_in": truthy(r.get("marketing_opt_in")),
            "notes": r.get("notes") or "",
            "merged_rows": [i],
            "dq": [],
        })
    return out


def load_crm(contacts_path, companies_path, deals_path, owners_path, suppression_path) -> dict:
    contacts = {}
    for r in read_csv(contacts_path):
        e = norm_email(r.get("email"))
        if not e:
            continue
        r["email"] = e
        r["unsubscribed_b"] = truthy(r.get("unsubscribed")) is True
        r["opt_in_b"] = truthy(r.get("marketing_opt_in"))
        r["campaign_set"] = {c.strip() for c in (r.get("campaign_ids") or "").split(";") if c.strip()}
        contacts[e] = r
    companies = {}
    by_domain, by_name = {}, {}
    for r in read_csv(companies_path):
        r["country_n"] = norm_country(r.get("country"))
        r["employees_n"] = parse_employees(r.get("employees"))
        r["is_customer_b"] = truthy(r.get("is_customer")) is True
        r["name_norm"] = norm_company(r.get("name"))
        companies[r["company_id"]] = r
        if r.get("domain"):
            by_domain[r["domain"].strip().lower()] = r["company_id"]
        by_name.setdefault(r["name_norm"], []).append(r["company_id"])
    owners = {r["owner_id"]: {**r, "active_b": truthy(r.get("active")) is not False} for r in read_csv(owners_path)}
    deals_by_company: dict[str, list] = {}
    for r in read_csv(deals_path):
        r["open"] = truthy(r.get("is_closed")) is not True
        deals_by_company.setdefault(r.get("company_id", ""), []).append(r)
    suppression = {norm_email(r.get("email")): r.get("reason", "") for r in read_csv(suppression_path)}
    people_by_company: dict[str, list] = {}
    for c in contacts.values():
        people_by_company.setdefault(c.get("company_id") or "", []).append(c)
    return {"contacts": contacts, "companies": companies, "by_domain": by_domain, "by_name": by_name,
            "owners": owners, "deals_by_company": deals_by_company, "suppression": suppression,
            "people_by_company": people_by_company}


def load_enrichment_source(path) -> dict:
    """Mock enrichment data keyed by domain (stands in for a Clay table export)."""
    return {(r.get("domain") or "").strip().lower(): r for r in read_csv(path)}


STATUS_MAP = {
    "attended": "attended", "checked in": "attended", "checked-in": "attended", "walk-in": "attended",
    "walk in": "attended", "present": "attended",
    "registered": "registered_no_show", "no-show": "registered_no_show", "no show": "registered_no_show",
    "noshow": "registered_no_show", "did not attend": "registered_no_show",
    "cancelled": "cancelled", "canceled": "cancelled", "declined": "cancelled",
}


def load_attendance(path) -> list[dict]:
    out = []
    for i, r in enumerate(read_csv(path), start=1):
        raw = (r.get("status") or "").strip().lower()
        out.append({"row": i, "email": norm_email(r.get("email")), "name": (r.get("name") or "").strip(),
                    "status_raw": r.get("status") or "", "status": STATUS_MAP.get(raw, "unknown"),
                    "walk_in": raw in {"walk-in", "walk in"},
                    "checked_in_at": (r.get("checked_in_at") or "").strip(),
                    "company": (r.get("company") or "").strip()})
    return out
