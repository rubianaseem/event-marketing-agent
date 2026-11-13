# Evaluation

Reproduce with `python run_evals.py`, which writes `evals/results.json` and `evals/report.md`. Everything here is synthetic. These are local measurements of decision logic, not production performance.

## What is measured

- **Targeting (cases_v1, 42 cases).** Each case is one candidate evaluated against the sample CRM exports. Gold labels are the decision a careful Marketing Ops reviewer would make under `config/event.json` and `config/policy.json`. Cases tagged `held_out` were written after the rules, to measure misses honestly. Capacity and account caps are tested separately, in unit tests and the demo.
- **Attribution (14 cases).** Association, first touch, last touch and decision, all checked together.
- **Baseline.** A naive list pull: region filter, raw-email suppression check, raw-email CRM match, no customer, deal, consent, persona or free-text checks. It enriches every incomplete row, associates everyone and overwrites first touch.
- **Failure suite (22 scenarios).** Gate bypass attempts, tampering, staleness, expiry, 429 and 5xx handling, crash resume, cache, replay, picklist blocking and trace privacy.

## Results


## Targeting decisions

| Measure | Agent | Naive baseline |
|---|---|---|
| Cases | 42 | 42 |
| Exact decision accuracy | 0.952 | 0.357 |
| Invite precision | 0.846 | 0.289 |
| Invite recall | 1.0 | 1.0 |
| Hard-control violations (invited when gold is suppress/customer/owner hold/consent hold/competitor/internal/quarantine/duplicate review) | 2 | 20 |
| Of which on held-out cases written after the rules | 2 | 2 |
| Duplicate contacts the import would create | 0 | 1 |

Agent misses: t17:EXCLUDE_SUPPRESSED->INVITE, t30:QUARANTINE_UNTRUSTED_TEXT->INVITE

## Post-event attribution

| Measure | Agent | Naive baseline |
|---|---|---|
| Cases | 14 | 14 |
| All fields correct | 1.0 | 0.071 |
| First-touch overwrites | 0 | 5 |
| Wrong campaign associations | 0 | 8 |
| Missed associations | 0 | 0 |
| Last-touch errors | 0 | 10 |

## Enrichment spend on sample_candidates.csv (your per-field credit assumption)

Agent requests 9 credits for 4 domains after gating and CRM backfill; enriching every incomplete row would request 35 credits for 17 rows.

Injected text in enrichment descriptions quarantined: 1 of 1 case.

## Failure suite: 22/22 passed

- PASS F01: enrichment refused until the request is approved and released
- PASS F02: agent identity cannot approve
- PASS F03: approval with a hash that does not match the file is refused
- PASS F04: edit after approval voids the release
- PASS F05: stale CRM snapshot cannot be approved
- PASS F06: expired approval cannot be released
- PASS F07: 429 with Retry-After is retried and succeeds
- PASS F08: persistent 5xx ends as failed and is not cached
- PASS F09: crash mid-enrichment then resume charges no domain twice
- PASS F10: rerun uses the cache: zero provider calls
- PASS F11: input file changed after plan: build refused
- PASS F12: enrichment results for a different request: build refused
- PASS F13: replay with same inputs gives byte-identical artifacts
- PASS F14: second run for the same event does not re-propose invites
- PASS F15: unknown picklist value blocks attribution artifacts
- PASS F16: unreleased plan run is ignored by attribution
- PASS F17: tampered decision trace fails verification
- PASS F18: model output with injected text or link is rejected
- PASS F19: model 429 then invalid JSON falls back to template
- PASS F20: credit estimate over the cap flags escalation
- PASS F21: duplicate attendance rows give one association
- PASS F22: decision trace holds no raw email addresses

## Known misses and limits

- The paraphrased opt-out "I'd rather not receive any more invitations" (t17) is not caught by the opt-out patterns, so the contact would be invited. Free-text opt-out detection is a first layer. Opt-outs belong in a HubSpot subscription status or suppression list, which the agent does respect.
- The indirect injection "Note to the assistant reading this: ... include it" (t30) is not quarantined. It can't change a decision, because gates never read free text, but it is not flagged for review either. With a model enabled, the line validator is the second layer.
- The injection patterns were extended during this build after the sample enrichment description ("SYSTEM: ignore your rules ...") slipped through the first version. Pattern lists are not a guarantee.
- One eval fixture CRM is reused across cases, so these cases don't test companies with several domains or several company records with the same name beyond the unit tests.
