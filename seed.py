"""Insert 200 deterministic sample rows once; reruns leave user edits intact."""
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.main import CATEGORIES, CURRENCIES, connect, initialize


def seed():
    initialize()
    rng = random.Random(42)
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    with connect() as db:
        # The marker shares this transaction: a failed seed can safely be retried.
        db.execute("CREATE TABLE IF NOT EXISTS seed_marker (name TEXT PRIMARY KEY)")
        inserted = db.execute("INSERT INTO seed_marker VALUES ('demo-v1') ON CONFLICT DO NOTHING RETURNING name").fetchone()
        if inserted is None:
            print("Seed already applied; existing rows and edits preserved")
            return
        for expense_id in range(1, 201):
            currency = CURRENCIES[(expense_id - 1) % 3]
            dollars = Decimal(rng.randint(100, 12000)) / 100
            divisor = {"USD": Decimal("1"), "INR": Decimal("0.012"), "EUR": Decimal("1.08")}[currency]
            amount = (dollars / divisor).quantize(Decimal("0.01"))
            db.execute("""INSERT INTO expenses
                (user_email, category, amount, currency, description, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                ("demo@example.com", CATEGORIES[(expense_id - 1) % 5], amount,
                 currency, f"Sample expense {expense_id}", today - timedelta(days=(expense_id - 1) % 30)))
        count = db.execute("SELECT COUNT(*) AS n FROM expenses WHERE NOT is_deleted").fetchone()["n"]
    print(f"Seed complete: {count} active application expenses (existing rows preserved)")


if __name__ == "__main__":
    seed()
