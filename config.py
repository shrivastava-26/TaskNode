import os
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit
from dotenv import load_dotenv

load_dotenv()


def app_db_url():
    return os.environ["APP_DB_URL"]


def warehouse_url():
    # Redshift speaks the Postgres wire protocol for these simple SQL statements.
    return os.environ["WAREHOUSE_URL"].replace("redshift://", "postgresql://", 1)


def jdbc(url, kind="postgresql"):
    if kind not in ("postgresql", "redshift"):
        raise ValueError("WAREHOUSE_KIND must be postgresql or redshift")
    parts = urlsplit(url)
    if not parts.hostname or not parts.path.strip("/"):
        raise ValueError("Database URL must include host and database name")
    options = dict(parse_qsl(parts.query))
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    address = f"jdbc:{kind}://{host}:{parts.port or (5439 if kind == 'redshift' else 5432)}{parts.path}"
    if options:
        address += "?" + urlencode(options)
    return address, {
        "user": unquote(parts.username or ""), "password": unquote(parts.password or ""),
        "driver": "com.amazon.redshift.jdbc.Driver" if kind == "redshift" else "org.postgresql.Driver",
    }


def validate_separation():
    app, warehouse = urlsplit(app_db_url()), urlsplit(warehouse_url())
    # Separate databases on the same server do NOT satisfy this MVP's requirement.
    if (app.hostname, app.port or 5432) == (warehouse.hostname, warehouse.port or 5432):
        raise ValueError("APP_DB_URL and WAREHOUSE_URL must point to separate server instances")
