"""Форматирование результата для чтения моделью.

Осознанное отличие от audit_analyzer, который печатает JSON: здесь основной
вывод — markdown-таблица. Потребитель stdout — языковая модель, а JSON на
50 строк стоит в 2-3 раза больше токенов, чем та же таблица; при
последовательной аналитике (запрос → результат → следующий запрос) это
разница между «влезло в контекст» и «не влезло». Машинный формат остаётся
доступен через ``--json``.
"""

from __future__ import annotations

import json
from typing import Any

_MAX_COL_WIDTH = 40


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    text = str(value)
    if len(text) > _MAX_COL_WIDTH:
        text = text[: _MAX_COL_WIDTH - 1] + "…"
    return text.replace("|", "\\|")


def as_markdown(result: dict[str, Any]) -> str:
    """Результат запроса — markdown-таблицей с пометкой об урезании."""
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if not columns:
        return "Запрос выполнен, результат пуст."
    if not rows:
        return "Запрос выполнен, строк не найдено."

    header = "| " + " | ".join(_fmt(c) for c in columns) + " |"
    divider = "|" + "|".join(["---"] * len(columns)) + "|"
    body = [
        "| " + " | ".join(_fmt(v) for v in row) + " |"
        for row in rows
    ]
    lines = [header, divider, *body, "", f"Строк: {result.get('row_count', len(rows))}"]
    if result.get("truncated"):
        lines.append(
            "ВНИМАНИЕ: результат урезан лимитом. Это не все данные — "
            "агрегируй в SQL вместо того, чтобы читать строки."
        )
    return "\n".join(lines)


def schema_as_markdown(rows: list[dict[str, Any]]) -> str:
    """Схема таблиц — сгруппированный список колонок."""
    if not rows:
        return "Схема не найдена: проверь db_schema и db_tables в project.json."
    by_table: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_table.setdefault(str(row.get("table_name")), []).append(row)

    blocks = []
    for table, columns in by_table.items():
        lines = [f"## {table}", "", "| колонка | тип | NULL |", "|---|---|---|"]
        lines += [
            "| {} | {} | {} |".format(
                _fmt(c.get("column_name")),
                _fmt(c.get("data_type")),
                "да" if str(c.get("is_nullable")).upper() == "YES" else "нет",
            )
            for c in columns
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def chart_as_markdown(chart: dict[str, Any], *, truncated: bool = False) -> str:
    """Итог отрисовки: путь к файлу плюс что именно нарисовано.

    Путь печатается markdown-картинкой и отдельной строкой: часть интерфейсов
    показывает изображение, остальным нужен просто путь к файлу.
    """
    path = chart.get("path", "")
    series = ", ".join(chart.get("series") or []) or "—"
    lines = [
        f"![график]({path})",
        "",
        f"График сохранён: {path}",
        f"Тип: {chart.get('chart_type')}; точек: {chart.get('points')}; серии: {series}",
    ]
    if truncated:
        lines.append(
            "ВНИМАНИЕ: данные для графика урезаны лимитом точек — "
            "картина неполная. Сузь период или агрегируй крупнее."
        )
    return "\n".join(lines)


def as_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
