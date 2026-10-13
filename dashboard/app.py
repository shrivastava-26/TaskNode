"""Warehouse-only dashboard. No app API requests and no application DB access."""
import sys
from pathlib import Path

import pandas as pd
import psycopg
from psycopg.rows import dict_row
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import warehouse_url


def load_data():
    # No cache: every refresh reads the latest committed warehouse snapshot.
    with psycopg.connect(warehouse_url(), row_factory=dict_row, connect_timeout=5) as db:
        # Keep all dashboard queries on one snapshot during concurrent ETL commits.
        db.isolation_level = psycopg.IsolationLevel.SERIALIZABLE
        kpis = db.execute("""SELECT COALESCE(SUM(amount_usd), 0) AS total_spend,
            COUNT(*) AS txn_count, COALESCE(AVG(amount_usd), 0) AS avg_txn,
            MAX(loaded_at) AS latest_load FROM fact_expenses""").fetchone()
        categories = db.execute("""SELECT category_name, SUM(total_amount) AS total_amount
            FROM agg_daily_category_spend GROUP BY category_name
            ORDER BY total_amount DESC""").fetchall()
        daily = db.execute("""SELECT expense_date, SUM(total_amount) AS total_amount
            FROM agg_daily_category_spend GROUP BY expense_date ORDER BY expense_date""").fetchall()
        recent = db.execute("""SELECT f.expense_id, f.expense_date, f.user_email,
            d.category_name, f.amount_usd FROM fact_expenses f
            JOIN dim_category d ON f.category_key=d.category_key
            ORDER BY f.expense_date DESC, f.expense_id DESC LIMIT 10""").fetchall()
    return kpis, categories, daily, recent


def main():
    st.set_page_config(page_title="Expense analytics", page_icon="📊", layout="wide")
    st.title("Expense analytics")
    st.caption("Warehouse snapshot · amounts in USD · dates in UTC")
    st.button("Refresh warehouse data")  # Clicking reruns this script and its queries.
    try:
        kpis, categories, daily, recent = load_data()
    except KeyError:
        st.error("Warehouse configuration is missing. Set WAREHOUSE_URL and restart the dashboard.")
        st.stop()
    except psycopg.errors.UndefinedTable:
        st.info("Analytics tables are not ready. Run the Spark ETL, then refresh this page.")
        st.stop()
    except (psycopg.Error, ValueError):
        st.error("Warehouse unavailable. Check its connection settings and availability, then refresh.")
        st.stop()

    latest = kpis["latest_load"]
    columns = st.columns(4)
    columns[0].metric("Total spend", f"${kpis['total_spend']:,.2f}")
    columns[1].metric("Transactions", f"{kpis['txn_count']:,}")
    columns[2].metric("Average transaction", f"${kpis['avg_txn']:,.2f}")
    columns[3].metric("Latest load (UTC)", latest.strftime("%Y-%m-%d %H:%M:%S") if latest else "—")
    if latest:
        st.info(f"Data freshness: max(loaded_at) = {latest.isoformat(sep=' ')} UTC. Run ETL to include new app changes.")
    else:
        st.info("No active expenses in the warehouse snapshot. Freshness is unavailable because there are no fact rows.")

    left, right = st.columns(2)
    with left:
        st.subheader("Spend by category")
        if categories:
            frame = pd.DataFrame(categories).set_index("category_name")
            frame["total_amount"] = frame["total_amount"].astype(float)
            st.bar_chart(frame, y="total_amount")
        else:
            st.caption("No category spending yet.")
    with right:
        st.subheader("Spend over time")
        if daily:
            frame = pd.DataFrame(daily).set_index("expense_date")
            frame["total_amount"] = frame["total_amount"].astype(float)
            st.line_chart(frame, y="total_amount")
        else:
            st.caption("No daily spending yet.")
    st.subheader("10 most recent expenses")
    if recent:
        frame = pd.DataFrame(recent)
        frame["amount_usd"] = frame["amount_usd"].astype(float)
        st.dataframe(frame, hide_index=True, use_container_width=True,
                     column_config={"amount_usd": st.column_config.NumberColumn("Amount (USD)", format="$%.2f")})
    else:
        st.caption("No recent expenses.")


if __name__ == "__main__":
    main()
