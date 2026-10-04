"""Task 6.6: the SQL examples in the README's `persons` section run against a migrated database."""

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

README = Path(__file__).resolve().parent.parent / "README.md"
SECTION_HEADING = "## Your tags and marks: the `persons` table"
RESTRICT_VIOLATION = "23001"


def persons_section() -> str:
    readme = README.read_text()
    start = readme.index(SECTION_HEADING)
    following = re.search(r"^## ", readme[start + len(SECTION_HEADING):], flags=re.MULTILINE)
    return readme[start : start + len(SECTION_HEADING) + following.start()] if following else readme[start:]


def documented_statements() -> list[str]:
    blocks = re.findall(r"```sql\n(.*?)```", persons_section(), flags=re.DOTALL)
    statements = [chunk.strip() for block in blocks for chunk in block.split(";")]
    return [s for s in statements if s]


def expects_failure(statement: str) -> bool:
    return any(line.startswith("-- fails") for line in statement.splitlines())


def test_section_documents_both_user_columns_and_the_examples():
    section = persons_section()
    assert "`tags`" in section and "`is_marked`" in section and "'lab' = ANY(tags)" in section
    statements = documented_statements()
    assert len(statements) >= 8
    assert sum(expects_failure(s) for s in statements) == 1


@pytest.mark.db
def test_documented_sql_runs_against_a_migrated_database(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO persons (id, username) VALUES (1, 'alice'), (2, 'bob')"))
        conn.execute(text("INSERT INTO targets (id, username) VALUES (100, 'target')"))
        scan_id = conn.execute(
            text("INSERT INTO scans (target_id, backend, mode, is_baseline) VALUES (100, 'instaloader', 'default', true) RETURNING id")
        ).scalar_one()
        conn.execute(
            text("INSERT INTO follows (target_id, person_id, first_seen_scan_id, last_seen_scan_id) VALUES (100, 1, :s, :s), (100, 2, :s, :s)"),
            {"s": scan_id},
        )

    results = {}
    for statement in documented_statements():
        if expects_failure(statement):
            with pytest.raises(DBAPIError) as failure:
                with db_engine.begin() as conn:
                    conn.execute(text(statement))
            assert failure.value.orig.sqlstate == RESTRICT_VIOLATION
            with db_engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM persons WHERE username = 'alice'")).scalar_one() == 1
            continue
        with db_engine.begin() as conn:
            result = conn.execute(text(statement))
            if result.returns_rows:
                results[statement] = result.all()

    by_prefix = {next(line for line in s.splitlines() if not line.startswith("--"))[:30]: rows for s, rows in results.items()}
    lab = next(rows for key, rows in by_prefix.items() if key.startswith("SELECT id, username, tags"))
    assert sorted(row.username for row in lab) == ["alice", "bob"]
    marked = next(rows for key, rows in by_prefix.items() if key.startswith("SELECT t.username AS target"))
    assert [(row.target, row.username, row.tags) for row in marked] == [("target", "alice", ["lab"])]

    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT username FROM persons")).scalars().all() == ["bob"]
        assert conn.execute(text("SELECT person_id FROM follows")).scalars().all() == [2]
