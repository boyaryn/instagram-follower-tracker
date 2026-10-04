"""Table, CSV and JSON renderers (design D13). Each takes the report rows and returns the text to print."""

from __future__ import annotations

import csv
import enum
import io
import json
import shutil
from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from igft.reporting.queries import FollowerRow
from igft.timeutil import format_local

CSV_TAG_SEPARATOR = ";"
EMPTY_MESSAGE = "No followers to show."
FIELDS = ("id", "username", "full_name", "first_seen_at", "tags", "is_marked")


class OutputFormat(enum.StrEnum):
    TABLE = "table"
    CSV = "csv"
    JSON = "json"


def render_table(rows: Sequence[FollowerRow]) -> str:
    if not rows:
        return EMPTY_MESSAGE + "\n"
    table = Table()
    table.add_column("ID", justify="right", no_wrap=True)
    table.add_column("Username")
    table.add_column("Full name")
    table.add_column("First seen", no_wrap=True)
    table.add_column("Tags")
    table.add_column("Marked", no_wrap=True)
    for row in rows:
        # Text, not str: names come from Instagram and must not be read as Rich markup.
        table.add_row(
            Text(str(row.id)),
            Text(row.username),
            Text(row.full_name or ""),
            Text(format_local(row.first_seen_at)),
            Text(", ".join(row.tags)),
            Text("yes" if row.is_marked else "no"),
        )
    buffer = io.StringIO()
    Console(file=buffer, width=shutil.get_terminal_size().columns, highlight=False).print(table)
    return buffer.getvalue()


def render_csv(rows: Sequence[FollowerRow]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.username,
                row.full_name or "",
                format_local(row.first_seen_at),
                CSV_TAG_SEPARATOR.join(row.tags),
                "true" if row.is_marked else "false",
            ]
        )
    return buffer.getvalue()


def render_json(rows: Sequence[FollowerRow]) -> str:
    objects = [
        {
            "id": row.id,
            "username": row.username,
            "full_name": row.full_name,
            "first_seen_at": row.first_seen_at.astimezone().isoformat(timespec="seconds"),
            "tags": list(row.tags),
            "is_marked": row.is_marked,
        }
        for row in rows
    ]
    return json.dumps(objects, indent=2, ensure_ascii=False) + "\n"


RENDERERS = {
    OutputFormat.TABLE: render_table,
    OutputFormat.CSV: render_csv,
    OutputFormat.JSON: render_json,
}


def render(rows: Sequence[FollowerRow], fmt: OutputFormat) -> str:
    return RENDERERS[fmt](rows)
