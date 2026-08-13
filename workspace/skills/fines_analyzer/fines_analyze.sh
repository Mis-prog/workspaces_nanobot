#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python &> /dev/null; then
    echo "Ошибка: Python не установлен или не найден в PATH."
    exit 1
fi
python scripts/cli.py "$@"
