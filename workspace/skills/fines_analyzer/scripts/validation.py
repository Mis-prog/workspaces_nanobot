"""Проверка SQL до выполнения: разбор AST через SQLGlot, диалект postgres.

Отличие от проверки по ключевым словам (первое слово + счёт `;`): она
пропускает `SELECT 1; DROP TABLE t` (точка с запятой одна) и
`WITH x AS (SELECT 1) INSERT INTO ...` (первое слово — WITH). Здесь запрос
разбирается в дерево, поэтому такие формы отсекаются по типам узлов.

Правила:
  * ровно один statement;
  * корень — только SELECT / UNION / INTERSECT / EXCEPT;
  * ни одного узла записи или DDL нигде в дереве;
  * нераспарсенная команда (exp.Command: SET, SHOW, COPY, вызовы) — отказ;
  * физические таблицы проверяются по белому списку, алиасы CTE исключаются
    через scopes и потому не маскируют настоящие таблицы;
  * внешний LIMIT дописывается через AST, если его нет.

Всё, что не удалось разобрать, отклоняется (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from sqlglot import exp, parse
    from sqlglot.errors import ParseError, TokenError
    from sqlglot.optimizer.scope import traverse_scope
except ImportError as _exc:  # pragma: no cover — окружение без sqlglot
    raise RuntimeError(
        "Не установлен sqlglot — проверка SQL невозможна. "
        "Установите: pip install 'sqlglot>=25'"
    ) from _exc

DIALECT = "postgres"  # GP 6.25 по синтаксису — PostgreSQL 9.4


class SqlRejected(ValueError):
    """Запрос отклонён до выполнения. Текст предназначен для модели."""


@dataclass(frozen=True)
class SqlPolicy:
    """Границы, в которых навыку разрешено читать данные."""

    schema: str = ""
    allowed_tables: frozenset[str] = field(default_factory=frozenset)
    max_rows: int = 50
    enforce_limit: bool = True


# Узлы записи и DDL. Состав классов у sqlglot менялся между версиями,
# поэтому берём только те, что есть в установленной.
_WRITE_NODE_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable",
    "TruncateTable", "Merge", "Grant", "Revoke", "Copy", "Call", "Command",
)
_WRITE_NODES = tuple(
    node
    for node in (getattr(exp, name, None) for name in _WRITE_NODE_NAMES)
    if isinstance(node, type)
)

_ROOT_ALLOWED = tuple(
    node
    for node in (
        getattr(exp, name, None)
        for name in ("Select", "Union", "Intersect", "Except", "Subquery")
    )
    if isinstance(node, type)
)


def _parse_single(sql: str):
    """Разобрать ровно один statement или отклонить запрос."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SqlRejected("Пустой запрос.")
    try:
        statements = parse(text, read=DIALECT)
    except (ParseError, TokenError) as exc:
        raise SqlRejected(f"Не удалось разобрать SQL: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — неизвестная ошибка парсера тоже отказ
        raise SqlRejected(f"Не удалось разобрать SQL: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if not statements:
        raise SqlRejected("Не удалось разобрать SQL: пустое дерево разбора.")
    if len(statements) > 1:
        raise SqlRejected(
            "Разрешён ровно один запрос за вызов; несколько statement'ов "
            "через ';' не выполняются."
        )
    return statements[0]


def _check_read_only(statement) -> None:
    """Отклонить всё, что не является чтением."""
    root = statement
    while isinstance(root, exp.Subquery) and root.this is not None:
        root = root.this

    if _ROOT_ALLOWED and not isinstance(root, _ROOT_ALLOWED):
        raise SqlRejected(
            f"Разрешены только SELECT-запросы, получено: {type(root).__name__.upper()}."
        )

    for node_type in _WRITE_NODES:
        found = statement.find(node_type)
        if found is not None:
            raise SqlRejected(
                "Запрос содержит операцию изменения данных или служебную команду "
                f"({type(found).__name__.upper()}). Доступ только на чтение."
            )


def _physical_tables(statement) -> set[str]:
    """Реальные таблицы запроса; имена CTE исключаются через scopes."""
    tables: set[str] = set()
    try:
        scopes = traverse_scope(statement)
    except Exception as exc:  # noqa: BLE001 — не разобрали scopes => отказ
        raise SqlRejected(f"Не удалось разобрать структуру запроса: {exc}") from exc

    for scope in scopes:
        cte_names = set(getattr(scope, "cte_sources", {}) or {})
        for table in getattr(scope, "tables", []) or []:
            if not isinstance(table, exp.Table):
                continue
            name = (table.name or "").strip()
            if not name:
                continue
            db = (table.db or "").strip()
            # Имя без схемы, совпадающее с CTE этого scope, — не физическая таблица.
            if not db and name in cte_names:
                continue
            tables.add(f"{db}.{name}".lower() if db else name.lower())
    return tables


def _check_tables(statement, policy: SqlPolicy) -> None:
    """Сверить таблицы запроса с белым списком."""
    tables = _physical_tables(statement)
    if not tables:
        # SELECT без FROM (например, SELECT now()) безопасен.
        return

    allowed = {t.lower() for t in policy.allowed_tables}
    schema = policy.schema.strip().lower()

    for table in sorted(tables):
        qualified = table if "." in table else (f"{schema}.{table}" if schema else table)
        if allowed:
            if qualified not in allowed and table not in allowed:
                raise SqlRejected(
                    f"Таблица {qualified} недоступна навыку. Разрешены: "
                    + ", ".join(sorted(allowed))
                    + "."
                )
        elif schema and "." in qualified and not qualified.startswith(f"{schema}."):
            raise SqlRejected(
                f"Таблица {qualified} вне разрешённой схемы {schema}."
            )


def _apply_limit(statement, policy: SqlPolicy):
    """Дописать внешний LIMIT, если его нет."""
    if not policy.enforce_limit:
        return statement
    if statement.args.get("limit") is not None:
        return statement
    try:
        return statement.limit(policy.max_rows)
    except Exception:  # noqa: BLE001 — не смогли дописать: отдаём как есть,
        return statement  # ограничение всё равно продублировано fetchmany().


def validate_sql(sql: str, policy: SqlPolicy) -> str:
    """Проверить запрос и вернуть нормализованный SQL.

    Raises:
        SqlRejected: с текстом, который можно показать модели.
    """
    statement = _parse_single(sql)
    _check_read_only(statement)
    _check_tables(statement, policy)
    statement = _apply_limit(statement, policy)
    return statement.sql(dialect=DIALECT)
