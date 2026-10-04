"""Reports and `target list` must work without Instagram: no fetcher code loaded, no cooldown in the way."""

import json
import os
import subprocess
import sys
import textwrap
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from dbutil import migrated_schema
from igft.cli import app
from igft.db.targets import add_target
from test_gate import hours, put_cooldown

pytestmark = pytest.mark.db

SCRIPT = textwrap.dedent(
    """
    import json, sys
    from igft.cli import app

    for args in (["list", "alice"], ["first-seen", "alice"], ["target", "list"]):
        try:
            app(args, standalone_mode=False)
        except SystemExit:
            pass
    loaded = sorted(
        name for name in sys.modules
        if name in ("instaloader", "igft.fetchers") or name.startswith(("instaloader.", "igft.fetchers."))
    )
    print(json.dumps(loaded))
    """
)


def test_list_first_seen_and_target_list_never_load_the_fetcher_code(cli_env):
    with migrated_schema() as schema:
        with schema.engine.begin() as conn:
            add_target(conn, 100, "alice")
        env = {**os.environ, **cli_env, "IGFT_DATABASE_URL": schema.url}

        result = subprocess.run(
            [sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=60
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == []


def test_list_still_works_while_a_cooldown_is_active(cli_env):
    with migrated_schema() as schema:
        with schema.engine.begin() as conn:
            add_target(conn, 100, "alice")
        put_cooldown(schema.engine, started_ago=timedelta(minutes=5), ends_in=hours(3))
        env = {**cli_env, "IGFT_DATABASE_URL": schema.url}

        result = CliRunner().invoke(app, ["list", "alice"], env=env)

    assert result.exit_code == 0, result.output
