"""Bounded demo controls, not an injection-proof or comprehensive PII detector."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
import sqlglot
from sqlglot import exp

class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "update", "delete"]
    expense_id: int | None = Field(default=None, gt=0)
    category: Literal["Food", "Transport", "Shopping", "Bills", "Entertainment"] = "Food"
    amount: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("9999999999.99"), decimal_places=2)
    currency: Literal["USD", "INR", "EUR"] = "USD"
    description: str = Field(default="", max_length=500)

PII = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b\d{3}-\d{2}-\d{4}\b|(?<!\w)(?:\+\d{1,3}[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}(?!\w)|\b(?:\d[ -]?){13,19}\b")
INJECTION = re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|system\s+prompt|you\s+are\s+now|\b(?:drop|truncate)\s+(?:table|the\s+\w+\s+table)|[A-Za-z0-9+/]{120,}={0,2}", re.I)

def redact(text):
    return PII.sub("[REDACTED]", text), len(PII.findall(text))

def digest(value):
    return hmac.new(os.environ["CHAT_ACCESS_TOKEN"].encode(), json.dumps(value, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()

def sign(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    mac = hmac.new(os.environ["WRITE_APPROVAL_KEY"].encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + mac

def verify(token):
    body, mac = token.split(".")
    expected = hmac.new(os.environ["WRITE_APPROVAL_KEY"].encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("Invalid approval")
    payload = json.loads(base64.urlsafe_b64decode(body))
    if payload["expires"] < time.time():
        raise ValueError("Expired approval")
    Draft.model_validate(payload["draft"])
    return payload

def safe_sql(sql):
    trees = sqlglot.parse(sql, read="postgres")
    if len(trees) != 1 or not isinstance(trees[0], exp.Select):
        raise ValueError("Only one SELECT is allowed")
    if PII.search(sql):
        raise ValueError("PII literals are outside the query tool's scope")
    tree = trees[0]
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1 or tables[0].name not in {"ai_expenses", "ai_daily", "ai_categories"} or tables[0].db or tables[0].catalog:
        raise ValueError("Query one approved analytics view")
    if tree.args.get("joins") or any(tree.find(c) for c in (exp.With, exp.Subquery, exp.Into, exp.Lock)):
        raise ValueError("Joins, subqueries, CTEs, locks and SELECT INTO are outside this MVP")
    allowed = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max, exp.Round, exp.Coalesce, exp.CurrentDate, exp.DateTrunc, exp.And, exp.Or)
    for fn in tree.find_all(exp.Func):
        if not isinstance(fn, allowed):
            raise ValueError("SQL function is not allowed")
    if any(c.name.lower() in {"user_email", "description"} for c in tree.find_all(exp.Column)):
        raise ValueError("PII columns are not allowed")
    if tree.args.get("offset"):
        raise ValueError("Offsets are outside this MVP")
    limit = tree.args.get("limit")
    cap = min(100, int(limit.expression.this)) if limit and isinstance(limit.expression, exp.Literal) else 100
    return tree.limit(max(1, cap)).sql(dialect="postgres")

def grounded(text, context):
    # Reject unsupported literal numeric claims; this is not semantic proof.
    numbers = lambda s: {Decimal(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}
    return numbers(text) <= numbers(context)
