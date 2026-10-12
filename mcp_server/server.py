"""Private stdio MCP server: exactly two tools; never bind a public HTTP port."""
import json
import os
import time
from uuid import UUID, uuid4
import psycopg
from psycopg.rows import dict_row
from mcp.server.fastmcp import FastMCP
from config import app_db_url
from ai.security import Draft, safe_sql, verify, digest
from ai.audit import log
from app.service import save_record, delete_record

mcp = FastMCP("Expense tools")

@mcp.tool()
def query_warehouse(sql: str, session_id: str, request_id: str) -> dict:
    """Read only the approved anonymized warehouse views, at most 100 rows."""
    UUID(session_id); UUID(request_id)
    start = time.monotonic()
    try:
        checked = safe_sql(sql)
        with psycopg.connect(os.environ["WAREHOUSE_READ_URL"], row_factory=dict_row, connect_timeout=5) as db:
            db.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            db.execute("SET TRANSACTION READ ONLY")
            db.execute("SET LOCAL statement_timeout='3s'")
            rows = db.execute(checked).fetchall()
            freshness = db.execute("SELECT MAX(loaded_at) AS ts FROM ai_expenses").fetchone()["ts"]
        log(request_id, session_id, "query_warehouse", sql, (time.monotonic()-start)*1000)
        return {"sql": checked, "rows": json.loads(json.dumps(rows, default=str)), "latest_load": str(freshness)}
    except Exception:
        log(request_id, session_id, "query_warehouse", sql, status="blocked", events=("sql_or_database_rejected",))
        raise ValueError("Query rejected or warehouse unavailable") from None

@mcp.tool()
def crud_expense(approval: str) -> dict:
    """Execute an exact server-signed, user-confirmed write. Never invent approval."""
    start = time.monotonic()
    try:
        payload = verify(approval)
    except Exception:
        log(str(uuid4()), "unverified", "crud_expense", approval, status="blocked", events=("approval_rejected",))
        raise ValueError("Invalid or expired approval") from None
    draft = Draft.model_validate(payload["draft"])
    with psycopg.connect(app_db_url(), row_factory=dict_row, connect_timeout=5) as db:
        # One DB transaction makes replay/crash recovery safe without double writes.
        claimed = db.execute("INSERT INTO ai_write_receipts(nonce,result) VALUES (%s,NULL) ON CONFLICT DO NOTHING RETURNING nonce", (payload["nonce"],)).fetchone()
        if not claimed:
            result = db.execute("SELECT result FROM ai_write_receipts WHERE nonce=%s", (payload["nonce"],)).fetchone()["result"]
        else:
            if draft.action == "delete":
                expense_id = delete_record(db, draft.expense_id)
            else:
                expense_id = save_record(db, ("demo@example.com", draft.category, draft.amount, draft.currency, draft.description),
                                         draft.expense_id if draft.action == "update" else None)
            result = {"expense_id": expense_id, "action": draft.action}
            db.execute("UPDATE ai_write_receipts SET result=%s WHERE nonce=%s", (json.dumps(result), payload["nonce"]))
        db.execute("INSERT INTO audit_log(request_id,session_id,user_id,action,payload_hash,latency_ms,status,citations) VALUES (%s,%s,'demo','crud_expense',%s,%s,'ok',0)",
                   (payload["request_id"], digest(payload["session_id"]), digest(payload["draft"]), (time.monotonic()-start)*1000))
    return result

if __name__ == "__main__":
    mcp.run(transport="stdio")
