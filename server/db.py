"""The node's own PostgreSQL database.

Saved cards and, later, subscribed channels live here, never in NNTP. For now
everything is in the schema `public`. Later a middleware picks a schema per
library, so `migrate()` already takes the schema as an argument.

The routes use an async pool, so a query never blocks the event loop. The
migrations run once at start-up, before the node takes requests, on a plain
connection.

The password comes from the Docker secret `ome_db_password`, never
from the URL, so it is not in `docker inspect` and may hold any character.
"""

import os
import re
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

MIGRATIONS = Path(__file__).parent / "migrations"
MIGRATION_NAME = re.compile(r"\d{4}_[a-z0-9_]+\.sql")


def database_url() -> str | None:
    """None when the node runs without a database, like most tests."""
    return os.getenv("OME_DATABASE_URL") or None


def read_password(path: str | None) -> str | None:
    """The password in a Docker secret file, without the final newline."""
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8").rstrip("\n")


def connect_kwargs() -> dict[str, str]:
    """The password from the file in `OME_DB_PASSWORD_FILE`, apart from the URL.

    Empty without that file, like in the tests: then the URL holds the password.
    """
    password = read_password(os.getenv("OME_DB_PASSWORD_FILE"))
    return {"password": password} if password else {}


def migrate(
    conn: psycopg.Connection, schema: str = "public", folder: Path = MIGRATIONS
) -> list[str]:
    """Apply the SQL files this schema does not have yet, in name order.

    Returns the versions applied now. It all runs in one transaction, so a
    failing file leaves the schema as it was.
    """
    applied = []
    name = sql.Identifier(schema)
    with conn.transaction():
        # Several workers may start at once: one migrates, the others wait
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [schema])
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(name))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(name))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        rows = conn.execute("SELECT version FROM schema_migrations")
        done = {version for (version,) in rows}
        for path in sorted(folder.glob("*.sql")):
            if path.stem in done:
                continue
            if not MIGRATION_NAME.fullmatch(path.name):
                msg = f"{path.name}: name it like 0001_saved_cards.sql"
                raise ValueError(msg)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", [path.stem]
            )
            applied.append(path.stem)
    return applied


def prepare(url: str) -> list[str]:
    """Bring the schema `public` up to date, before the node takes requests."""
    with psycopg.connect(url, **connect_kwargs()) as conn:
        return migrate(conn)


async def open_pool(url: str) -> AsyncConnectionPool:
    """The pool the routes use: `async with app.state.db.connection() as conn`."""
    pool = AsyncConnectionPool(
        url, kwargs=connect_kwargs(), min_size=1, max_size=5, open=False
    )
    await pool.open()
    return pool
