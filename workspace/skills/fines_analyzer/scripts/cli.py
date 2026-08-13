"""Точка входа навыка fines_analyzer (вызывается из fines_analyze.bat/.sh).

Режимы:
    schema  — колонки и типы разрешённых таблиц
    query   — выполнить SELECT: проверка → read-only транзакция → таблица
    chart   — то же плюс отрисовка результата в PNG

SQL пишет сам агент, а не вторая языковая модель внутри скрипта. Это
намеренно: аналитика здесь последовательная (запрос → результат → следующий
запрос с учётом увиденного), и агенту нужно видеть промежуточные результаты.
Скрипт только проверяет и исполняет.

Режим chart получает данные тем же --sql через тот же валидатор, а не
готовыми числами в аргументе: цифры на картинке тогда гарантированно те же,
что вернула БД, и не зависят от того, верно ли модель переписала их из
предыдущего ответа.

Примеры:
    fines_analyze --mode schema
    fines_analyze --mode query --sql "SELECT count(*) FROM oarb.fines"
    fines_analyze --mode query --sql "..." --json
    fines_analyze --mode chart --sql "SELECT m, sum(amt) FROM oarb.fines GROUP BY 1 ORDER BY 1" \
        --chart-type line --title "Динамика штрафов"
"""

from __future__ import annotations

import argparse
import sys
import traceback

import skill_config  # noqa: F401 — импорт настраивает sys.path, должен быть первым

import charts  # noqa: E402 — matplotlib внутри него тоже импортируется лениво
import output  # noqa: E402
from validation import SqlPolicy, SqlRejected, validate_sql  # noqa: E402

# database импортируется лениво, внутри режимов: он тянет psycopg2, а --help и
# отказ валидатора должны работать даже там, где драйвер БД не установлен.


_UNCONFIGURED = (
    "Навык не настроен: заполни skills.fines_analyzer.db_schema и main_table "
    "в project.json. Пока они пусты, доступ к данным закрыт."
)


def _configured() -> bool:
    """Ненастроенный навык не должен открывать доступ ко всей базе.

    Пустые db_schema и main_table означали бы пустой белый список, то есть
    отсутствие ограничений, — поэтому такая конфигурация трактуется как отказ.
    """
    return bool(skill_config.get_schema() and
                (skill_config.get_main_table() or skill_config.get_tables()))


def _policy(max_rows: int | None = None) -> SqlPolicy:
    """Границы доступа из project.json.

    ``max_rows`` переопределяется режимом chart: у графика свой, больший
    лимит точек — таблицу читает модель, а картинку человек.
    """
    schema = skill_config.get_schema()
    tables = skill_config.get_tables()
    main_table = skill_config.get_main_table()
    if main_table and main_table not in tables:
        tables = [*tables, main_table]

    qualified = {
        t.lower() if "." in t else f"{schema.lower()}.{t.lower()}"
        for t in tables
        if t
    }
    return SqlPolicy(
        schema=schema,
        allowed_tables=frozenset(qualified),
        max_rows=skill_config.get_max_rows() if max_rows is None else max_rows,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fines_analyze",
        description="Аналитика по таблице штрафов в Greenplum (только чтение).",
    )
    parser.add_argument("--mode", required=True, choices=("schema", "query", "chart"))
    parser.add_argument("--sql", default="", help="SELECT для режимов query и chart")
    parser.add_argument("--json", action="store_true", help="Машинный вывод JSON")

    group = parser.add_argument_group("график (--mode chart)")
    group.add_argument("--chart-type", default="bar", choices=charts.CHART_TYPES,
                       help="Тип графика (по умолчанию bar)")
    group.add_argument("--title", default="", help="Заголовок графика")
    group.add_argument("--stacked", action="store_true",
                       help="Накопительные столбцы для нескольких серий")
    group.add_argument("--x-column", default="",
                       help="Колонка подписей (по умолчанию первая)")
    group.add_argument("--y-columns", default="",
                       help="Колонки значений через запятую (по умолчанию все числовые)")
    return parser


def _run_schema(args: argparse.Namespace) -> int:
    import database

    try:
        rows = database.get_schema(skill_config.get_schema(), skill_config.get_tables())
    except database.QueryFailed as exc:
        print(str(exc), file=sys.stderr)
        return 4

    if args.json:
        print(output.as_json({"status": "success", "mode": "schema", "columns": rows}))
    else:
        print(output.schema_as_markdown(rows))
    return 0


class _Failed(Exception):
    """Ошибка режима: текст уже пригоден для показа модели, код — для exit."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def _checked_query(sql: str, mode: str, max_rows: int) -> tuple[str, dict]:
    """Проверить SQL и выполнить его. Общее начало режимов query и chart."""
    if not sql.strip():
        raise _Failed(f"Ошибка: для --mode {mode} нужен --sql.", 2)
    if not _configured():
        raise _Failed(_UNCONFIGURED, 2)

    # Проверка идёт до импорта database: отклонённый запрос не должен зависеть
    # от доступности драйвера БД, и до соединения дело не доходит.
    try:
        checked = validate_sql(sql, _policy(max_rows))
    except SqlRejected as exc:
        raise _Failed(f"Запрос отклонён: {exc}", 3) from exc

    import database

    try:
        result = database.run_query(
            checked,
            max_rows=max_rows,
            max_cell_chars=skill_config.get_max_cell_chars(),
            timeout_sec=skill_config.get_query_timeout_sec(),
        )
    except database.QueryFailed as exc:
        raise _Failed(str(exc), 4) from exc
    return checked, result


def _run_query(args: argparse.Namespace) -> int:
    try:
        checked, result = _checked_query(args.sql, "query", skill_config.get_max_rows())
    except _Failed as exc:
        print(str(exc), file=sys.stderr)
        return exc.code

    if args.json:
        print(output.as_json({"status": "success", "mode": "query",
                              "sql": checked, **result}))
    else:
        print(output.as_markdown(result))
    return 0


def _run_chart(args: argparse.Namespace) -> int:
    try:
        checked, result = _checked_query(
            args.sql, "chart", skill_config.get_chart_max_points()
        )
    except _Failed as exc:
        print(str(exc), file=sys.stderr)
        return exc.code

    try:
        chart = charts.render(
            result,
            charts_dir=skill_config.get_charts_dir(),
            sql=checked,
            title=args.title,
            chart_type=args.chart_type,
            stacked=args.stacked,
            x_column=args.x_column,
            y_columns=args.y_columns,
            max_files=skill_config.get_chart_max_files(),
        )
    except charts.ChartFailed as exc:
        print(f"График не построен: {exc}", file=sys.stderr)
        return 5

    truncated = bool(result.get("truncated"))
    if args.json:
        print(output.as_json({"status": "success", "mode": "chart",
                              "sql": checked, "truncated": truncated, **chart}))
    else:
        print(output.chart_as_markdown(chart, truncated=truncated))
    return 0


def main() -> None:
    try:
        args = _build_parser().parse_args()
        runner = {"schema": _run_schema, "query": _run_query, "chart": _run_chart}
        sys.exit(runner[args.mode](args))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"Внутренняя ошибка: {exc}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
