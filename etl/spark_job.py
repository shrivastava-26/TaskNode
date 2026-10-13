"""Tiny incremental extract + full snapshot rebuild. Run only one pipeline at a time.

Warehouse publication is transactional. Its checkpoint points to immutable raw
Parquet, so a failed publication cannot advance the input state or watermark.
The visible lake snapshot is replaced each run; after failure, rerun to repair it.
"""
import fcntl
import hashlib
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from pyspark.sql import SparkSession, Window, functions as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import app_db_url, jdbc, validate_separation, warehouse_url

CATEGORIES = ["Food", "Transport", "Shopping", "Bills", "Entertainment"]
TABLES = {
    "dim_category": "category_key VARCHAR(64), category_name VARCHAR(32)",
    "fact_expenses": "expense_id BIGINT, user_email VARCHAR(254), category_key VARCHAR(64), "
                     "amount_usd DECIMAL(14,2), expense_date DATE, loaded_at TIMESTAMP",
    "agg_daily_category_spend": "expense_date DATE, category_name VARCHAR(32), "
                                "total_amount DECIMAL(24,2), txn_count BIGINT",
}


def transform(spark, raw):
    # Exact duplicate removal, then latest version per ID. Filter deletions AFTER
    # latest-version selection, otherwise an old live row could resurrect.
    latest = raw.dropDuplicates().withColumn("version", F.row_number().over(
        Window.partitionBy("id").orderBy(F.col("updated_at").desc())))
    latest = latest.filter("version = 1").drop("version")
    active = latest.filter(~F.col("is_deleted"))

    # Hardcoded DEMO exchange rates, not live rates or accounting-grade FX.
    rates = spark.createDataFrame([("USD", "1.000000"), ("INR", "0.012000"), ("EUR", "1.080000")],
                                  ["currency", "rate"]).withColumn("rate", F.col("rate").cast("decimal(12,6)"))
    if active.filter(~F.col("currency").isin("USD", "INR", "EUR")).limit(1).count():
        raise ValueError("Unsupported currency in source; refusing to silently drop rows")
    if active.filter(~F.col("category").isin(*CATEGORIES)).limit(1).count():
        raise ValueError("Unsupported category in source")
    dimension = spark.createDataFrame([(c,) for c in CATEGORIES], ["category_name"]).withColumn(
        "category_key", F.sha2("category_name", 256)).select("category_key", "category_name")

    # Join FX and stable category keys; derive the UTC date; round converted USD.
    converted = active.join(F.broadcast(rates), "currency").withColumn(
        "amount_usd", F.round(F.col("amount") * F.col("rate"), 2).cast("decimal(14,2)"))
    enriched = converted.join(F.broadcast(dimension), converted.category == dimension.category_name)
    fact = enriched.withColumn("expense_date", F.to_date("created_at")).withColumn(
        "loaded_at", F.current_timestamp()).select(
        F.col("id").alias("expense_id"), "user_email", "category_key", "amount_usd", "expense_date", "loaded_at")

    # This groupBy causes a Spark shuffle: rows sharing date/category are combined.
    aggregate = fact.join(dimension, "category_key").groupBy("expense_date", "category_name").agg(
        F.sum("amount_usd").cast("decimal(24,2)").alias("total_amount"), F.count("expense_id").alias("txn_count"))
    return latest, {"dim_category": dimension, "fact_expenses": fact, "agg_daily_category_spend": aggregate}


def run(spark, lake):
    validate_separation()
    app_url, app_properties = jdbc(app_db_url())
    warehouse_jdbc, warehouse_properties = jdbc(os.environ["WAREHOUSE_URL"], os.getenv("WAREHOUSE_KIND", "postgresql"))
    # Include source identity so changing APP_DB_URL cannot reuse another source's state.
    source_hash = hashlib.sha256(app_url.encode()).hexdigest()
    with psycopg.connect(warehouse_url(), connect_timeout=15) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS etl_state (
            watermark TIMESTAMPTZ, raw_path VARCHAR(4096), source_hash VARCHAR(64), loaded_at TIMESTAMPTZ)""")
        state = db.execute("SELECT watermark, raw_path, source_hash FROM etl_state").fetchone()
    if state and state[2] != source_hash:
        raise ValueError("Source changed. Use a new warehouse/lake or deliberately reset etl_state.")
    if state and not Path(state[1]).exists():
        raise FileNotFoundError("Committed raw Parquet state is missing; restore the lake before running ETL")
    with psycopg.connect(app_db_url(), connect_timeout=15) as db:
        cutoff = db.execute("SELECT clock_timestamp()").fetchone()[0]

    # A one-day overlap catches recent late commits. Latest-ID dedupe makes replay
    # safe. This demo assumes no backdating and no transaction open over one day.
    lower = state[0] - timedelta(days=1) if state else None
    where = f"updated_at <= TIMESTAMPTZ '{cutoff.isoformat()}'"
    if lower:
        where += f" AND updated_at >= TIMESTAMPTZ '{lower.isoformat()}'"
    delta = spark.read.jdbc(app_url, f"(SELECT * FROM expenses WHERE {where}) extracted", properties=app_properties).cache()
    print(f"EXTRACT: {delta.count()} changed/replayed rows; cutoff={cutoff.isoformat()}", flush=True)
    combined = spark.read.parquet(state[1]).unionByName(delta) if state else delta
    latest, outputs = transform(spark, combined)
    raw_path = str((lake / "_state" / uuid4().hex / "raw").resolve())
    latest.write.mode("overwrite").parquet(raw_path)
    outputs["fact_expenses"] = outputs["fact_expenses"].cache()
    fact_count = outputs["fact_expenses"].count()
    print(f"TRANSFORM: {fact_count} active deduplicated expenses", flush=True)
    outputs["agg_daily_category_spend"].explain()

    # Full snapshot overwrite is intentional for ~200 rows. It also removes empty
    # partitions after deletions/date changes; dynamic partition overwrite would not.
    for name, frame in outputs.items():
        writer = frame.write.mode("overwrite")
        if "expense_date" in frame.columns:
            writer = writer.partitionBy("expense_date")
        writer.parquet(str(lake / name))
        frame.write.mode("overwrite").option("createTableColumnTypes", TABLES[name]).jdbc(
            warehouse_jdbc, f"stage_{name}", properties=warehouse_properties)
        print(f"STAGED: {name}={frame.count()} rows; Parquet + warehouse JDBC", flush=True)

    # Spark JDBC writes are separate transactions. Publish all three staging tables
    # together via one SQL transaction so readers retain the old snapshot on failure.
    with psycopg.connect(warehouse_url(), connect_timeout=15) as db:
        for name, schema in TABLES.items():
            db.execute(f"CREATE TABLE IF NOT EXISTS {name} ({schema})")
            db.execute(f"DELETE FROM {name}")
            db.execute(f"INSERT INTO {name} SELECT * FROM stage_{name}")
        db.execute("DELETE FROM etl_state")
        db.execute("INSERT INTO etl_state VALUES (%s, %s, %s, CURRENT_TIMESTAMP)", (cutoff, raw_path, source_hash))
    # Previous state files are kept for recovery; routine cleanup is out of scope.
    print(f"LOADED: {fact_count} facts; warehouse committed; watermark advanced", flush=True)
    outputs["fact_expenses"].unpersist()
    delta.unpersist()


if __name__ == "__main__":
    lake = Path(os.getenv("LAKE_PATH", "./datalake")).resolve()
    lake.mkdir(parents=True, exist_ok=True)
    with (lake / ".pipeline.lock").open("w") as lock:
        # Serial execution protects fixed staging names and full lake overwrites.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        spark = SparkSession.builder.appName("Expense ETL").master(os.getenv("SPARK_MASTER", "local[*]")).config(
            "spark.sql.session.timeZone", "UTC").config("spark.sql.shuffle.partitions", os.getenv(
            "SPARK_SHUFFLE_PARTITIONS", "4")).getOrCreate()
        spark.sparkContext.setLogLevel("WARN")
        try:
            run(spark, lake)
        finally:
            spark.stop()
