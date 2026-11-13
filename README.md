# Event Marketing Agent

**Builds a pre-event invite list you can defend, gates enrichment spend behind an approved credit estimate, dedupes against HubSpot, and proposes the upload plus post-event campaign and attribution changes. It never writes to your CRM.**

Built by Rubia Naseem, GTM Engineer (12+ yrs CRM/RevOps, HubSpot & Salesforce).

## The problem

Event lists are where CRM hygiene and attribution quietly break. Partner lists, past attendees and sales nominations get merged, bulk-enriched (including people who'd never be invited) and imported. Customers get prospect invites, reps' open deals get marketing email, and the post-event import overwrites first touch. Three primary sources shaped the controls:

- HubSpot deduplicates contacts by email and companies by primary domain ([HubSpot KB: deduplication](https://knowledge.hubspot.com/records/deduplication-of-records)), so the import must agree with that, and personal addresses need a different company match.
- "Once a contact is marked as Influenced by a campaign, the campaign influence cannot be removed" ([HubSpot KB: campaign assets](https://knowledge.hubspot.com/campaigns/associate-assets-and-content-with-a-campaign)). That makes campaign association an action worth approving first.
- The ICO treats sole traders like individuals for email marketing, while corporate bodies can be emailed ([ICO: electronic mail marketing](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/)). By default, the agent holds any personal-domain address that has no recorded opt-in. That's a cautious policy setting, not legal advice.

It's a different job from [event-lead-import-agent](https://github.com/rubianaseem/event-lead-import-agent) (cleaning badge scans after an event) and [webinar-follow-up-agent](https://github.com/rubianaseem/webinar-follow-up-agent) (segmenting follow-ups).

## What it does

- **Targets before it spends.** Hard gates (suppression, opt-out text, customers, competitors, internal, region, duplicate review, consent) run first, gaps are filled from the CRM company record, and only then is enrichment planned.
- **Gates enrichment.** `enrichment_request.json` lists domains, fields and a credit estimate from your own per-field assumption. A mock provider (standing in for a Clay export) runs only after that exact file is approved and released, with retries, Retry-After and a cache so no domain is charged twice.
- **Dedupes and matches** on normalised email, then work domain, then company name for personal addresses. A same-name person under a different email at the same company goes to review instead of being created.
- **Ranks within capacity.** It checks segment, persona and seniority, holds anyone with an open deal owned by another team until that owner agrees, scores tiers, and applies the account cap and seat capacity.
- **Proposes the post-event changes.** It writes campaign associations, sets first touch only when the CRM shows it empty, moves last touch for attendees only, and blocks the whole file if a picklist value isn't allowed.
- **Leaves an audit trail:** a hash-chained decision trace (pseudonymised emails), review queue, owner request drafts and invite drafts.

## Where each step runs

1. **GitHub** stores the code. Clone it or download the ZIP.
2. **Your terminal** (Mac Terminal or the Cursor terminal) runs `agent.py` on CSV exports. No API key is needed. If `ANTHROPIC_API_KEY` is set, Claude writes one personal line per invite through the same code, behind a validator.
3. **You review** `review_queue.csv`, `enrichment_request.json` and `hubspot_import_proposed.csv`, then approve a file by passing its SHA-256 as a named approver. If the file changes, the approval is void.
4. **HubSpot**: you import the released file yourself (Contacts > Import), add released associations to the campaign, and import attribution updates matched on Record ID.
5. **The agent performs no writes**: no CRM calls, no sends, no Clay calls, no deletes.

## Quick start

```bash
git clone https://github.com/rubianaseem/event-marketing-agent.git
cd event-marketing-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # standard library only
python run_demo.py                # full flow on synthetic data
python run_evals.py               # eval set + failure suite
python -m unittest discover -s tests -t .
```

The step-by-step CLI (`plan`, `show`, `approve`, `release`, `enrich`, `build`, `attribute`, `verify`) is in [SETUP.md](SETUP.md).

## Example output (synthetic sample, 30 rows, condensed from outputs/demo_transcript.txt)

```text
run run_e31d24ddd1ec: 4 domains need enrichment, estimated 9 credits (your per-field assumption)
REFUSED: enrichment_request.json has not been approved and released; no enrichment run
REFUSED (agent): 'agent' is an agent/service identity and can never approve
  orbitfin.example: found after 3 attempt(s)
re-run: provider calls 0, credits charged 0 (cache, no double spend)
  INVITE: 10   EXCLUDE_SUPPRESSED: 3   WAITLIST_CAPACITY: 2   HOLD_OWNER_APPROVAL: 1   HOLD_CONSENT: 1
  first_touch_set: 1   first_touch_preserved: 2   first_touch_unverified: 4   last_touch_event: 4
```

Measured on the versioned synthetic set (`python run_evals.py`): 0.952 exact targeting accuracy against 0.357 for a naive list pull. There were 2 hard-control violations against 20 for the baseline, and both of the agent's were on held-out cases written after the rules (a paraphrased opt-out and an indirect injection). Attribution had 0 first-touch overwrites against 5 for the baseline, and the failure suite passed 22 of 22. Full report: [docs/EVALUATION.md](docs/EVALUATION.md).

## Guardrails and human in the loop

- Two approval gates are enforced in code: the enrichment request (row count and credits) and every import or association file. Each approval binds to the file's SHA-256, a named approver and a 72-hour expiry. Agent identities are refused, and there's no auto-approve flag.
- Approvals in the demo are simulated. They don't give permission to touch a real CRM.
- Stale snapshots (older than 24 hours) can't be approved. Native traffic source, lifecycle stage and owner are never written.

## More

[SETUP.md](SETUP.md) | [Architecture](docs/ARCHITECTURE.md) | [Evaluation](docs/EVALUATION.md) | [Details, costs, stack swaps and production gaps](docs/DETAILS.md) | [Sources and inspiration](docs/SOURCES.md) | [CLAUDE.md](CLAUDE.md)
