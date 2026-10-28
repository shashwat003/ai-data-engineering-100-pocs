# Third-party notices (for review)

The repository's own code is licensed under Apache-2.0 (`LICENSE`). It does not copy or vendor third-party code. The packages below are the directly pinned dependencies listed in `requirements*.txt`; they are installed by users from PyPI under their own licences. Transitive dependencies (about 275 packages in the full test environment) are not listed individually: regenerate the full list with `pip install pip-licenses && pip-licenses --format=markdown --with-urls`.

Licence names are as reported by each package's metadata and have not been legally reviewed.

| Package | Version | Licence | Home page |
|---|---|---|---|
| alembic | 1.20.0 | MIT | https://alembic.sqlalchemy.org |
| aws-cdk.asset-awscli-v1 | 2.2.292 | Apache-2.0 | https://github.com/cdklabs/awscdk-asset-awscli#readme |
| aws-cdk.asset-node-proxy-agent-v6 | 2.1.3 | Apache-2.0 | https://github.com/cdklabs/awscdk-asset-node-proxy-agent#readme |
| aws-cdk.cloud-assembly-schema | 54.26.0 | Apache-2.0 | https://github.com/aws/aws-cdk |
| aws-cdk-lib | 2.273.0 | Apache-2.0 | https://github.com/aws/aws-cdk |
| boto3 | 1.43.114 | Apache-2.0 | https://github.com/boto/boto3 |
| cattrs | 26.2.1 | MIT License | https://catt.rs |
| cfn-lint | 1.57.2 | MIT-0 | https://github.com/aws-cloudformation/cfn-lint |
| constructs | 10.8.1 | Apache-2.0 | https://github.com/aws/constructs |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| dagster | 1.13.26 | Apache-2.0 | https://dagster.io |
| dbt-duckdb | 1.11.0 | Apache Software License | https://github.com/duckdb/dbt-duckdb |
| deltalake | 1.6.6 | Apache-2.0 | https://github.com/delta-io/delta-rs |
| duckdb | 1.5.6 | MIT License | https://github.com/duckdb/duckdb-python |
| dvc | 3.67.1 | Apache-2.0 | https://github.com/treeverse/dvc |
| fairlearn | 0.15.0 | MIT | https://github.com/fairlearn/fairlearn |
| faiss-cpu | 1.13.2 | MIT AND BSD-3-Clause | https://github.com/kyamagu/faiss-wheels |
| fastapi | 0.149.0 | MIT | https://github.com/fastapi/fastapi |
| httpx | 0.28.1 | BSD License | https://github.com/encode/httpx |
| Jinja2 | 3.1.6 | BSD License | https://github.com/pallets/jinja/ |
| jsii | 1.141.0 | Apache Software License | https://github.com/aws/jsii |
| lightgbm | 4.7.0 | MIT | https://github.com/lightgbm-org/LightGBM |
| markdown-it-py | 4.2.0 | MIT License | https://github.com/executablebooks/markdown-it-py |
| mcp | 2.3.0 | MIT License | https://modelcontextprotocol.io |
| mlflow-skinny | 3.17.1 | Apache Software License | https://mlflow.org |
| moto | 5.2.4 | Apache-2.0 | https://github.com/getmoto/moto |
| networkx | 3.7 | BSD-3-Clause | https://networkx.org/ |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | https://numpy.org |
| onnx | 1.23.2 | Apache-2.0 | https://onnx.ai/ |
| onnxruntime | 1.31.0 | MIT License | https://onnxruntime.ai |
| openlineage-python | 1.54.0 | Apache-2.0 |  |
| opentelemetry-sdk | 1.45.1 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-sdk |
| pandas | 3.0.6 | BSD License | https://pandas.pydata.org |
| pdfplumber | 0.11.10 | MIT License | https://github.com/jsvine/pdfplumber |
| playwright | 1.64.0 | Apache-2.0 | https://github.com/Microsoft/playwright-python |
| psycopg | 3.3.6 | LGPL-3.0-only | https://psycopg.org/ |
| publication | 0.0.3 | MIT License | https://github.com/glyph/publication |
| pyarrow | 26.0.0 | Apache-2.0 | https://arrow.apache.org/ |
| pydantic | 2.14.1 | MIT | https://github.com/pydantic/pydantic |
| pyiceberg | 0.12.0 | Apache-2.0 | https://py.iceberg.apache.org/ |
| pyiceberg-core | 0.10.1 | Apache Software License | https://rust.iceberg.apache.org/ |
| pyspark | 4.2.0 | Apache-2.0 | https://github.com/apache/spark/tree/master/python |
| pytest | 9.1.1 | MIT | https://docs.pytest.org/en/latest/ |
| PyYAML | 6.0.3 | MIT License | https://pyyaml.org/ |
| reportlab | 5.0.1 | BSD License | https://www.reportlab.com/ |
| river | 0.26.1 | BSD-3-Clause | https://riverml.xyz/ |
| ruff | 0.17.1 | MIT | https://docs.astral.sh/ruff |
| scikit-learn | 1.9.1 | BSD-3-Clause | https://scikit-learn.org |
| scipy | 1.18.1 | BSD License | https://scipy.org/ |
| skl2onnx | 1.20.0 | Apache Software License | https://github.com/onnx/sklearn-onnx |
| splink | 5.0.0 | MIT | https://github.com/moj-analytical-services/splink |
| SQLAlchemy | 2.1.5 | MIT | https://www.sqlalchemy.org |
| sqlglot | 30.23.0 | MIT | https://sqlglot.com/ |
| statsmodels | 0.15.0 | BSD-3-Clause | https://www.statsmodels.org |
| typeguard | 2.13.3 | MIT License |  |

## Packages with weak or strong copyleft terms (any in the full test environment, direct or transitive)

| Package | Version | Licence | Where it comes from |
|---|---|---|---|
| asyncssh | 2.24.1 | EPL-2.0 OR GPL-2.0-or-later | DVC dependency chain (dual-licensed EPL-2.0 / GPL) |
| certifi | 2026.10.18 | Mozilla Public License 2.0 (MPL 2.0) | transitive |
| dulwich | 1.2.17 | Apache-2.0 OR GPL-2.0-or-later | DVC dependency chain (dual-licensed Apache-2.0) |
| grandalf | 0.8 | GNU General Public License v2 (GPLv2) | DVC dependency chain (POCs 067, 076) |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | transitive |
| pathspec | 1.0.4 | Mozilla Public License 2.0 (MPL 2.0) | transitive |
| psycopg | 3.3.6 | LGPL-3.0-only | POC 039 (requirements-postgres.txt), LGPL: dynamic use as a library |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | POC 039, LGPL |
| pygit2 | 1.20.1 | GPLv2 with linking exception | DVC dependency chain |
| shtab | 1.12.1 | MPL-2.0 | transitive |
| text-unidecode | 1.3 | Artistic License; GNU General Public License (GPL); GNU | transitive (python-slugify) |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | transitive |

None of these is copied into the repository or redistributed by it. If you ever publish a container image or wheel that bundles them, review their terms first.


Direct pins without installed metadata in the audit environment (Airflow / Playwright extras installed elsewhere): none.
