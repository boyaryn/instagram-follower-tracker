"""Throwaway database schemas for `db` tests: the real migrations, run in a private PostgreSQL schema."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from igft.db.engine import normalise_url
from igft.db.migrate import upgrade

TEST_DATABASE_URL_ENV = "IGFT_TEST_DATABASE_URL"
SCHEMA_PREFIX = "igft_test_"


@dataclass(frozen=True)
class Schema:
    engine: Engine
    name: str
    url: str  # a database URL whose connections all use this schema


def database_url() -> str:
    return normalise_url(os.environ[TEST_DATABASE_URL_ENV])


def schema_exists(name: str) -> bool:
    admin = create_engine(database_url())
    try:
        with admin.connect() as conn:
            return conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :n)"), {"n": name}
            ).scalar_one()
    finally:
        admin.dispose()


@contextmanager
def empty_schema() -> Iterator[Schema]:
    """Yield a fresh, empty schema and drop it afterwards."""
    name = SCHEMA_PREFIX + uuid.uuid4().hex[:12]
    base_url = database_url()
    admin = create_engine(base_url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{name}"'))
    url = make_url(base_url).update_query_dict({"options": f"-csearch_path={name}"}).render_as_string(
        hide_password=False
    )
    engine = create_engine(url)
    try:
        yield Schema(engine, name, url)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        admin.dispose()


@contextmanager
def migrated_schema() -> Iterator[Schema]:
    """Yield a fresh schema that the real migrations have built."""
    with empty_schema() as schema:
        upgrade(schema.engine)
        yield schema
