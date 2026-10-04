import re
from pathlib import Path

import typer.main

from igft.cli import app

README = (Path(__file__).parent.parent / "README.md").read_text()


def section(title: str) -> str:
    start = README.index(f"## {title}")
    end = README.find("\n## ", start + 1)
    return README[start:end]


def options(command: str) -> set[str]:
    group = typer.main.get_command(app)
    params = group.commands[command].params
    return {opt for p in params for opt in getattr(p, "opts", []) if opt.startswith("--")} - {"--help"}


def test_every_scan_and_resume_option_is_documented_in_the_scanning_section():
    scanning = section("Scanning")

    for command in ("scan", "resume"):
        assert options(command), command
        for option in options(command):
            assert option in scanning


def test_the_scanning_section_documents_no_option_the_commands_lack():
    documented = set(re.findall(r"--[a-z][a-z-]*", section("Scanning")))

    assert documented <= options("scan") | options("resume")


def test_the_reports_section_documents_every_option_of_list_and_first_seen():
    reports = section("Reports: `list` and `first-seen`")

    for command in ("list", "first-seen"):
        assert options(command), command
        for option in options(command):
            assert option in reports
    assert set(re.findall(r"--[a-z][a-z-]*", reports)) <= options("list") | options("first-seen")


def test_the_reports_examples_match_what_the_renderers_print_for_the_same_rows(monkeypatch):
    import time
    from datetime import UTC, datetime

    from igft.reporting.queries import FollowerRow
    from igft.reporting.render import render_csv, render_json, render_table

    monkeypatch.setenv("TZ", "Europe/Berlin")
    monkeypatch.setenv("COLUMNS", "100")
    time.tzset()
    try:
        moment = datetime(2026, 9, 15, 10, 30, tzinfo=UTC)
        rows = [
            FollowerRow(1001, "alice", "Alice A.", moment, ("lab", "pilot"), True),
            FollowerRow(2002, "bob", None, moment, (), False),
        ]
        reports = section("Reports: `list` and `first-seen`")

        for render in (render_table, render_csv, render_json):
            assert render(rows) in reports.replace("```\n", "").replace("\n```", "\n")
    finally:
        monkeypatch.undo()
        time.tzset()
