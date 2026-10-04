"""Source scan for design D8: the tool never writes `persons.tags` / `persons.is_marked` and never deletes a person.

The scan reads the package source, not the database. It flags:
- a SQLAlchemy insert/update/delete statement, or a `.values(...)` / `.on_conflict_do_update(...)` call,
  that names `tags` or `is_marked`;
- `delete(persons)` or `persons.delete()`;
- raw SQL strings that insert/update with those columns, delete from `persons` or truncate it.

Reading the columns (`select(persons.c.tags)`) is fine: reports show them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "igft"

PROTECTED = {"tags", "is_marked"}
TABLE_NAMES = {"persons", "targets", "follows", "scans", "cooldowns", "session_checks"}
MODULE_ALIASES = {"sa", "sqlalchemy", "postgresql", "pg"}
WRITE_FUNCS = {"insert", "update", "delete"}
WRITE_METHODS = {"values", "on_conflict_do_update"}

RAW_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b", re.IGNORECASE)
RAW_PROTECTED = re.compile(r"\b(tags|is_marked)\b", re.IGNORECASE)
RAW_REMOVES_PERSONS = re.compile(
    r"\b(DELETE\s+FROM|TRUNCATE(\s+TABLE)?)\s+(ONLY\s+)?[\"\w.]*\bpersons\b", re.IGNORECASE
)


def _names(node: ast.AST) -> set[str]:
    """Identifiers, attribute names, keyword names and string constants anywhere under `node`."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.keyword) and child.arg:
            found.add(child.arg)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            found.add(child.value)
    return found


def _is_write_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in WRITE_FUNCS
    if isinstance(func, ast.Attribute) and func.attr in WRITE_FUNCS:
        receiver = func.value
        if isinstance(receiver, ast.Name):
            return receiver.id in TABLE_NAMES | MODULE_ALIASES
        if isinstance(receiver, ast.Attribute):
            return receiver.attr in TABLE_NAMES | MODULE_ALIASES
    return False


def _is_write_method(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr in WRITE_METHODS


def _is_delete(call: ast.Call) -> bool:
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "delete") or (
        isinstance(func, ast.Attribute) and func.attr == "delete"
    )


def find_violations(root: Path) -> list[str]:
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    violations: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def enclosing_statement(node: ast.AST) -> ast.AST:
            while not isinstance(node, ast.stmt):
                node = parents[node]
            return node

        for node in ast.walk(tree):
            where = f"{path}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, ast.Call):
                if _is_write_call(node):
                    if _is_delete(node) and "persons" in _names(node):
                        violations.append(f"{where}: deletes from persons")
                    if _names(enclosing_statement(node)) & PROTECTED:
                        violations.append(f"{where}: write statement names tags or is_marked")
                elif _is_write_method(node) and _names(node) - {node.func.attr} & PROTECTED:
                    violations.append(f"{where}: {node.func.attr}() names tags or is_marked")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if RAW_WRITE.search(node.value) and RAW_PROTECTED.search(node.value):
                    violations.append(f"{where}: raw SQL writes tags or is_marked")
                if RAW_REMOVES_PERSONS.search(node.value):
                    violations.append(f"{where}: raw SQL deletes from persons")
    return violations


def test_package_never_writes_user_columns_or_deletes_persons():
    assert find_violations(PACKAGE) == []


VIOLATIONS = {
    "update_values": "conn.execute(update(persons).values(tags=['x']))",
    "table_update": "conn.execute(persons.update().values(is_marked=True))",
    "upsert_set": "stmt.on_conflict_do_update(index_elements=[persons.c.id], set_={'tags': stmt.excluded.tags})",
    "set_in_statement_chain": (
        "conn.execute(insert(persons).values(id=1).on_conflict_do_update("
        "index_elements=[persons.c.id], set_={persons.c.is_marked: True}))"
    ),
    "values_dict": "stmt = stmt.values({'is_marked': True})",
    "delete_function": "conn.execute(delete(persons))",
    "delete_method": "conn.execute(persons.delete().where(persons.c.id == 1))",
    "raw_update_tags": "conn.execute(text(\"UPDATE persons SET tags = '{}'\"))",
    "raw_update_mark": "conn.execute(text('UPDATE persons SET is_marked = false'))",
    "raw_insert_tags": "conn.execute(text(\"INSERT INTO persons (id, username, tags) VALUES (1, 'a', '{x}')\"))",
    "raw_delete": "conn.execute(text('DELETE FROM persons WHERE id = 1'))",
    "raw_truncate": "conn.execute(text('TRUNCATE TABLE persons'))",
}


@pytest.mark.parametrize("source", VIOLATIONS.values(), ids=VIOLATIONS.keys())
def test_scan_fails_on_a_deliberate_violation(tmp_path, source):
    bad = tmp_path / "bad.py"
    bad.write_text(f"def run(conn, stmt):\n    {source}\n")
    assert find_violations(bad)


ALLOWED = {
    "select_user_columns": "conn.execute(select(persons.c.tags, persons.c.is_marked))",
    "upsert_without_user_columns": (
        "conn.execute(insert(persons).values(id=1, username='a').on_conflict_do_update("
        "index_elements=[persons.c.id], set_={'username': 'a'}))"
    ),
    "update_another_table": "conn.execute(update(scans).values(cursor='12'))",
    "dict_update_with_a_tags_key": "row.update(tags=['x'])",
    "raw_select_of_user_columns": "conn.execute(text(\"SELECT id FROM persons WHERE 'lab' = ANY(tags)\"))",
}


@pytest.mark.parametrize("source", ALLOWED.values(), ids=ALLOWED.keys())
def test_scan_allows_reading_user_columns_and_other_writes(tmp_path, source):
    ok = tmp_path / "ok.py"
    ok.write_text(f"def run(conn, row):\n    {source}\n")
    assert find_violations(ok) == []
