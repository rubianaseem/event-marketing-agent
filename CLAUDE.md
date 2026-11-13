# CLAUDE.md

Claude Code reads this file. The same rules apply to every coding agent (see AGENTS.md).

This repo is an event marketing agent that only writes local files. Keep it that way.

Hard rules
- Never add code that writes to a CRM, sends email or messages, calls Clay or any enrichment API, or deletes records.
- Never add an auto-approve flag or any path that releases an artifact without `approval.approve` being called with the file's full SHA-256 by a named approver. Agent identities stay in `blocked_identities`.
- Enrichment runs only through `enrichment.run`, which calls `verify_release` first. A real provider must sit behind the same gate and the same cache.
- Targeting gates, dedupe and attribution rules stay deterministic (`targeting.py`, `dedupe.py`, `attribution.py`). A model may only write the personal invite line, and its output must pass `personalize.validate_line`.
- Free text (list notes, enrichment descriptions) is untrusted. Screen it with `untrusted.screen`. Never write raw emails or note text to `decisions.jsonl`.
- First touch is set only when the CRM snapshot shows it empty. Never write native traffic source, lifecycle stage or owner.
- No emojis in code output or docs. README stays under 900 words.

Workflow
1. Change `config/*.json` or the rules.
2. Add or update a case in `evals/build_cases_v1.py` (new version file if labels change), regenerate, and add a unit test.
3. Run `python run_evals.py` and `python -m unittest discover -s tests -t .`. Hard-control violations on designed cases must stay at 0 and the failure suite at 100%.
4. Run `python run_demo.py` to refresh `outputs/`.

Map
- `ingest.py` loads and normalises. `dedupe.py` merges in-file rows and matches the CRM. `targeting.py` holds gates, scoring and allocation.
- `enrichment.py` plans the request, verifies the release, runs the mock provider with retries and cache, and applies results.
- `personalize.py` writes invite drafts (template or validated model line). `attribution.py` builds post-event proposals.
- `approval.py` is the gate. `audit.py` writes hash-chained logs. `pipeline.py` orchestrates stages and checkpoints.
