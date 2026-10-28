# Publishing checklist (owner actions marked **[OWNER]**)

Nothing below has been done. The repository is private, the work is on `claude/relaxed-allen-usvr0o`, and `shashwat003/shashwat003` has not been created or modified.

## 1. Decisions only the owner can make
- [ ] **[OWNER]** Confirm the copyright holder and year; replace the `[YEAR]` / `[COPYRIGHT HOLDER - PLACEHOLDER]` line in `NOTICE`. Confirm Apache-2.0 is the licence you want (`LICENSE` is the canonical text).
- [ ] **[OWNER]** Review `.gitleaksignore` (4 findings, all verified fake: a test canary string, a fake AWS-shaped key assembled in a test, and two policy-rule-name lists in POC 051 tests; each entry is one commit+file+line fingerprint) and `docs/DEPENDENCY_AUDIT.md` (one accepted advisory: `diskcache`, DVC POCs 067/076 only).
- [ ] **[OWNER]** `docs/archive/`: the four notes about your other repositories (profile audit, repository review, security remediation, old profile draft; they described an exposed third-party credential by type only, no value) were **removed from the working tree**. They remain in the private branch history, so they become visible if the full history is published. Decision needed: publish history as is (they contain no secret values, but name your other repositories and the kind of leak), or publish `main` as a squashed/fresh history. History rewriting was not done and needs your explicit approval. Separately, make sure the exposed Azure/Google keys from those repositories are revoked.
- [ ] **[OWNER]** Decide whether to purge the 10 accidentally committed wheel files and 2 dbt logs from history (1.8 MB, public PyPI artefacts and sandbox logs; harmless). Default: leave as is.

## 2. Final verification on the release commit (automated; re-run if code changes)
- [ ] Fast `ci` and `heavy-validation` green on the final commit (GitHub Actions page). Status at 7cbc5d5: `ci` #55-#57 green; `heavy-validation` #22 (1b5d296; heavy and airflow jobs) green, heavy demos step took 17 min (normal).
- [ ] `python scripts/check_secrets.py` clean; `gitleaks git .` reports no leaks (with `.gitleaksignore`).
- [ ] `python scripts/build_catalogue.py && python scripts/build_status.py && git diff --exit-code` (README, docs generated and current).
- [ ] `python scripts/check_links.py` (relative links and anchors); optional `scripts/check_mermaid.py` (needs `npm install mermaid@11` and Chromium; currently 104/104 diagrams parse).

## 3. Publish the portfolio repository
1. **[OWNER]** Merge `claude/relaxed-allen-usvr0o` into `main` (pull request or fast-forward) once CI is green.
2. **[OWNER]** Repository settings: add description and topics (for example `data-engineering`, `llm`, `rag`, `mlops`, `aws`, `terraform`, `dbt`), enable branch protection with the fast `ci` check required, enable Dependabot alerts and secret scanning.
3. **[OWNER]** Change visibility to public.
4. **[OWNER]** Pin it on your profile. The README links to `main` paths, so publish after the merge.

## 4. Publish the personal profile
1. **[OWNER]** Review `profile/README.md`. It uses only the details you supplied (name, positioning, 10+ years, Dublin, listed skills); add contact links, employer or certifications yourself if you want them.
2. **[OWNER]** Create the public repository `shashwat003/shashwat003` and commit the file as its `README.md`.
3. **[OWNER]** Pin the portfolio repository (and other repositories you choose) on the profile.

## 5. After publishing
- [ ] Re-run the link check on the public URLs; fix any `main`-branch link that 404s.
- [ ] Consider a non-blocking `pip-audit` workflow and refreshing `requirements-airflow-lock.txt` when Airflow publishes security releases.
