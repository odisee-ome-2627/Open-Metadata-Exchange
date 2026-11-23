"""FastAPI opens the node database at start. Needs INN, like every app test."""

import os

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import AsyncConnectionPool

from server.main import app

needs_database = pytest.mark.skipif(
    not os.getenv("OME_DATABASE_URL"), reason="set OME_DATABASE_URL"
)


async def select_one(pool: AsyncConnectionPool) -> tuple[int] | None:
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT 1")
        return await cursor.fetchone()


@pytest.mark.thread_unsafe(reason="app.state belongs to the one app")
def test_the_app_starts_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OME_DATABASE_URL", raising=False)
    with TestClient(app):
        assert app.state.db is None


@needs_database
@pytest.mark.thread_unsafe(reason="one app with one pool for every thread")
def test_the_routes_get_an_async_pool_that_answers() -> None:
    with TestClient(app) as client:
        pool = app.state.db
        assert isinstance(pool, AsyncConnectionPool)
        assert client.portal.call(select_one, pool) == (1,)
    assert pool.closed
