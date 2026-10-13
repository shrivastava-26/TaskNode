import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
import yaml
from config import app_db_url, warehouse_url

@lru_cache(maxsize=1)
def model():
    from fastembed import TextEmbedding
    return TextEmbedding("sentence-transformers/all-MiniLM-L6-v2", cache_dir=os.getenv("AI_MODEL_CACHE", "/tmp/fastembed"), threads=2)

def embed(text):
    return json.dumps(next(model().embed([text])).tolist())

def index():
    metrics = yaml.safe_load(Path(__file__).with_name("metrics.yaml").read_text())
    docs = [("metric:"+key, value) for key, value in metrics.items()]
    with psycopg.connect(warehouse_url(), row_factory=dict_row) as db:
        for table in ("fact_expenses", "dim_category", "agg_daily_category_spend"):
            columns = db.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,)).fetchall()
            docs.append(("schema:"+table, table+": "+json.dumps(columns)))
        samples = db.execute("SELECT * FROM agg_daily_category_spend ORDER BY expense_date DESC,category_name LIMIT 50").fetchall()
        for row in samples:
            docs.append((f"sample:{row['expense_date']}:{row['category_name']}", "Illustrative snapshot sample, NOT live totals: "+json.dumps(row, default=str)))
    changed = 0
    with psycopg.connect(app_db_url()) as db:
        for doc_id, content in docs:
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            prior = db.execute("SELECT content_hash FROM doc_embeddings WHERE id=%s", (doc_id,)).fetchone()
            if prior and prior[0] == content_hash:
                continue
            vector = embed(content)
            db.execute("""INSERT INTO doc_embeddings VALUES (%s,%s,%s,%s,%s::vector)
                ON CONFLICT(id) DO UPDATE SET content=EXCLUDED.content,metadata=EXCLUDED.metadata,
                content_hash=EXCLUDED.content_hash,embedding=EXCLUDED.embedding""",
                (doc_id, content, json.dumps({"kind": doc_id.split(":")[0]}), content_hash, vector))
            changed += 1
        db.execute("DELETE FROM doc_embeddings WHERE NOT (id=ANY(%s))", ([d[0] for d in docs],))
    print(f"RAG index: {len(docs)} documents; {changed} changed (0 means no-op)")
    return changed

def retrieve(question):
    with psycopg.connect(app_db_url(), row_factory=dict_row, connect_timeout=5) as db:
        # Prefer definitions/schema; illustrative samples must not answer live amounts.
        return db.execute("SELECT id,content FROM doc_embeddings WHERE metadata->>'kind' != 'sample' ORDER BY embedding <=> %s::vector LIMIT 4", (embed(question),)).fetchall()

if __name__ == "__main__":
    index()
