# Dependency audit (pip-audit) and hardening log

Reproduce: `pip install pip-audit`, then `pip-audit --local` (main test environment) or `pip-audit --path <venv>/lib/python3.13/site-packages` (isolated Airflow environment); classify with `python scripts/classify_advisories.py audit.json imported.txt` after `pip-audit -f json -o audit.json`.

**Limits of this review.** CVSS scores and exploit data could not be fetched (the OSV and GitHub advisory APIs are not reachable from the build sandbox), so **severity is not asserted**. Advisories are grouped by *impact class* parsed from the advisory text (a keyword heuristic, which can over-state, for example "deserialization" in a description) and by the *precondition* needed to exploit them. "Imported" means the package is loaded when POC 041's tests run (measured with `sys.modules`), which is an upper bound on what is executed.

## 1. Before: Airflow 3.1.8 + official constraints (POC 041 only)
162 advisory rows = **102 unique advisories in 16 packages** (rows are duplicated across PyPI/OSV aliases).

| Package | Version | Unique advisories | Impact classes | Highest fixed version | Imported by POC | Precondition(s) |
|---|---|---|---|---|---|---|
| aiosmtplib | 5.1.0 | 2 | remote code execution x1, injection / XSS / SSRF x1 | 5.1.2 | no | needs a running web UI/API or attacker-supplied URLs/headers; needs attacker-controlled input to the affected parser/loader or a writable trusted loca |
| anyio | 4.12.1 | 2 | other / unclassified x2 | 4.14.2 | yes | see advisory text |
| apache-airflow | 3.1.8 | 40 | authentication / authorisation bypass x26, remote code execution x6, information disclosure x6, other / unclassified x2 | 3.3.2 | yes | needs a running multi-user API server / web UI; needs a running server or shared logs/connections; needs attacker-controlled input to the affected par |
| apache-airflow-core | 3.1.8 | 8 | information disclosure x4, remote code execution x2, authentication / authorisation bypass x2 | 3.2.2 | yes | needs a running multi-user API server / web UI; needs a running server or shared logs/connections; needs attacker-controlled input to the affected par |
| apache-airflow-providers-smtp | 2.4.2 | 1 | information disclosure x1 | 3.0.0 | no | needs a running server or shared logs/connections |
| cryptography | 45.0.7 | 7 | information disclosure x4, other / unclassified x2, authentication / authorisation bypass x1 | 50.0.0 | yes | needs a running multi-user API server / web UI; needs a running server or shared logs/connections; see advisory text |
| fsspec | 2026.2.0 | 1 | remote code execution x1 | 2026.6.0 | yes | needs attacker-controlled input to the affected parser/loader or a writable trusted location |
| idna | 3.11 | 1 | denial of service x1 | 3.15 | yes | needs network-reachable service or attacker-controlled input size |
| mako | 1.3.10 | 1 | authentication / authorisation bypass x1 | 1.3.12 | yes | needs a running multi-user API server / web UI |
| pygments | 2.19.2 | 1 | other / unclassified x1 | 2.20.0 | yes | see advisory text |
| pyjwt | 2.11.0 | 16 | authentication / authorisation bypass x10, injection / XSS / SSRF x6 | 2.15.0 | yes | needs a running multi-user API server / web UI; needs a running web UI/API or attacker-supplied URLs/headers |
| python-multipart | 0.0.22 | 5 | denial of service x2, injection / XSS / SSRF x2, other / unclassified x1 | 0.0.31 | yes | needs a running web UI/API or attacker-supplied URLs/headers; needs network-reachable service or attacker-controlled input size; see advisory text |
| requests | 2.32.5 | 1 | other / unclassified x1 | 2.33.0 | no | see advisory text |
| sqlparse | 0.5.5 | 5 | denial of service x2, remote code execution x2, authentication / authorisation bypass x1 | 0.6.0 | no | needs a running multi-user API server / web UI; needs attacker-controlled input to the affected parser/loader or a writable trusted location; needs ne |
| starlette | 0.48.0 | 6 | authentication / authorisation bypass x4, injection / XSS / SSRF x2 | 1.3.1 | yes | needs a running multi-user API server / web UI; needs a running web UI/API or attacker-supplied URLs/headers |
| urllib3 | 2.6.3 | 5 | injection / XSS / SSRF x2, denial of service x2, authentication / authorisation bypass x1 | 2.8.0 | no | needs a running multi-user API server / web UI; needs a running web UI/API or attacker-supplied URLs/headers; needs network-reachable service or attac |

**Direct vs transitive.** Direct: `apache-airflow` 3.1.8 (40 unique; 8 more against its `apache-airflow-core` component). Everything else is transitive through Airflow: `starlette`, `python-multipart`, `pyjwt`, `anyio` (API server stack), `cryptography`, `urllib3`, `requests`, `idna` (clients and crypto), `sqlparse`, `mako`, `pygments`, `fsspec`, `aiosmtplib` and the SMTP provider. Most were pinned *by the official Airflow constraints file* of that release, so they could not be fixed by upgrading individual packages without leaving the constraints.

**Was the affected code executed?** POC 041 builds DAGs from YAML, loads them into a DagBag and runs them in-process with `dag.test()` against a throw-away SQLite database. No API server, web UI, scheduler, triggerer, SMTP, outbound HTTP or multi-user authentication is started (`urllib3`, `requests`, `sqlparse`, `aiosmtplib` and the SMTP provider are not even imported). The Airflow, Airflow-core, PyJWT, Starlette and python-multipart advisories (75 of the 102) are predominantly about the API server, web UI, authentication or multi-user deployments, which this usage never starts, so they were **not exploitable here** (this is a judgement from the advisory text, not a proof for each one); the remainder concern components that are imported but not exercised in a hostile setting (connections encryption, remote filesystems, templating of trusted migrations).

## 2. Remediation (isolated environment, not blind upgrades)
1. New virtualenv with `apache-airflow==3.3.2` and its **official 3.3.2 constraints** (the release that carries the fixes for every Airflow advisory): 102 -> **5 unique advisories** (PyJWT 2.14.0 x2, urllib3 2.7.0 x3).
2. Raised those two above the constraints: `PyJWT>=2.15` -> 2.15.1, `urllib3>=2.8` -> 2.8.0. `pip check`: no broken requirements; **`pip-audit`: no known vulnerabilities**.
3. Compatibility: Airflow 3.3 moved `DagBag` to `airflow.dag_processing.dagbag` and removed `include_examples`. POC 041 now loads it through `env.load_dagbag()`, which supports both lines (examples are disabled through `AIRFLOW__CORE__LOAD_EXAMPLES`).
4. Re-verified: POC 041 tests **23 passed on both Airflow 3.1.8 and 3.3.2** (3.3.2 plus overrides, and again from a fresh venv built from the lock); `make demo` runs and regenerates the same DAG results; the CI `airflow` job installs the exact lock `requirements-airflow-lock.txt` (133 pinned lines, Python 3.13 / Linux).

## 3. After
| Environment | Result |
|---|---|
| Isolated Airflow 3.3.2 lock (POC 041) | **0 known vulnerabilities**, `pip check` clean |
| Main test environment (287 packages) | **1 advisory**: `diskcache 5.6.3`, CVE-2025-69872 / PYSEC-2026-2447 (impact class: unsafe pickle deserialisation if an attacker can write the cache directory). **No fixed release exists.** |
| Secret scan (`check_secrets.py`) and gitleaks full history | clean; only the 4 documented false positives in `.gitleaksignore` |

### Remaining advisory: diskcache (documented acceptance)
- Pulled in transitively by `dvc-data` through `requirements-dvc.txt` (POCs 067 and 076 only).
- Exploit precondition: write access to the cache directory (arbitrary code execution when a victim loads a tampered pickle entry). The POCs create their DVC cache inside their own temporary or working directory and never read a cache from an untrusted location.
- Usage restriction: run POCs 067/076 only with caches created by the same run; do not point DVC at shared or downloaded caches. Re-audit when `diskcache` or `dvc-data` publishes a fix; remove the pin of DVC if it cannot be kept clean.

## 4. Policy going forward
Run `pip-audit` in CI as an advisory (non-blocking) job, refresh `requirements-airflow-lock.txt` from the matching official constraints when Airflow releases a security fix, and keep Airflow out of the main environment.
