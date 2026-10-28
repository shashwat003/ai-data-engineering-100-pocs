# Results: hybrid PII redaction on synthetic text (names in the test set were never seen in training)

Hybrid = validated pattern detectors (Luhn, IBAN mod-97, SSN rules, email, phone) from POC 034 plus a small logistic-regression tagger for person names and street addresses. 'Patterns only' is the same pipeline without the tagger. No Presidio, spaCy or neural NER was used.

| type | unseen names: P | R | unseen names + new templates: P | R | patterns-only P | R | support |
|---|---|---|---|---|---|---|---|
| PERSON | 1.000 | 1.000 | 0.342 | 0.243 | 0.000 | 0.000 | 719 |
| ADDRESS | 1.000 | 1.000 | 0.962 | 0.962 | 0.000 | 0.000 | 151 |
| EMAIL | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 176 |
| PHONE | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 151 |
| CREDIT_CARD | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 90 |
| IBAN | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 90 |
| US_SSN | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 69 |

Character-level PII leak rate: unseen names 0.0, unseen names AND unseen sentence templates 0.2396, patterns only 0.4818. The near-perfect 'unseen names' column reflects test sentences that reuse the training templates; the held-out-template column is the fairer estimate. A variant that also uses token identity and suffix features (identity features) scores PERSON recall 0.812 on unseen names but 1.0 on names seen in training: it memorises names. Over-redaction of non-PII characters: 0.0. Seen-name leak rate: 0.0. Throughput on one CPU core: about 830.2 texts per second.

Hard cases (obfuscation, lowercase names) the system is expected to miss:

| input | redacted |
|---|---|
| reach me at john [at] example [dot] com tonight | reach me at john [at] example [dot] com tonight |
| call five five five 123 4567 after noon | call five five five [PHONE]after noon |
| mail JOHN.SMITH@EXAMPLE.COM now | mail [EMAIL] now |
| my friend maria gonzalez lives at 12 baker street | my friend maria gonzalez lives at 12 baker street |
| Contact: Ana Ng (ana.ng@example.com), 555-123-4567 | Contact: [PERSON] ([EMAIL]), [PHONE] |
