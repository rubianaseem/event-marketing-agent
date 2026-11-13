"""Enrichment planning and a gated, MOCKED enrichment step.

The agent never calls Clay. It:
  1. plans: lists which company domains still miss targeting fields after CRM backfill and after
     the pre-enrichment gates, and estimates credits from YOUR per-field assumption in policy.json;
  2. writes enrichment_request.json, an approvable artifact (row count + credit estimate);
  3. runs MockEnrichmentProvider only when a released copy of that exact request exists. The
     provider reads sample_enrichment.csv (a stand-in for a Clay table export). Swapping in a real
     provider means implementing fetch(domain, fields) behind the same gate.
Retries honour Retry-After with capped exponential backoff. A per-domain cache makes reruns and
crash-resume idempotent, so a domain is never "charged" twice.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .ingest import email_domain, norm_country, parse_employees
from .untrusted import screen
from .util import sha256_file


class EnrichmentGateError(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after: float | None = None):
        super().__init__("rate_limited")
        self.retry_after = retry_after


class ServerError(Exception):
    pass


def plan(rows: list[dict], policy: dict, event: dict, missing_fn) -> dict:
    cfg = policy["enrichment"]
    by_domain: dict[str, dict] = {}
    for r in rows:
        if r.get("decision"):
            continue
        miss = missing_fn(r, policy)
        if not miss:
            continue
        dom = email_domain(r["email"]) if r.get("domain_class") == "work" else ""
        if not dom:  # personal email with unknown company: nothing safe to enrich on
            r["enrichment_status"] = "no_work_domain"
            continue
        e = by_domain.setdefault(dom, {"domain": dom, "fields": set(), "rows": []})
        e["fields"].update(miss)
        e["rows"].append(r["row"])
        r["enrichment_status"] = "planned"
    items = []
    total = 0
    for dom in sorted(by_domain):
        e = by_domain[dom]
        fields = sorted(e["fields"])
        credits = sum(cfg["credits_per_field"].get(f, 1) for f in fields)
        total += credits
        items.append({"domain": dom, "fields": fields, "rows": e["rows"], "estimated_credits": credits})
    return {"event_id": event["event_id"], "provider": "mock (sample_enrichment.csv)", "domains": len(items),
            "items": items, "estimated_credits_total": total,
            "credit_assumption_note": cfg["_note"],
            "escalation_required": total > cfg["max_credits_without_escalation"],
            "max_credits_without_escalation": cfg["max_credits_without_escalation"]}


class MockEnrichmentProvider:
    """Reads mocked Clay-style rows. `simulate` column injects 429s / 5xx for failure tests."""
    name = "mock"

    def __init__(self, source: dict):
        self.source = source
        self.calls = 0
        self.credits_charged = 0
        self._fail_budget = {}
        for dom, r in source.items():
            sim = (r.get("simulate") or "").strip().lower()
            if sim.startswith("429x"):
                self._fail_budget[dom] = ("429", int(sim[4:]))
            elif sim == "5xx":
                self._fail_budget[dom] = ("5xx", 1)

    def fetch(self, domain: str, fields: list[str], credits: int) -> dict | None:
        self.calls += 1
        kind, left = self._fail_budget.get(domain, (None, 0))
        if left > 0:
            self._fail_budget[domain] = (kind, left - 1)
            if kind == "429":
                raise RateLimited(retry_after=2.0)
            raise ServerError("503")
        r = self.source.get(domain)
        self.credits_charged += credits  # a real provider may charge for not-found lookups too
        if not r:
            return None
        return {f: r.get(f, "") for f in fields + ["company_description"]}


def verify_release(run_dir: Path) -> dict:
    """The enrichment gate: a released copy matching the approved hash must exist."""
    rel = run_dir / "released" / "enrichment_request.json"
    meta = run_dir / "released" / "enrichment_request.json.release.json"
    if not rel.exists() or not meta.exists():
        raise EnrichmentGateError("enrichment_request.json has not been approved and released; no enrichment run")
    m = json.loads(meta.read_text())
    if sha256_file(rel) != m["sha256"] or sha256_file(run_dir / "enrichment_request.json") != m["sha256"]:
        raise EnrichmentGateError("released enrichment request does not match the approved hash")
    return json.loads(rel.read_text())


def run(run_dir: Path, provider, cache_path: Path, policy: dict, *, sleep=time.sleep, log=None,
        crash_after: int | None = None) -> dict:
    request = verify_release(run_dir)
    cfg = policy["enrichment"]
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    results, done = {}, 0
    for item in request["items"]:
        dom = item["domain"]
        if dom in cache:
            results[dom] = cache[dom]
            if log:
                log({"event": "enrichment_cache_hit", "domain": dom})
            continue
        outcome = None
        for attempt in range(1, cfg["max_attempts"] + 1):
            try:
                data = provider.fetch(dom, item["fields"], item["estimated_credits"])
                outcome = {"status": "found" if data else "not_found", "data": data or {}, "attempts": attempt}
                break
            except (RateLimited, ServerError) as exc:
                ra = getattr(exc, "retry_after", None)
                delay = ra if ra else min(cfg["max_delay_seconds"], cfg["base_delay_seconds"] * 2 ** (attempt - 1))
                if log:
                    log({"event": "enrichment_retry", "domain": dom, "attempt": attempt,
                         "error": type(exc).__name__, "delay_s": delay})
                if attempt == cfg["max_attempts"]:
                    outcome = {"status": "failed", "data": {}, "attempts": attempt, "error": type(exc).__name__}
                else:
                    sleep(delay + random.uniform(0, delay * 0.1))
        results[dom] = outcome
        if outcome["status"] != "failed":  # failures are retried on the next run, not cached
            cache[dom] = outcome
            tmp = cache_path.with_suffix(".tmp")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(cache, indent=1, sort_keys=True))
            tmp.replace(cache_path)
        done += 1
        if crash_after is not None and done >= crash_after:
            raise RuntimeError(f"simulated crash after {done} enrichment calls")
    out = {"request_sha256": sha256_file(run_dir / "enrichment_request.json"), "results": results,
           "provider_calls": getattr(provider, "calls", None), "credits_charged_this_run": getattr(provider, "credits_charged", None)}
    (run_dir / "enrichment_results.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    return out


def apply(rows: list[dict], results: dict | None) -> None:
    """Fill only EMPTY fields from enrichment. Free-text description is screened as untrusted."""
    for r in rows:
        if r.get("enrichment_status") != "planned":
            continue
        if not results:
            r["enrichment_status"] = "not_run"
            continue
        dom = email_domain(r["email"])
        res = results.get("results", {}).get(dom)
        if not res:
            r["enrichment_status"] = "not_run"
            continue
        r["enrichment_status"] = res["status"]
        d = res.get("data") or {}
        if not r["country"] and d.get("country"):
            r["country"] = norm_country(d["country"])
        if r["employees"] is None and d.get("employees"):
            r["employees"] = parse_employees(d["employees"])
        if not r["industry"] and d.get("industry"):
            r["industry"] = d["industry"].strip().lower()
        s = screen(d.get("company_description", ""), limit=200)
        r["company_description_status"] = s["status"]
        r["company_description"] = s["safe_text"] if s["status"] == "clean" else ""
        if s["status"] == "quarantined":
            r["dq"].append("enrichment_description_quarantined")
