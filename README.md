# Tiny expense data platform

FastAPI CRUD → separate operational Postgres → Spark 3.5.5 → Parquet + separate analytics Postgres → Streamlit. Demo only: no authentication, use fake data. Existing `data/tasks.json` is preserved legacy data, unused by this app.

Optional RAG, private MCP tools and a LangGraph chatbot: see [README-AI.md](README-AI.md). The base command below remains AI-independent.

```mermaid
flowchart TD
  U[User] --> A[FastAPI CRUD]
  A --> O[App Postgres instance]
  O -->|JDBC| S[Spark ETL]
  S --> L[Parquet lake]
  S --> W[Warehouse Postgres instance]
  W -->|SQL queries| D[Streamlit dashboard]
```

## Local setup (Docker Desktop / Docker Engine + Compose v2)

Allocate at least 4 GB RAM to Docker. No local Java/Python installation required. First build downloads Python packages and JDBC drivers; allow several minutes with a good connection.

```bash
git clone --branch data-platform-mvp https://github.com/shrivastava-26/TaskNode.git
cd TaskNode
cp .env.example .env
docker compose up --build -d --wait --wait-timeout 600
```

Windows PowerShell: use `Copy-Item .env.example .env` instead of `cp` if needed. Docker Desktop must use Linux containers.

Open [CRUD](http://localhost:8000/app) and [dashboard](http://localhost:8501). Compose automatically seeds 200 expenses, runs Spark once, then starts the dashboard. Two Postgres containers have separate processes, credentials, and storage; neither exposes a host database port. Dashboard receives only `WAREHOUSE_URL` and does not depend on app-db.

Exact individual commands after the initial build:

```bash
# Seed only (safe to rerun; preserves edits/deletions).
docker compose run --rm spark python seed.py
# ETL only, without seed.
docker compose run --rm spark spark-submit --driver-memory 512m --jars /opt/jdbc/postgresql.jar,/opt/jdbc/redshift.jar etl/spark_job.py
# Usual seed → ETL → warehouse summary.
docker compose run --rm spark
# Launch/restart dashboard independently of app-db and Spark.
docker compose up -d --no-deps dashboard
docker compose logs --tail=100 spark
# Stop services, retain databases and lake.
docker compose down
```

`run_pipeline.sh` runs inside the Spark image. For a simple schedule, cron on your computer can run `cd /absolute/path/TaskNode && docker compose run --rm spark` at the interval you choose. Airflow adds no value to this one-job demo.

## How the data flows

1. The form writes original currency/amount into `expenses`. Editing changes `updated_at`; deletion sets `is_deleted`, preserving a tombstone.
2. Spark extracts using `spark.read.jdbc`. Its warehouse checkpoint bounds `updated_at`, with one day of overlap to replay recent late commits. This demo assumes no timestamp backdating or transactions open over a day.
3. Spark unions changes with previously committed raw Parquet, removes exact duplicates, then keeps the latest version per ID **before** filtering deletions. It joins demo FX rates and stable category keys, derives the UTC expense date, and computes USD amounts. Rates: USD=1, INR=0.012, EUR=1.08; these are teaching constants.
4. Spark groups by date/category to calculate total spend and transaction count. `.explain()` prints the physical plan; `SPARK_SHUFFLE_PARTITIONS=4` keeps this tiny shuffle small. `SPARK_MASTER=local[*]` uses local CPU cores, not a multi-machine cluster.
5. Spark writes snapshots to `datalake/dim_category/`, `datalake/fact_expenses/expense_date=YYYY-MM-DD/`, and `datalake/agg_daily_category_spend/expense_date=YYYY-MM-DD/`. Full overwrite is deliberate at this scale: it removes obsolete/empty partitions after edits or deletions. The partition key follows the required model rather than renaming it to `dt`.
6. JDBC writes `stage_*` tables. One SQL transaction replaces all three final tables and advances `etl_state`. Dashboard queries **only** those final warehouse tables; refresh does not run ETL. Replays rebuild snapshots instead of appending facts.

Immutable raw checkpoints live under `datalake/_state/<run>/raw/`; `etl_state.raw_path` points to the last successful version. Keep this folder and its `/workspace/datalake` mount path intact. Failed warehouse publication retains the previous warehouse/checkpoint; the visible lake may already have changed, so rerun to repair it. Lake + warehouse are not one atomic transaction. Old raw checkpoints are retained, so storage grows until manually cleaned. Run only one pipeline against a warehouse; the local file lock only coordinates jobs sharing the same lake folder.

## Free cloud setup: Render + two Neon projects

This is a **hybrid** free deployment: web apps/databases online, Spark + durable lake on your computer. It does not promise an always-on free Spark VM.

1. Sign up at [Neon](https://neon.com/). Create **two projects**, `expenses-oltp` and `expenses-warehouse`, preferably in the same region. Use each project's Connect panel to copy its direct Postgres URL with `sslmode=require`. Two schemas/databases/branches on one server are insufficient for this demo.
2. Put those URLs into your local `.env` as `APP_DB_URL` and `WAREHOUSE_URL`. Keep `WAREHOUSE_KIND=postgresql`. Never commit `.env`.
3. Build the Spark image if necessary, then seed and load the remote databases:
   ```bash
   docker compose build spark
   docker compose run --rm --no-deps spark
   ```
4. Open [Render's Blueprint setup](https://dashboard.render.com/blueprint/new?repo=https://github.com/shrivastava-26/TaskNode). Connect GitHub, select **data-platform-mvp** as the Blueprint branch, and use `render.yaml`. Both services explicitly use this branch and `plan: free`.
5. Supply `APP_DB_URL` only to `tasknode-expenses`; supply `WAREHOUSE_URL` only to `tasknode-analytics`. Apply the Blueprint. Open the URLs Render gives you: `/app` for CRUD and `/` for Streamlit. Port binding and health checks are configured.
6. After cloud CRUD changes, run `docker compose run --rm --no-deps spark` locally and refresh the cloud dashboard. The local lake remains durable while the warehouse receives the new snapshot.

Render free services sleep after idle time, have shared monthly usage limits, and cannot attach persistent disks. Its free Postgres expires after 30 days, which is why this setup uses Neon instead. Check [Render limits](https://render.com/docs/free) and [Neon pricing](https://neon.com/pricing) before deploying; free quotas may change. Render fits these long-running Python web apps without converting them to Vercel functions.

## Redshift alternative (connection settings only)

Use AWS Console → Redshift Serverless → create a namespace/workgroup, database, and password-based database user. Configure a reachable endpoint/security group allowing your ETL computer and dashboard host. Confirm trial eligibility and billing in your account; this project does not provision paid resources.

```dotenv
WAREHOUSE_KIND=redshift
WAREHOUSE_URL=redshift://USER:PASSWORD@WORKGROUP_ENDPOINT:5439/analytics?sslmode=require
```

Update the dashboard's `WAREHOUSE_URL` as well. Both JDBC drivers are bundled; no code changes are intended. Start with an empty warehouse and retain the same local lake path. Spark uses Redshift JDBC; dashboard/publication use its Postgres-compatible wire protocol. **Redshift integration is unverified without an AWS account**. See [AWS setup](https://docs.aws.amazon.com/redshift/latest/mgmt/serverless-getting-started.html).

## Acceptance checks and troubleshooting

The Actions workflow runs `python tests/smoke.py` on a fresh Compose stack: seed, JDBC/Parquet load, replay, CRUD edits/deletion, aggregate counts, and dashboard query while app-db is stopped. Run it locally with host Python 3.10+ (stdlib only); use a fresh demo dataset with 200 active rows. It takes several Spark runs. It deletes its own test expense and restarts app-db in `finally`.

Database inspection:
```bash
docker compose exec app-db psql -U app_user -d expenses -c "SELECT id, amount, is_deleted FROM expenses ORDER BY id DESC LIMIT 5"
docker compose exec warehouse-db psql -U analytics_user -d analytics -c "SELECT * FROM agg_daily_category_spend LIMIT 5"
```

Missing analytics tables: run ETL. Unreachable warehouse: dashboard shows a clear error. Missing committed raw files: restore the lake; do not blindly advance/reset checkpoints. Changed source URL: use a new warehouse/lake, or deliberately rebuild the demo after reviewing stored data. Check `docker compose logs app dashboard` and the pipeline exit status for failures.

## What I'd add in production (out of scope)

Airflow scheduling/retries, dbt models and tests, Iceberg/Delta transactions, CDC or a durable change log, incremental MERGE, atomic lake publication, data quality checks, monitored freshness, secrets/auth/read-only dashboard credentials, backup/retention, and CI/CD deployment gates. This demo includes one acceptance workflow, not a production delivery platform.
