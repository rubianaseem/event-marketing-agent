from pathlib import Path

from event_agent.dedupe import match_crm
from event_agent.ingest import load_crm, load_json

ROOT = Path(__file__).resolve().parents[1]
EVENT = load_json(ROOT / "config" / "event.json")
POLICY = load_json(ROOT / "config" / "policy.json")


def crm():
    return load_crm(ROOT / "sample_crm_contacts.csv", ROOT / "sample_crm_companies.csv", ROOT / "sample_crm_deals.csv",
                    ROOT / "sample_owners.csv", ROOT / "sample_suppression.csv")


def row(**kw):
    from event_agent.ingest import norm_company
    base = {"row": 1, "source": "partner_list", "first_name": "Test", "last_name": "Person", "email": "t.person@newco.example",
            "company": "NewCo", "title": "Head of RevOps", "country": "UK", "employees": 500, "industry": "software",
            "marketing_opt_in": None, "notes": "", "merged_rows": [1], "dq": []}
    base.update(kw)
    base["company_norm"] = norm_company(base["company"])
    return base


def matched(c=None, **kw):
    c = c or crm()
    r = row(**kw)
    match_crm(r, c, POLICY)
    return r, c
