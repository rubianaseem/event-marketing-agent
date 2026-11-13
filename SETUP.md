# Setup

## 1. Virtual environment and sample run

```bash
git clone https://github.com/rubianaseem/event-marketing-agent.git
cd event-marketing-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # standard library only, nothing to install
python run_demo.py
python run_evals.py
python -m unittest discover -s tests -t .
```

Python 3.10 or newer. The demo writes `outputs/sample_run` and `outputs/sample_attribution`.

## 2. The step-by-step CLI

The sample files carry fixed export dates, so pass `--as-of` to keep the snapshot fresh. With your own exports, leave it out.

```bash
A="--as-of 2026-11-13T12:00:00Z"
python agent.py plan  --out outputs/run_mine $A           # dedupe, CRM match, gates, enrichment request
python agent.py show  --run-dir outputs/run_mine           # artifacts and SHA-256
python agent.py approve --run-dir outputs/run_mine --artifact enrichment_request.json --sha256 <hash> --approver rubia $A
python agent.py release --run-dir outputs/run_mine --artifact enrichment_request.json $A
python agent.py enrich  --run-dir outputs/run_mine $A      # mock provider; refuses without the release
python agent.py build   --run-dir outputs/run_mine $A      # targeting, import proposal, drafts
python agent.py approve --run-dir outputs/run_mine --artifact hubspot_import_proposed.csv --sha256 <hash> --approver rubia $A
python agent.py release --run-dir outputs/run_mine --artifact hubspot_import_proposed.csv $A
python agent.py attribute --out outputs/attr_mine --plan-run outputs/run_mine \
  --crm-manifest sample_export_manifest_post_event.json --as-of 2026-12-04T10:00:00Z
python agent.py verify  --run-dir outputs/run_mine
```

You can skip enrichment and run `build` straight after `plan`. Rows that need data are then held as `HOLD_NEEDS_ENRICHMENT`.

## 3. Optional Claude

```bash
pip install -r requirements-claude.txt
cp .env.example .env    # add ANTHROPIC_API_KEY, then: export $(grep -v '^#' .env | xargs)
```

With a key set, `build` asks Claude for one personal line per invitee. It sends only structured fields plus company description text that passed the injection screen. Any output that fails `validate_line` falls back to the template, and the trace records why. Gates, ranking and attribution never use the model. The live Claude path has not been tested in this repo's CI; the tests use scripted clients.

## 4. Real data sources

Export these from HubSpot (Settings > Import & Export, or a saved view export):

| File | HubSpot export | Columns used |
|---|---|---|
| `sample_candidates.csv` | your merged event list | source, first_name, last_name, email, company, title, country, employees, industry, marketing_opt_in, notes |
| `sample_crm_contacts.csv` | Contacts | record_id, email, firstname, lastname, company_id, lifecyclestage, unsubscribed, marketing_opt_in, first/last touch properties, campaign_ids |
| `sample_crm_companies.csv` | Companies | company_id, name, domain, owner_id, is_customer, country, employees, industry |
| `sample_crm_deals.csv` | Deals | deal_id, company_id, stage, is_closed, owner_id |
| `sample_owners.csv` | Users and teams | owner_id, name, team, active |
| `sample_suppression.csv` | your suppression list | email, reason |
| `sample_attendance.csv` | event platform export | email, name, status, checked_in_at |

Then update `sample_export_manifest.json` with the export time, `config/event.json` for the event, and `config/picklists.json` with the internal values from Settings > Properties. Create the custom contact properties first: `event_attendance_status`, `event_source_detail`, `event_invite_tier`, `first_touch_source`, `first_touch_campaign`, `last_touch_source` and `last_touch_campaign`. Keep HubSpot's import default of not overwriting with blank cells.

Enrichment: export your Clay table (or any provider) as CSV with `domain,country,employees,industry,company_description` and point `--enrichment-source` at it. The agent never calls Clay. Agree the row count and credits with whoever owns the Clay budget before running anything in Clay.

## 5. Extending with Cursor, Claude Code, Codex CLI or Grok

Open the repo in your tool. The rules for coding agents are in `CLAUDE.md` and `AGENTS.md`: no CRM writes, no auto-approve, gates stay deterministic. Good first prompts:

- "Add a Salesforce export adapter in ingest.py that maps Lead and Contact exports to the candidate schema, with tests."
- "Add a real enrichment provider class behind enrichment.verify_release and the cache, mocked in tests."
- "Add an eval case for a partner-supplied list with title in the name column, then make it pass."

Run `python run_evals.py` and the tests after every change.

## 6. Troubleshooting

| Message | Fix |
|---|---|
| `snapshot is N hours old` | Re-export from HubSpot and re-run `plan` (limit in `policy.json`). |
| `enrichment_request.json has not been approved and released` | Approve with the hash from `show`, then `release`. |
| `hash does not match the file on disk` | You reviewed an older file. Run `show` and use the current hash. |
| `input 'x' changed since plan` | An input file changed after `plan`. Run `plan` again. |
| `file changed after approval` | Someone edited the artifact. Re-run instead of editing outputs. |
| Attribution `BLOCKED` | A value is missing from `config/picklists.json`. Fix the event config or the picklist. |
| Many `HOLD_CONSENT` rows | Personal addresses without opt-in. Decide a lawful basis, or set `personal_email_requires_opt_in` deliberately. |
