# Results: read-only MCP server over stdio against a MOCK repository

Server `repo-ops-readonly` exposes 6 tools (list_pull_requests, get_pull_request, list_workflow_runs, get_run_failure, ci_health_summary, search_commits); all annotated read-only: True. Data comes from a synthetic in-memory repository; no GitHub API was called.

| tool | arguments | error | reply chars |
|---|---|---|---|
| ci_health_summary | {'days': 7} | False | 259 |
| list_workflow_runs | {'conclusion': 'failure', 'branch': 'main', 'limit': 3} | False | 515 |
| get_run_failure | {'run_id': 1150} | False | 425 |
| get_pull_request | {'number': 17} | False | 521 |
| list_pull_requests | {'state': 'open', 'limit': 3} | False | 610 |
| search_commits | {'query': 'cdc', 'limit': 3} | False | 436 |

Refused or rejected requests:

| tool | arguments | rejected |
|---|---|---|
| get_run_failure | {'run_id': 1000, 'repo': 'other/private'} | True |
| create_issue | {'title': 'x'} | True |
| list_workflow_runs | {'branch': 'main; rm -rf /'} | True |
| list_pull_requests | {'limit': 10000} | True |
| get_pull_request | {'number': '17 OR 1=1'} | True |
| search_commits | {'query': 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx | True |

Fake token leaked into any reply: False; email leaked: False.
