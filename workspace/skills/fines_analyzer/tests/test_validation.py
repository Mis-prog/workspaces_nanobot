"""Проверка валидатора SQL. Базы данных не требует — только sqlglot.

Запуск:
    python tests/test_validation.py
    pytest tests/test_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validation import SqlPolicy, SqlRejected, validate_sql  # noqa: E402

POLICY = SqlPolicy(
    schema="oarb",
    allowed_tables=frozenset({"oarb.fines", "oarb.violators"}),
    max_rows=50,
)


def _rejected(sql: str, policy: SqlPolicy = POLICY) -> str:
    try:
        validate_sql(sql, policy)
    except SqlRejected as exc:
        return str(exc)
    raise AssertionError(f"Запрос должен был быть отклонён: {sql}")


def test_select_passes_and_gets_limit():
    result = validate_sql("SELECT count(*) FROM oarb.fines", POLICY)
    assert "LIMIT 50" in result.upper()


def test_existing_limit_is_kept():
    result = validate_sql("SELECT * FROM oarb.fines LIMIT 5", POLICY)
    assert result.upper().count("LIMIT") == 1
    assert "LIMIT 5" in result.upper()


def test_write_statements_rejected():
    for sql in (
        "INSERT INTO oarb.fines (id) VALUES (1)",
        "UPDATE oarb.fines SET amount = 0",
        "DELETE FROM oarb.fines",
        "DROP TABLE oarb.fines",
        "TRUNCATE oarb.fines",
        "CREATE TABLE oarb.tmp (id int)",
    ):
        _rejected(sql)


def test_semicolon_smuggling_rejected():
    """Ровно тот случай, который проходит проверку по первому слову."""
    _rejected("SELECT 1; DROP TABLE oarb.fines")


def test_cte_prefix_smuggling_rejected():
    """Первое слово WITH, а внутри — запись."""
    _rejected("WITH x AS (SELECT 1) INSERT INTO oarb.fines SELECT * FROM x")


def test_opaque_command_rejected():
    _rejected("SET statement_timeout = 0")


def test_table_outside_allowlist_rejected():
    message = _rejected("SELECT * FROM oarb.secrets")
    assert "oarb.secrets" in message


def test_cte_alias_is_not_a_table():
    """Алиас CTE не должен приниматься за физическую таблицу."""
    sql = "WITH secrets AS (SELECT 1 AS x) SELECT * FROM secrets"
    validate_sql(sql, POLICY)


def test_same_named_real_table_still_checked():
    """CTE в одном scope не открывает доступ к одноимённой таблице в другом."""
    sql = (
        "WITH secrets AS (SELECT 1 AS x) "
        "SELECT * FROM secrets UNION ALL SELECT id FROM oarb.secrets"
    )
    _rejected(sql)


def test_unqualified_table_resolves_to_schema():
    validate_sql("SELECT * FROM fines", POLICY)


def test_join_within_allowlist_passes():
    sql = (
        "SELECT f.id, v.name FROM oarb.fines f "
        "JOIN oarb.violators v ON v.id = f.violator_id"
    )
    validate_sql(sql, POLICY)


def test_multiple_statements_rejected():
    _rejected("SELECT 1; SELECT 2")


def test_unparseable_fails_closed():
    _rejected("SELECT FROM WHERE ((")


def test_select_without_from_allowed():
    validate_sql("SELECT now()", POLICY)


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERR  {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} прошло")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
