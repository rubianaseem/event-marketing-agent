# Event targeting brief: London RevOps Leaders Roundtable (2026-12-03)

Run run_e31d24ddd1ec, policy 2026-11-13.1, CRM snapshot 2026-11-13T09:00:00Z (3.0 h old).

Candidates after in-file dedupe: 29. Proposed invites: 10 of capacity 10. Review queue: 7 rows.

## Decisions

- INVITE: 10
- EXCLUDE_SUPPRESSED: 3
- EXCLUDE_SEGMENT: 2
- WAITLIST_CAPACITY: 2
- EXCLUDE_COMPETITOR: 1
- EXCLUDE_CUSTOMER: 1
- EXCLUDE_INTERNAL: 1
- EXCLUDE_PERSONA: 1
- EXCLUDE_REGION: 1
- HOLD_CONSENT: 1
- HOLD_MISSING_DATA: 1
- HOLD_OWNER_APPROVAL: 1
- QUARANTINE_INVALID_EMAIL: 1
- QUARANTINE_UNTRUSTED_TEXT: 1
- REVIEW_POSSIBLE_DUPLICATE: 1
- WAITLIST_ACCOUNT_CAP: 1

## Enrichment (mock provider)

- domains looked up: 4 (found 3, not_found 1)
- credits charged this run (your per-field assumption): 0

## Proposed invites (ranked)

1. Chloe Byrne, Head of Marketing Ops, Kestrel Cloud (tier A, score 9)
2. Oliver Wren, Director of Sales Operations, Stratus HR (tier A, score 8)
3. Maya Patel, Head of Revenue Operations, Northwind Analytics (tier A, score 7)
4. Omar Haddad, RevOps Director, Kestrel Cloud Ltd (tier A, score 7)
5. Noah Fischer, VP Sales Operations, Tidewater Systems (tier A, score 7)
6. Mia Torres, Head of Sales Operations, Orbit Finance (tier A, score 7)
7. Priya Shah, VP Revenue Operations, Helixsoft (tier A, score 7)
8. Isla Murray, Head of CRM, Beaconly (tier A, score 6)
9. Jack Pryce, RevOps Manager, Meridian Pay (tier B, score 5)
10. Tom Reid, Marketing Operations Manager, Northwind (tier B, score 4)

## Next step

Review hubspot_import_proposed.csv and review_queue.csv. Approve the import with its SHA-256 (`python agent.py show` then `approve`). Release copies it to released/. You import it in HubSpot yourself.
