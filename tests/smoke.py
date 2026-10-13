"""Real Compose acceptance check; uses only the local demo databases."""
import subprocess
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def compose(*args):
    return subprocess.check_output(["docker", "compose", *args], text=True).strip()


def sql(service, user, database, query):
    return compose("exec", "-T", service, "psql", "-U", user, "-d", database, "-Atc", query)


def snapshot():
    return sql("warehouse-db", "analytics_user", "analytics", "SELECT COUNT(*) || '|' || COALESCE(SUM(amount_usd),0) FROM fact_expenses")


def pipeline():
    compose("run", "--rm", "spark")


def save(amount, expense_id=None):
    data = dict(user_email="smoke@example.com", category="Food", amount=amount, currency="USD", description="Acceptance check")
    if expense_id:
        data["expense_id"] = expense_id
    with urlopen("http://localhost:8000/app/save", data=urlencode(data).encode(), timeout=15) as response:
        assert response.status == 200


baseline = snapshot()
count, total = baseline.split("|")
assert int(count) == 200, "Use a fresh local database for this check"
assert list(Path("datalake/fact_expenses").rglob("*.parquet"))
pipeline()
assert snapshot() == baseline, "ETL replay duplicated data"
save("10.00")
expense_id = sql("app-db", "app_user", "expenses", "SELECT MAX(id) FROM expenses WHERE user_email='smoke@example.com'")
assert snapshot() == baseline, "Dashboard warehouse changed before ETL"
pipeline()
assert snapshot() == f"201|{Decimal(total) + Decimal('10.00'):.2f}"
save("25.00", expense_id)
pipeline()
assert snapshot() == f"201|{Decimal(total) + Decimal('25.00'):.2f}"
with urlopen(f"http://localhost:8000/app/{expense_id}/delete", data=b"", timeout=15):
    pass
pipeline()
assert snapshot() == baseline, "Soft delete did not remove analytics row"
assert sql("warehouse-db", "analytics_user", "analytics", "SELECT SUM(txn_count) FROM agg_daily_category_spend") == count
compose("stop", "app-db")
try:
    # Query the same function the dashboard uses, in its running container.
    result = compose("exec", "-T", "dashboard", "python", "-c",
                     "from dashboard.app import load_data; print(load_data()[0]['txn_count'])")
    assert result == count
    with urlopen("http://localhost:8501/_stcore/health", timeout=15) as response:
        assert response.status == 200
finally:
    compose("start", "app-db")
print("PASS: CRUD, JDBC ETL, Parquet, idempotence, edits, deletes, aggregates, app-DB-independent dashboard")
