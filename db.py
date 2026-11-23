"""Shared database connections and fragment routing for M1 (Postgres) + M2 (Cassandra).

Import this module before other project imports so gevent is patched on Python 3.12+.
CRUD modules should use: `import db` then `db.pg_conn`, `db.cassandra_sessions`, etc.
(Do not `from db import pg_conn` — that freezes the pre-connect None value.)

Secrets are loaded from a local .env file (see .env.example). Never commit .env.
"""
from gevent import monkey
monkey.patch_all()

import atexit
import json
import os
import warnings
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# gevent + libuv on Windows: Cassandra often schedules <1ms timeouts
warnings.filterwarnings(
    "ignore",
    message="libuv only supports millisecond timer resolution.*",
    category=UserWarning,
    module=r"gevent\.timeout",
)

from dotenv import load_dotenv
import psycopg2
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider
from cassandra.io.geventreactor import GeventConnection

# Load .env from the project root (same folder as this file)
load_dotenv(Path(__file__).resolve().parent / ".env")


def _require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {name}. "
            f"Copy .env.example to .env and fill in your secrets."
        )
    return value


# Shared handles (filled by connect_all)
pg_conn = None
cassandra_cluster = None
cassandra_sessions = {}  # keyspace -> Session

# Horizontal routing: region -> Postgres schema + Cassandra keyspace
PG_FRAGMENTS = (
    {"schema": "db11", "region": "Vilnius", "cassandra": "db21"},
    {"schema": "db12", "region": "Kaunas", "cassandra": "db22"},
)

REGION_TO_FRAGMENT = {f["region"]: f for f in PG_FRAGMENTS}
SCHEMA_TO_FRAGMENT = {f["schema"]: f for f in PG_FRAGMENTS}


def connect_postgres():
    """One Supabase connection covers all schemas (use schema.table in SQL)."""
    global pg_conn
    print("Connecting to PostgreSQL (Supabase)...")
    pg_conn = psycopg2.connect(
        host=_require_env("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=_require_env("POSTGRES_DB"),
        user=_require_env("POSTGRES_USER"),
        password=_require_env("POSTGRES_PASSWORD"),
    )
    pg_conn.autocommit = False
    with pg_conn.cursor() as cur:
        cur.execute("SELECT version();")
        version = cur.fetchone()[0]
    print(f" -> PostgreSQL connected. {version}")
    return pg_conn


def connect_cassandra(keyspaces=("db21", "db22")):
    """One Astra cluster, separate sessions per keyspace."""
    global cassandra_cluster, cassandra_sessions
    print("Connecting to Cassandra (Astra DB)...")
    bundle = _require_env("ASTRA_SECURE_BUNDLE")
    bundle_path = Path(__file__).resolve().parent / bundle
    if not bundle_path.is_file():
        raise FileNotFoundError(
            f"Astra secure connect bundle not found: {bundle_path}. "
            f"Download it from Astra and place it in the project folder "
            f"(do not commit the zip to git)."
        )

    cloud_config = {"secure_connect_bundle": str(bundle_path)}
    auth_provider = PlainTextAuthProvider("token", _require_env("ASTRA_TOKEN"))
    cassandra_cluster = Cluster(
        cloud=cloud_config,
        auth_provider=auth_provider,
        connection_class=GeventConnection,
    )

    for keyspace in keyspaces:
        cassandra_sessions[keyspace] = cassandra_cluster.connect(keyspace)
        print(f" -> Astra session ready for keyspace '{keyspace}'")

    row = cassandra_sessions[keyspaces[0]].execute(
        "SELECT release_version FROM system.local"
    ).one()
    print(f" -> Cassandra connected. Version: {row.release_version}")
    return cassandra_cluster, cassandra_sessions


def close_connections():
    global pg_conn, cassandra_cluster, cassandra_sessions
    if pg_conn is not None and not pg_conn.closed:
        pg_conn.close()
        print("PostgreSQL connection closed.")
    pg_conn = None

    if cassandra_cluster is not None:
        cassandra_cluster.shutdown()
        print("Cassandra cluster shut down.")
    cassandra_cluster = None
    cassandra_sessions = {}


def connect_all():
    """Open both databases once at startup."""
    connect_postgres()
    connect_cassandra()
    atexit.register(close_connections)
    print("All database connections ready.")


def resolve_fragment(region):
    """Map region name to fragment info, or None if unknown."""
    return REGION_TO_FRAGMENT.get(region)


def json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if hasattr(value, "__dict__") and not isinstance(value, type):
        data = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        if data:
            return data
    return str(value)


def print_json(data):
    print(json.dumps(data, indent=2, default=json_default, ensure_ascii=False))
