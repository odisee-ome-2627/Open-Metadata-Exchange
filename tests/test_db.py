"""Tests for `server/db.py`. They need a PostgreSQL, see `OME_DATABASE_URL`."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from server import db

URL = os.getenv("OME_DATABASE_URL")
needs_database = pytest.mark.skipif(not URL, reason="set OME_DATABASE_URL")


@contextmanager
def fresh_schema() -> Iterator[tuple[psycopg.Connection, str]]:
    """A schema of its own, so tests on parallel threads never meet."""
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(URL, autocommit=True) as conn:
        try:
            yield conn, schema
        finally:
            drop = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE")
            conn.execute(drop.format(sql.Identifier(schema)))


def migrations(tmp_path: Path, files: dict[str, str]) -> Path:
    folder = tmp_path / uuid.uuid4().hex
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


@needs_database
def test_migrations_run_once_and_in_order(tmp_path: Path) -> None:
    folder = migrations(
        tmp_path,
        {
            "0002_title.sql": "ALTER TABLE cards ADD COLUMN title text;",
            "0001_cards.sql": "CREATE TABLE cards (id int PRIMARY KEY); SELECT 1;",
        },
    )
    with fresh_schema() as (conn, schema):
        assert db.migrate(conn, schema, folder) == ["0001_cards", "0002_title"]
        assert db.migrate(conn, schema, folder) == []
        cards = sql.Identifier(schema, "cards")
        conn.execute(sql.SQL("INSERT INTO {} VALUES (1, 'a')").format(cards))
        title = conn.execute(sql.SQL("SELECT title FROM {}").format(cards)).fetchone()
        assert title == ("a",)


@needs_database
def test_a_failing_migration_leaves_the_schema_as_it_was(tmp_path: Path) -> None:
    folder = migrations(
        tmp_path,
        {
            "0001_ok.sql": "CREATE TABLE ok (id int);",
            "0002_bad.sql": "CREATE TABLE bad (;",
        },
    )
    with fresh_schema() as (conn, schema):
        with pytest.raises(psycopg.errors.SyntaxError):
            db.migrate(conn, schema, folder)
        exists = conn.execute("SELECT to_regclass(%s)", [f"{schema}.ok"]).fetchone()
        assert exists == (None,)


@needs_database
def test_a_wrong_file_name_stops_the_migration(tmp_path: Path) -> None:
    folder = migrations(tmp_path, {"saved cards.sql": "SELECT 1;"})
    with fresh_schema() as (conn, schema), pytest.raises(ValueError, match="0001_"):
        db.migrate(conn, schema, folder)


@needs_database
def test_prepare_brings_the_public_schema_up_to_date() -> None:
    db.prepare(URL)
    with psycopg.connect(URL) as conn:
        table = conn.execute("SELECT to_regclass('public.schema_migrations')")
        assert table.fetchone() == ("schema_migrations",)


def test_read_password_drops_only_the_final_newline(tmp_path: Path) -> None:
    secret = tmp_path / uuid.uuid4().hex
    secret.write_text(" s3cr@t/pw:# \n", encoding="utf-8")
    assert db.read_password(str(secret)) == " s3cr@t/pw:# "
    assert db.read_password(None) is None


@needs_database
def test_the_password_can_come_apart_from_the_url(tmp_path: Path) -> None:
    parts = conninfo_to_dict(URL)
    password = parts.pop("password")
    secret = tmp_path / uuid.uuid4().hex
    secret.write_text(f"{password}\n", encoding="utf-8")
    url_without_password = make_conninfo(**parts)
    with psycopg.connect(
        url_without_password, password=db.read_password(str(secret))
    ) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
