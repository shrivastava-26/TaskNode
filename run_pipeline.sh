#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python seed.py
spark-submit --driver-memory 512m --jars /opt/jdbc/postgresql.jar,/opt/jdbc/redshift.jar etl/spark_job.py
python - <<'PY'
import psycopg
from config import warehouse_url
with psycopg.connect(warehouse_url()) as db:
    print('Warehouse (facts, USD total, latest load):', db.execute(
        'SELECT COUNT(*), COALESCE(SUM(amount_usd),0), MAX(loaded_at) FROM fact_expenses').fetchone())
PY
