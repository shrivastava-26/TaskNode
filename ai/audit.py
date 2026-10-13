import json
import psycopg
from config import app_db_url
from ai.security import digest

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
 request_id UUID, session_id VARCHAR(64), user_id TEXT, action TEXT, payload_hash TEXT,
 ts TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, latency_ms DOUBLE PRECISION, status TEXT, citations INT);
CREATE TABLE IF NOT EXISTS guardrail_events (request_id UUID, reason TEXT, ts TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS data_retention_policy (table_name TEXT PRIMARY KEY, retention_days INT);
INSERT INTO data_retention_policy VALUES ('audit_log',30),('guardrail_events',30),('ai_write_receipts',30)
 ON CONFLICT (table_name) DO NOTHING;
CREATE TABLE IF NOT EXISTS ai_write_receipts (nonce UUID PRIMARY KEY, result JSONB, ts TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
"""

def log(request_id, session, action, payload, latency=0, status="ok", citations=0, events=()):
    # Neither raw messages nor tool arguments/results are stored.
    with psycopg.connect(app_db_url(), connect_timeout=5) as db:
        db.execute("INSERT INTO audit_log(request_id,session_id,user_id,action,payload_hash,latency_ms,status,citations) VALUES (%s,%s,'demo',%s,%s,%s,%s,%s)",
                   (request_id, digest(session), action, digest(payload), latency, status, citations))
        for reason in events:
            db.execute("INSERT INTO guardrail_events(request_id,reason) VALUES (%s,%s)", (request_id, reason))

def report(purge=False):
    with psycopg.connect(app_db_url()) as db:
        if purge:
            for table in ("audit_log", "guardrail_events", "ai_write_receipts"):
                db.execute(f"DELETE FROM {table} WHERE ts < CURRENT_TIMESTAMP - (SELECT retention_days * INTERVAL '1 day' FROM data_retention_policy WHERE table_name=%s)", (table,))
        row = db.execute("""SELECT COUNT(*),COUNT(*) FILTER(WHERE status='blocked'),
            COALESCE(100.0*COUNT(*) FILTER(WHERE citations>0)/NULLIF(COUNT(*),0),0),COALESCE(AVG(latency_ms),0)
            FROM audit_log WHERE action='chat'""").fetchone()
        redactions = db.execute("SELECT COUNT(*) FROM guardrail_events WHERE reason LIKE 'pii_%'").fetchone()[0]
    print(json.dumps(dict(requests=row[0], blocked=row[1], pii_redaction_events=redactions, cited_requests_percent=float(row[2]), avg_latency_ms=row[3])))
