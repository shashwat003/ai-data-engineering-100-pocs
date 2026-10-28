# AI risk register (draft for human review)

> Screening aid based on a simplified reading of the EU AI Act. Not legal advice.

Systems: 7 | prohibited: 1 | high: 2 | limited: 2 | minimal: 2 | open gaps: 5

## SYS-06 Emotion tracking in call centre - PROHIBITED
- Purpose: Infer agent emotions from voice to rank staff
- Why: Art. 5(1)(f): emotion inference in the workplace
- Action: STOP: do not deploy; escalate to legal
## SYS-01 Credit scoring model - HIGH
- Purpose: Rank consumer loan applicants by default risk
- Why: Annex III(5)(b): creditworthiness of natural persons
- Action: no open gaps against modelled obligations
| Obligation | Status |
|---|---|
| Risk management system (Art. 9) | evidenced |
| Data and data governance (Art. 10) | evidenced |
| Technical documentation (Art. 11) | evidenced |
| Record-keeping / logging (Art. 12) | evidenced |
| Human oversight (Art. 14) | evidenced |
| Accuracy, robustness, cybersecurity (Art. 15) | evidenced |
## SYS-02 CV screening assistant - HIGH
- Purpose: Shortlist job applicants from submitted CVs
- Why: Annex III(4): recruitment and worker management
- Action: close gaps before go-live
| Obligation | Status |
|---|---|
| Risk management system (Art. 9) | MISSING |
| Data and data governance (Art. 10) | MISSING |
| Technical documentation (Art. 11) | evidenced |
| Record-keeping / logging (Art. 12) | evidenced |
| Human oversight (Art. 14) | MISSING |
| Accuracy, robustness, cybersecurity (Art. 15) | MISSING |
## SYS-03 Support chatbot - LIMITED
- Purpose: Answer customer questions about card fees
- Why: Art. 50: system interacts directly with people
- Action: no open gaps against modelled obligations
| Obligation | Status |
|---|---|
| Transparency: tell users they interact with AI (Art. 50) | evidenced |
## SYS-07 Deepfake voice for ads - LIMITED
- Purpose: Synthetic celebrity-style voice for radio ads
- Why: Art. 50: deepfake content
- Action: close gaps before go-live
| Obligation | Status |
|---|---|
| Label synthetic/deepfake content (Art. 50) | MISSING |
## SYS-04 Marketing copy generator - MINIMAL
- Purpose: Draft promotional emails reviewed by staff
- Why: no prohibited, high-risk or transparency rule matched
- Action: no open gaps against modelled obligations
## SYS-05 Invoice anomaly detector - MINIMAL
- Purpose: Flag unusual supplier invoices for finance review
- Why: no prohibited, high-risk or transparency rule matched
- Action: no open gaps against modelled obligations
