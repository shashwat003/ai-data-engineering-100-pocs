# Results (measured by `python -m airflow_dag_factory.main`; Airflow 3.3.2, dag.test() in-process, SQLite metadata DB)

- import errors: none
- **fan_in**: 4 tasks, schedule `@hourly`, tags ['demo'], test run: **success**
- **orders_daily**: 4 tasks, schedule `0 3 * * *`, tags ['daily', 'orders'], test run: **success**
- **strict_quality**: 3 tasks, schedule `None`, tags ['quality'], test run: **failed**
