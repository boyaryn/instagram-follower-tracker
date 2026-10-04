import os

import pytest

TEST_DATABASE_URL_ENV = "IGFT_TEST_DATABASE_URL"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Environment for CLI tests: an empty config file, so nothing is read from the developer's own config."""
    for name in list(os.environ):
        if name.startswith("IGFT_") and name != TEST_DATABASE_URL_ENV:
            monkeypatch.delenv(name)
    config = tmp_path / "config.toml"
    config.write_text("")
    return {"IGFT_CONFIG": str(config)}


@pytest.fixture
def use_fetcher(monkeypatch):
    """Make every CLI command use `fetcher` instead of building the real instaloader one."""

    def install(fetcher):
        monkeypatch.setattr("igft.cli.fetcher.create_fetcher", lambda settings: fetcher)
        return fetcher

    return install


@pytest.fixture
def db_engine():
    """A SQLAlchemy engine on an empty, fully migrated throwaway schema, dropped afterwards."""
    from dbutil import migrated_schema

    with migrated_schema() as schema:
        yield schema.engine


def pytest_collection_modifyitems(config, items):
    if os.environ.get(TEST_DATABASE_URL_ENV):
        return
    skip_db = pytest.mark.skip(
        reason=f"database test: set {TEST_DATABASE_URL_ENV} to a throwaway PostgreSQL database to run it"
    )
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)
