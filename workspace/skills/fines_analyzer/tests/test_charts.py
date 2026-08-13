"""Проверка раскладки результата по осям и отрисовки.

Разбор серий базы и matplotlib не требует. Тесты самой отрисовки
пропускаются, если matplotlib не установлен.

Запуск:
    python tests/test_charts.py
    pytest tests/test_charts.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import charts  # noqa: E402
from charts import ChartFailed, extract_series  # noqa: E402


def _result(columns, rows, truncated=False):
    return {"columns": columns, "rows": rows,
            "row_count": len(rows), "truncated": truncated}


def _failed(**kwargs) -> str:
    try:
        extract_series(**kwargs)
    except ChartFailed as exc:
        return str(exc)
    raise AssertionError("Ожидался отказ ChartFailed")


def test_first_column_is_labels_rest_are_series():
    labels, series = extract_series(
        _result(["month", "cnt"], [["янв", 10], ["фев", 12]])
    )
    assert labels == ["янв", "фев"]
    assert series == [("cnt", [10.0, 12.0])]


def test_several_numeric_columns_become_several_series():
    _, series = extract_series(
        _result(["month", "cnt", "amount"], [["янв", 10, 500], ["фев", 12, 600]])
    )
    assert [name for name, _ in series] == ["cnt", "amount"]


def test_decimal_and_null_are_handled():
    """psycopg2 отдаёт NUMERIC как Decimal, а NULL не должен ронять серию."""
    _, series = extract_series(
        _result(["m", "amount"], [["янв", Decimal("1234.50")], ["фев", None]])
    )
    assert series == [("amount", [1234.5, 0.0])]


def test_bool_column_is_not_numeric():
    """bool — подкласс int, но рисовать «да/нет» как числа бессмысленно."""
    message = _failed(result=_result(["m", "paid"], [["янв", True], ["фев", False]]))
    assert "числовых колонок" in message


def test_explicit_columns_override_defaults():
    labels, series = extract_series(
        _result(["cnt", "month", "amount"], [[10, "янв", 500], [12, "фев", 600]]),
        x_column="month",
        y_columns="amount",
    )
    assert labels == ["янв", "фев"]
    assert series == [("amount", [500.0, 600.0])]


def test_unknown_column_is_reported_with_available_names():
    message = _failed(result=_result(["m", "cnt"], [["янв", 1]]), x_column="нет_такой")
    assert "нет_такой" in message and "cnt" in message


def test_empty_result_is_rejected():
    assert "не вернул строк" in _failed(result=_result(["m", "cnt"], []))


def test_dates_become_iso_labels():
    from datetime import date

    labels, _ = extract_series(_result(["d", "cnt"], [[date(2026, 3, 1), 5]]))
    assert labels == ["2026-03-01"]


def _skip_without_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("  пропущено: matplotlib не установлен")
        return True
    return False


def test_render_writes_png():
    if _skip_without_matplotlib():
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        charts_dir = Path(tmp) / "charts"
        for chart_type in charts.CHART_TYPES:
            info = charts.render(
                _result(["month", "cnt"], [["янв", 10], ["фев", 12]]),
                charts_dir=charts_dir,
                chart_type=chart_type,
                title="Динамика штрафов",
            )
            path = Path(info["path"])
            assert path.exists() and path.stat().st_size > 0
            assert info["points"] == 2


def test_render_is_deterministic_for_same_chart():
    if _skip_without_matplotlib():
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        charts_dir = Path(tmp) / "charts"
        data = _result(["m", "cnt"], [["янв", 1]])
        first = charts.render(data, charts_dir=charts_dir, sql="SELECT 1", title="A")
        second = charts.render(data, charts_dir=charts_dir, sql="SELECT 1", title="A")
        assert first["path"] == second["path"]
        assert len(list(charts_dir.glob("*.png"))) == 1


def test_pie_rejects_negative_values():
    if _skip_without_matplotlib():
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        try:
            charts.render(
                _result(["m", "cnt"], [["янв", -5], ["фев", 3]]),
                charts_dir=Path(tmp),
                chart_type="pie",
            )
        except ChartFailed as exc:
            assert "отрицательным" in str(exc)
        else:
            raise AssertionError("Круговая диаграмма по отрицательным значениям")


def test_unknown_chart_type_is_rejected():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        try:
            charts.render(_result(["m", "c"], [["янв", 1]]),
                          charts_dir=Path(tmp), chart_type="donut")
        except ChartFailed as exc:
            assert "donut" in str(exc)
        else:
            raise AssertionError("Неизвестный тип графика должен отклоняться")


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"OK   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\nПровалено: {failures}" if failures else "\nВсе тесты пройдены.")
    sys.exit(1 if failures else 0)
