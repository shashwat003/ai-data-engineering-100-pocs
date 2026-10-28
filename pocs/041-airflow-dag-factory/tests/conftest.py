import pytest
from airflow_dag_factory import env

HOME = env.setup()  # must run before any airflow import


@pytest.fixture(scope="session")
def bag():
    return env.load_dagbag(env.ROOT / "dags")
