# Details

## How this runs today (and what production would add)

| Capability | Status |
|---|---|
| Targeting gates, dedupe, CRM match, ranking, capacity | Implemented and tested locally on synthetic data |
| Enrichment request, approval gate, retries, cache, crash resume | Implemented and tested locally against a mock provider |
| Real Clay or other enrichment API | Not integrated, by design. Export a table to CSV instead |
| Optional Claude personal line | Implemented with validator and fallback. Tested with scripted clients only, live API not exercised |
| HubSpot import, campaign association, attribution import | Proposed files only. You perform these manually in HubSpot |
| Approval UI | CLI with SHA-256. Production would add a Slack or web approval bound to the same hash (see [hubspot-mcp-slack-approval-agent](https://github.com/rubianaseem/hubspot-mcp-slack-approval-agent)) |
| Decision log | Local hash-chained JSONL. Production would stream to a warehouse table (see [agent-decision-log](https://github.com/rubianaseem/agent-decision-log)) |
| Production-proven | Nothing in this repo is production-proven |

Production would also add: a scheduled CRM export or read-only API pull with a freshness check, keyed HMAC pseudonyms instead of the fixed-salt hash used here, secrets in a manager rather than `.env`, per-event config in version control with review, and alerting when the review queue grows past a threshold.

## API cost assumptions

No account or billing data was used. With no API key there is no model cost. With Claude enabled, there is one short call per invitee (around 300 input tokens and 60 output tokens per call, using a short system prompt and structured facts). A 10-seat event is 10 calls. Check current pricing on Anthropic's site for your model. Enrichment credits are estimated from `policy.json > enrichment.credits_per_field`, which is your own assumption, not published Clay pricing. Set it from your own workspace.

## Stack swap

| Piece | Here | Swap for |
|---|---|---|
| CRM | HubSpot-shaped CSV exports | Salesforce Lead/Contact/Account/Opportunity exports (map columns in `ingest.py`) |
| Enrichment | Mock provider over CSV | Clay table export, Apollo or ZoomInfo export, or a provider class behind `verify_release` |
| Event platform | Generic attendance CSV | Luma, Eventbrite, Goldcast or Zoom exports (map statuses in `STATUS_MAP`) |
| Approval | CLI + SHA-256 | Slack interactive message bound to the hash |
| Orchestration | Python CLI | n8n or a scheduler calling the same stages |
| Model | Claude (optional) | Any model behind `llm.py` with the same validator |

## Customise for your stack

- `config/event.json`: regions, segments, personas, minimum seniority, owning sales teams, capacity, account cap, campaign id and picklist values.
- `config/policy.json`: personal and competitor domains, segment thresholds, tier weights, enrichment fields and credit assumptions, attribution statuses, and the properties that must never be written.
- `config/approvers.json`: who may approve which artifact, plus blocked identities.
- `config/picklists.json`: internal values copied from HubSpot property settings.

## Trade-offs

- **Personal emails are held without an opt-in.** That's cautious, and it costs some invites. It's a setting, and the right call depends on your lawful-basis decision.
- **Possible duplicates go to review** rather than being merged automatically. That's slower, but a wrong merge in HubSpot is hard to undo.
- **Holds for open deals owned by another team** mean some good contacts wait for an owner's reply. The draft in `owner_requests.md` makes that quick.
- **First touch is never guessed** for contacts not in the CRM snapshot. Re-export after the import and re-run `attribute`.
- **Capacity uses a simple tier score.** It's transparent and tunable, not a predictive model.
