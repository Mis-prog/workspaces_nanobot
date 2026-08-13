"""Отрисовка результата запроса в PNG (matplotlib, backend Agg).

Разделение обязанностей то же, что и в остальном навыке: данные берутся
результатом уже проверенного SELECT, здесь только раскладка по осям и
рисование. Своего доступа к БД у модуля нет — на вход приходит словарь от
``database.run_query``.

Раскладка по умолчанию: первая колонка — подписи оси X, все остальные
числовые — серии. Поэтому обычный аналитический запрос
``SELECT month, sum(amount) ... GROUP BY 1 ORDER BY 1`` рисуется без единого
дополнительного аргумента, а разбивка по категориям
(``SELECT month, sum(a), sum(b) ...``) сама становится несколькими сериями.

matplotlib импортируется лениво, внутри :func:`render`: ``--help`` и отказ
валидатора должны работать там, где библиотека не установлена, — так же как
режимы schema/query не требуют psycopg2 до обращения к данным.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

CHART_TYPES = ("bar", "barh", "line", "pie")

# Порядок цветов фиксирован: одна и та же серия должна выглядеть одинаково
# на соседних графиках одного ответа.
_COLORS = (
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
)
_FIG_SIZE = (10, 5.5)
_DPI = 130


class ChartFailed(RuntimeError):
    """Не удалось построить график. Текст предназначен для модели."""


def _numeric(value: Any) -> float | None:
    """Привести значение ячейки к float или вернуть None.

    psycopg2 отдаёт NUMERIC как Decimal, а bool является подклассом int —
    рисовать «истина/ложь» как числа бессмысленно, поэтому bool отсекается.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _label(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)


def _column_index(columns: list[str], name: str) -> int:
    """Найти колонку по имени (без учёта регистра) или по номеру с нуля."""
    lowered = [str(c).lower() for c in columns]
    key = name.strip().lower()
    if key in lowered:
        return lowered.index(key)
    if key.isdigit() and int(key) < len(columns):
        return int(key)
    raise ChartFailed(
        f"Колонка «{name}» отсутствует в результате. Есть: {', '.join(columns)}."
    )


def extract_series(
    result: dict[str, Any],
    *,
    x_column: str = "",
    y_columns: str = "",
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    """Разложить результат запроса на подписи и числовые серии.

    Returns:
        (labels, [(имя_серии, значения), ...])

    Raises:
        ChartFailed: если рисовать нечего — с текстом, который можно
            показать модели.
    """
    columns = [str(c) for c in (result.get("columns") or [])]
    rows = result.get("rows") or []
    if not columns or not rows:
        raise ChartFailed(
            "Запрос не вернул строк — график строить не из чего. "
            "Проверь фильтры через режим query."
        )

    x_idx = _column_index(columns, x_column) if x_column else 0

    if y_columns:
        y_idx = [_column_index(columns, name) for name in y_columns.split(",") if name.strip()]
        if not y_idx:
            raise ChartFailed("В --y-columns не указано ни одной колонки.")
    else:
        # Числовой считается колонка, где есть хотя бы одно разобранное число:
        # NULL в отдельных строках не должен исключать всю серию.
        y_idx = [
            i
            for i in range(len(columns))
            if i != x_idx and any(_numeric(row[i]) is not None for row in rows)
        ]
        if not y_idx:
            raise ChartFailed(
                "В результате нет числовых колонок для оси значений. "
                "Агрегируй данные в SQL (count/sum/avg) или укажи --y-columns."
            )

    labels = [_label(row[x_idx]) for row in rows]
    series = [
        (columns[i], [_numeric(row[i]) or 0.0 for row in rows])
        for i in y_idx
    ]
    return labels, series


def _output_path(charts_dir: Path, title: str, sql: str, chart_type: str) -> Path:
    """Имя файла детерминировано: повтор того же графика не плодит копии."""
    digest = hashlib.sha1(f"{title}|{sql}|{chart_type}".encode()).hexdigest()[:10]
    return charts_dir / f"fines_{digest}.png"


def _prune(charts_dir: Path, max_files: int) -> None:
    """Удалить самые старые PNG, если каталог перерос лимит."""
    if max_files <= 0:
        return
    files = sorted(charts_dir.glob("fines_*.png"), key=lambda p: p.stat().st_mtime)
    for path in files[: max(0, len(files) - max_files)]:
        try:
            path.unlink()
        except OSError:  # каталог общий, файл мог исчезнуть — не наша беда
            pass


def _draw(ax, chart_type, labels, series, stacked):
    """Нарисовать серии выбранным типом на готовых осях."""
    import numpy as np

    positions = np.arange(len(labels))

    if chart_type == "pie":
        name, values = series[0]
        if any(v < 0 for v in values):
            raise ChartFailed(
                "Круговая диаграмма не рисуется по отрицательным значениям — "
                "выбери --chart-type bar."
            )
        if sum(values) <= 0:
            raise ChartFailed("Сумма значений равна нулю — доли посчитать нельзя.")
        ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            colors=_COLORS[: len(values)],
            startangle=90,
        )
        ax.axis("equal")
        ax.set_title(ax.get_title() or name)
        return

    if chart_type == "line":
        for i, (name, values) in enumerate(series):
            ax.plot(positions, values, marker="o", label=name,
                    color=_COLORS[i % len(_COLORS)], linewidth=2)
        ax.set_xticks(positions)
        ax.set_xticklabels(labels)
        return

    horizontal = chart_type == "barh"
    plot = ax.barh if horizontal else ax.bar
    count = len(series)

    if stacked or count == 1:
        bottom = np.zeros(len(labels))
        for i, (name, values) in enumerate(series):
            array = np.array(values, dtype=float)
            kwargs = {"left": bottom} if horizontal else {"bottom": bottom}
            plot(positions, array, label=name,
                 color=_COLORS[i % len(_COLORS)], **(kwargs if stacked else {}))
            if stacked:
                bottom += array
    else:
        width = 0.8 / count
        for i, (name, values) in enumerate(series):
            offset = (i - (count - 1) / 2) * width
            size = {"height": width} if horizontal else {"width": width}
            plot(positions + offset, values, label=name,
                 color=_COLORS[i % len(_COLORS)], **size)

    if horizontal:
        ax.set_yticks(positions)
        ax.set_yticklabels(labels)
        ax.invert_yaxis()  # топ-N читается сверху вниз
    else:
        ax.set_xticks(positions)
        ax.set_xticklabels(labels)


def render(
    result: dict[str, Any],
    *,
    charts_dir: Path,
    sql: str = "",
    title: str = "",
    chart_type: str = "bar",
    stacked: bool = False,
    x_column: str = "",
    y_columns: str = "",
    max_files: int = 100,
) -> dict[str, Any]:
    """Построить PNG по результату запроса и вернуть путь и описание.

    Raises:
        ChartFailed: рисовать нечего или matplotlib не установлен.
    """
    if chart_type not in CHART_TYPES:
        raise ChartFailed(
            f"Неизвестный тип графика «{chart_type}». Доступны: {', '.join(CHART_TYPES)}."
        )

    try:
        import matplotlib
        matplotlib.use("Agg")  # без дисплея: скрипт запускается из агента
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ChartFailed(
            "Не установлен matplotlib — графики недоступны. "
            "Установите: pip install 'matplotlib>=3.8'"
        ) from exc

    labels, series = extract_series(result, x_column=x_column, y_columns=y_columns)
    if chart_type == "pie" and len(series) > 1:
        series = series[:1]  # доли считаются по одной величине

    charts_dir.mkdir(parents=True, exist_ok=True)
    path = _output_path(charts_dir, title, sql, chart_type)

    figure, ax = plt.subplots(figsize=_FIG_SIZE, dpi=_DPI)
    try:
        if title:
            ax.set_title(title, fontsize=13, pad=12)
        _draw(ax, chart_type, labels, series, stacked)

        if chart_type != "pie":
            ax.grid(axis="x" if chart_type == "barh" else "y",
                    linestyle=":", alpha=0.5)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            if len(series) > 1:
                ax.legend(frameon=False, fontsize=9)
            # Длинные подписи категорий иначе налезают друг на друга.
            if chart_type != "barh" and max(len(text) for text in labels) > 6:
                plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

        figure.tight_layout()
        figure.savefig(path, bbox_inches="tight")
    finally:
        plt.close(figure)  # фигуры копятся в памяти, даже если рисование упало

    _prune(charts_dir, max_files)
    return {
        "path": str(path),
        "chart_type": chart_type,
        "points": len(labels),
        "series": [name for name, _ in series],
    }
