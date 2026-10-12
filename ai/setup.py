"""Explicit provisioning: admin credentials are never used by the query tool."""
import os
from urllib.parse import urlsplit, unquote
import psycopg
from psycopg import sql
from config import app_db_url, warehouse_url, validate_separation
from ai.audit import SCHEMA

def setup():
    validate_separation()
    with psycopg.connect(app_db_url()) as db:
        db.execute(SCHEMA)
        db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        db.execute("CREATE TABLE IF NOT EXISTS doc_embeddings(id TEXT PRIMARY KEY, content TEXT, metadata JSONB, content_hash TEXT, embedding VECTOR(384))")
    reader = urlsplit(os.environ["WAREHOUSE_READ_URL"])
    if reader.username != "chat_reader":
        raise ValueError("Use the dedicated chat_reader role")
    with psycopg.connect(warehouse_url()) as db:
        db.execute("""CREATE OR REPLACE VIEW ai_expenses AS SELECT f.expense_id, f.amount_usd,
            f.expense_date, f.loaded_at, d.category_name FROM fact_expenses f JOIN dim_category d USING(category_key);
            CREATE OR REPLACE VIEW ai_daily AS SELECT * FROM agg_daily_category_spend;
            CREATE OR REPLACE VIEW ai_categories AS SELECT * FROM dim_category;""")
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='chat_reader'").fetchone():
            db.execute("CREATE ROLE chat_reader")
        db.execute(sql.SQL("ALTER ROLE chat_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD {}").format(sql.Literal(unquote(reader.password or ""))))
        db.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM chat_reader")
        db.execute("GRANT USAGE ON SCHEMA public TO chat_reader")
        db.execute("GRANT SELECT ON ai_expenses,ai_daily,ai_categories TO chat_reader")
    print("AI storage and dedicated read-only analytics role ready")

if __name__ == "__main__":
    setup()
