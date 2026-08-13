"""Доступ к Greenplum: интроспекция схемы и выполнение проверенного SELECT.

Соединения берутся из общего коннектора ``workspace/utils/db.py`` — свои
psycopg2-соединения навык не открывает: в CHANGELOG проекта зафиксировано,
что число соединений к GP пришлось снижать из-за "too many connections".

Каждый запрос выполняется в транзакции, где явно выставлены:
  * ``SET TRANSACTION READ ONLY`` — запись невозможна на уровне СУБД,
    даже если проверка в validation.py будет когда-нибудь обойдена;
  * ``SET LOCAL statement_timeout`` — GP сам снимет затянувшийся запрос.

Строки берутся с запасом в одну (``max_rows + 1``), чтобы отличить полный
результат от урезанного и честно сказать об этом модели.
"""

from __future__ import annotations

from typing import Any

import skill_config  # noqa: F401 — импорт настраивает sys.path, должен быть первым

from utils.db import fetch, transaction  # noqa: E402

# SQLSTATE → понятная модели формулировка. Разбирать текст исключения не нужно:
# psycopg2 отдаёт код в exc.pgcode.
_SQLSTATE_MESSAGES = {
    "42P01": "Таблица не найдена. Проверь имя через режим schema.",
    "42703": "Колонка не найдена. Проверь имена колонок через режим schema.",
    "42601": "Синтаксическая ошибка в SQL.",
    "42501": "Недостаточно прав на объект.",
    "57014": "Запрос отменён по таймауту. Сузь период или добавь фильтры.",
    "53200": "Нехватка памяти на сегменте. Уменьши объём выборки.",
    "22012": "Деление на ноль. Используй NULLIF в знаменателе.",
}


class QueryFailed(RuntimeError):
    """Ошибка выполнения, текст пригоден для показа модели."""


def _classify(exc: Exception) -> str:
    """Перевести ошибку psycopg2 в короткое сообщение для модели."""
    code = getattr(exc, "pgcode", None)
    known = _SQLSTATE_MESSAGES.get(code or "")
    detail = str(getattr(exc, "pgerror", None) or exc).strip().splitlines()
    first_line = detail[0] if detail else str(exc)
    if known:
        return f"{known} ({code}): {first_line}"
    return f"Ошибка выполнения запроса: {first_line}"


def _cell(value: Any, max_chars: int) -> Any:
    """Обрезать длинное значение, оставив пометку."""
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def get_schema(schema: str, tables: list[str]) -> list[dict[str, Any]]:
    """Колонки и типы таблиц навыка из information_schema."""
    if not schema:
        raise QueryFailed(
            "Не задана схема БД: заполни skills.fines_analyzer.db_schema в project.json."
        )
    sql = """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY table_name, ordinal_position
    """
    args: list[Any] = [schema]
    if tables:
        sql = sql.replace(
            "WHERE table_schema = %s",
            "WHERE table_schema = %s AND table_name = ANY(%s)",
        )
        args.append(list(tables))
    try:
        return fetch(sql, *args)
    except Exception as exc:  # noqa: BLE001
        raise QueryFailed(_classify(exc)) from exc


def run_query(
    sql: str,
    *,
    max_rows: int,
    max_cell_chars: int,
    timeout_sec: int,
) -> dict[str, Any]:
    """Выполнить уже проверенный SELECT в read-only транзакции."""
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_sec) * 1000}")
                cur.execute(sql)
                columns = [d[0] for d in (cur.description or [])]
                raw = cur.fetchmany(max_rows + 1)
    except Exception as exc:  # noqa: BLE001
        raise QueryFailed(_classify(exc)) from exc

    truncated = len(raw) > max_rows
    rows = [
        [_cell(value, max_cell_chars) for value in row]
        for row in raw[:max_rows]
    ]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
