from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import app_db_url

CATEGORIES = ("Food", "Transport", "Shopping", "Bills", "Entertainment")
CURRENCIES = ("USD", "INR", "EUR")
SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id BIGSERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN
        ('Food', 'Transport', 'Shopping', 'Bills', 'Entertainment')),
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL CHECK (currency IN ('USD', 'INR', 'EUR')),
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS expenses_updated_at_idx ON expenses(updated_at);
"""


def connect():
    return psycopg.connect(app_db_url(), row_factory=dict_row, connect_timeout=5)


def initialize():
    with connect() as db:
        db.execute(SCHEMA)


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title="Expenses OLTP", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": "Application database unavailable. Try again shortly."})


@app.get("/health")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return {"status": "ok", "database": "OLTP"}


@app.get("/")
def root():
    return RedirectResponse("/app", status_code=303)


@app.get("/app")
def expense_list(request: Request, edit: int | None = None):
    with connect() as db:
        rows = db.execute("SELECT * FROM expenses WHERE NOT is_deleted ORDER BY created_at DESC, id DESC").fetchall()
        selected = None
        if edit is not None:
            selected = db.execute("SELECT * FROM expenses WHERE id=%s AND NOT is_deleted", (edit,)).fetchone()
            if selected is None:
                raise HTTPException(404, "Expense not found")
    return templates.TemplateResponse(request=request, name="expenses.html", context={
        "rows": rows, "expense": selected, "categories": CATEGORIES, "currencies": CURRENCIES,
    })


@app.post("/app/save")
def save_expense(
    category: Annotated[Literal["Food", "Transport", "Shopping", "Bills", "Entertainment"], Form()],
    currency: Annotated[Literal["USD", "INR", "EUR"], Form()],
    amount: Annotated[Decimal, Form(gt=0, le=Decimal("9999999999.99"), decimal_places=2)],
    user_email: Annotated[str, Form(min_length=3, max_length=254)] = "demo@example.com",
    description: Annotated[str, Form(max_length=500)] = "",
    expense_id: Annotated[int | None, Form(gt=0)] = None,
):
    if "@" not in user_email or any(c.isspace() for c in user_email):
        raise HTTPException(422, "Enter an email address")
    values = (user_email, category, amount, currency, description)
    with connect() as db:
        if expense_id is None:
            db.execute("""INSERT INTO expenses(user_email, category, amount, currency, description)
                       VALUES (%s, %s, %s, %s, %s)""", values)
        else:
            updated = db.execute("""UPDATE expenses SET user_email=%s, category=%s, amount=%s,
                currency=%s, description=%s, updated_at=clock_timestamp()
                WHERE id=%s AND NOT is_deleted RETURNING id""", (*values, expense_id)).fetchone()
            if updated is None:
                raise HTTPException(404, "Expense not found")
    return RedirectResponse("/app", status_code=303)


@app.post("/app/{expense_id}/delete")
def delete_expense(expense_id: int):
    # Keep the row so incremental ETL can observe the deletion.
    with connect() as db:
        deleted = db.execute("""UPDATE expenses SET is_deleted=TRUE, updated_at=clock_timestamp()
            WHERE id=%s AND NOT is_deleted RETURNING id""", (expense_id,)).fetchone()
        if deleted is None:
            raise HTTPException(404, "Expense not found")
    return RedirectResponse("/app", status_code=303)
