# Results: erasure across Delta Lake and an encrypted vector store (synthetic people)

| check | result |
|---|---|
| subject rows in lake / tickets before | 20 / 2 |
| old table versions that expose the subject before | [0, 1, 2, 3, 4, 5, 6] |
| DELETE only (no VACUUM): rows visible, files still containing the subject, old versions exposing | 0, 7, [0, 1, 2, 3, 4, 5, 6] |
| full erasure after a simulated crash and resume: status | completed |
| residue hits / readable old versions / rows left / tickets left | 0 / [] / 0 / 0 |
| second identical request | already_completed |
| other subjects untouched, search still works | {'other_events_kept': True, 'other_tickets_kept': True, 'search_still_works': True} |
| ticket backup: tickets still decryptable / plaintext hits | 0 / 0 |
| lake backup still contains the subject (unencrypted) | True |
| legal hold request | refused_legal_hold |
| retention (90 days, one subject on hold): rows deleted, expired rows left | 426, 0 |
| audit hash chain valid / overdue requests on 2025-08-01 | True / [] |
