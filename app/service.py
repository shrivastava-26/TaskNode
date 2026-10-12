"""Shared CRUD SQL: UI and approved MCP operations use the same implementation."""
from fastapi import HTTPException


def save_record(db, values, expense_id=None):
    if expense_id is None:
        return db.execute("""INSERT INTO expenses(user_email, category, amount, currency, description)
            VALUES (%s,%s,%s,%s,%s) RETURNING id""", values).fetchone()["id"]
    row = db.execute("""UPDATE expenses SET user_email=%s,category=%s,amount=%s,currency=%s,
        description=%s,updated_at=clock_timestamp() WHERE id=%s AND NOT is_deleted RETURNING id""",
        (*values, expense_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Expense not found")
    return row["id"]


def delete_record(db, expense_id):
    row = db.execute("""UPDATE expenses SET is_deleted=TRUE,updated_at=clock_timestamp()
        WHERE id=%s AND NOT is_deleted RETURNING id""", (expense_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Expense not found")
    return row["id"]
