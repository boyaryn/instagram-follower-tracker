import csv
import io
import json
import time
from datetime import UTC, datetime

import pytest

from igft.reporting.queries import FollowerRow
from igft.reporting.render import EMPTY_MESSAGE, OutputFormat, render, render_csv, render_json, render_table

MOMENT = datetime(2026, 9, 15, 10, 30, tzinfo=UTC)  # 12:30 in Berlin in September


@pytest.fixture(autouse=True)
def berlin_time_and_wide_terminal(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    monkeypatch.setenv("COLUMNS", "200")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


TAGGED = FollowerRow(
    id=1001, username="alice", full_name="Alice A.", first_seen_at=MOMENT, tags=("lab", "pilot"), is_marked=True
)
PLAIN = FollowerRow(id=2002, username="bob", full_name=None, first_seen_at=MOMENT, tags=(), is_marked=False)


def test_the_table_shows_every_field_of_a_tagged_marked_row_in_local_time():
    text = render_table([TAGGED])

    line = next(line for line in text.splitlines() if "alice" in line)
    for expected in ("1001", "Alice A.", "2026-09-15 12:30 CEST", "lab, pilot", "yes"):
        assert expected in line


def test_the_table_shows_an_unmarked_untagged_row():
    text = render_table([PLAIN])

    line = next(line for line in text.splitlines() if "bob" in line)
    assert "2002" in line and "no" in line


def test_the_table_prints_names_literally_instead_of_reading_them_as_markup():
    row = FollowerRow(3, "carol", "[bold]Carol[/bold] :smile:", MOMENT, (), False)

    assert "[bold]Carol[/bold] :smile:" in render_table([row])


def test_an_empty_table_is_a_message():
    assert render_table([]) == EMPTY_MESSAGE + "\n"


def test_csv_has_a_header_and_one_row_per_follower():
    rows = list(csv.reader(io.StringIO(render_csv([TAGGED, PLAIN]))))

    assert rows[0] == ["id", "username", "full_name", "first_seen_at", "tags", "is_marked"]
    assert rows[1] == ["1001", "alice", "Alice A.", "2026-09-15 12:30 CEST", "lab;pilot", "true"]
    assert rows[2] == ["2002", "bob", "", "2026-09-15 12:30 CEST", "", "false"]
    assert len(rows) == 3


def test_csv_quotes_commas_quotes_and_line_breaks_in_names():
    row = FollowerRow(5, "dave", 'Dave, "the" Great\nSecond line', MOMENT, (), False)

    parsed = list(csv.reader(io.StringIO(render_csv([row]))))

    assert parsed[1][2] == 'Dave, "the" Great\nSecond line'


def test_an_empty_csv_is_only_the_header():
    assert render_csv([]) == "id,username,full_name,first_seen_at,tags,is_marked\n"


def test_json_is_an_array_with_tags_as_a_list_and_the_mark_as_a_boolean():
    data = json.loads(render_json([TAGGED, PLAIN]))

    assert data[0] == {
        "id": 1001,
        "username": "alice",
        "full_name": "Alice A.",
        "first_seen_at": "2026-09-15T12:30:00+02:00",
        "tags": ["lab", "pilot"],
        "is_marked": True,
    }
    assert data[1]["full_name"] is None
    assert data[1]["tags"] == []
    assert data[1]["is_marked"] is False
    assert len(data) == 2


def test_json_keeps_non_ascii_names_readable():
    row = FollowerRow(6, "erin", "Ерін Ö", MOMENT, (), False)

    assert "Ерін Ö" in render_json([row])


def test_an_empty_json_result_is_an_empty_array():
    assert render_json([]).strip() == "[]"


def test_render_picks_the_renderer_for_each_format():
    assert render([TAGGED], OutputFormat.CSV) == render_csv([TAGGED])
    assert render([TAGGED], OutputFormat.JSON) == render_json([TAGGED])
    assert render([TAGGED], OutputFormat.TABLE) == render_table([TAGGED])


def test_the_valid_formats_are_table_csv_and_json():
    assert [f.value for f in OutputFormat] == ["table", "csv", "json"]
