"""Настройки навыка fines_analyzer из project.json (секция skills.fines_analyzer).

Бутстрап sys.path такой же, как в audit_analyzer/scripts/skill_config.py, но
добавляются ДВА каталога: корень проекта (для `config`) и `workspace/`
(для `utils.db`). Скрипт запускается напрямую через .bat/.sh, вне gateway,
поэтому рассчитывать на пути, которые ставит gateway.py, нельзя.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE_ROOT = _SKILL_ROOT.parents[1]        # workspace/
_PROJECT_ROOT = _SKILL_ROOT.parents[2]          # корень проекта

for _path in (_PROJECT_ROOT, _WORKSPACE_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from config import SETTINGS  # noqa: E402  — только после правки sys.path

_CFG: dict[str, Any] = SETTINGS.get("skills", {}).get("fines_analyzer", {})

# Значения по умолчанию намеренно консервативные: пока схема не заполнена
# в project.json, навык не должен молча ходить в произвольные таблицы.
_DEFAULT_MAX_ROWS = 50
_DEFAULT_MAX_CELL_CHARS = 300
_DEFAULT_TIMEOUT_SEC = 60
# График читается глазами, а не по токенам, поэтому точек ему нужно больше,
# чем строк таблице: 60 месяцев динамики в max_rows=50 не помещаются.
_DEFAULT_CHART_MAX_POINTS = 200
_DEFAULT_CHART_MAX_FILES = 100


def get_schema() -> str:
    """Схема Greenplum, в которой лежит таблица штрафов."""
    return str(_CFG.get("db_schema", "") or "").strip()


def get_tables() -> list[str]:
    """Белый список таблиц. Пусто — разрешены любые в рамках схемы."""
    tables = _CFG.get("db_tables", []) or []
    return [str(t).strip() for t in tables if str(t).strip()]


def get_main_table() -> str:
    """Основная таблица навыка (таблица штрафов)."""
    return str(_CFG.get("main_table", "") or "").strip()


def get_max_rows() -> int:
    return int(_CFG.get("max_rows", _DEFAULT_MAX_ROWS))


def get_max_cell_chars() -> int:
    return int(_CFG.get("max_cell_chars", _DEFAULT_MAX_CELL_CHARS))


def get_query_timeout_sec() -> int:
    return int(_CFG.get("query_timeout_sec", _DEFAULT_TIMEOUT_SEC))


def get_chart_max_points() -> int:
    """Максимум точек на графике (лимит строк запроса в режиме chart)."""
    return int(_CFG.get("chart_max_points", _DEFAULT_CHART_MAX_POINTS))


def get_chart_max_files() -> int:
    """Максимум PNG в каталоге графиков; лишние старые удаляются."""
    return int(_CFG.get("chart_max_files", _DEFAULT_CHART_MAX_FILES))


def get_charts_dir() -> Path:
    """Каталог для PNG — рядом с прочими артефактами агента в data_store/.

    Не внутри навыка: data_store/ уже задуман как место для файлов, которые
    навыки отдают агенту (gateway.py выгружает туда большие результаты), и
    чистится по тем же правилам.
    """
    configured = str(_CFG.get("charts_dir", "") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else _WORKSPACE_ROOT / path
    return _WORKSPACE_ROOT / "data_store" / "charts"
