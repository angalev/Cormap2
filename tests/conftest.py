import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

from tests.fake_refinery import fake_refinery  # noqa: F401  (фикстура заглушки refinery)

REFERENCE = Path(__file__).resolve().parent.parent / "reference"

BASE = ["Дистанция, м", "U т-з, В", "U т-з выкл, В"]


@pytest.fixture(autouse=True)
def _hide_real_refinery(monkeypatch):
    """Настоящий модуль заказчика (cormap/refinery.py) в тестах не участвует.

    Тесты работают на заглушке `fake_refinery`; без неё модуль считается отсутствующим.
    """
    monkeypatch.setitem(sys.modules, "cormap.refinery", None)


@pytest.fixture
def book(tmp_path):
    """Конструктор книги: book(columns, rows, sheet="Result", name="b.xlsx", extra=None).

    rows - списки значений в порядке columns; extra - {имя листа: (columns, rows)}.
    """

    def build(columns, rows, sheet="Result", name="b.xlsx", extra=None):
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
        ws.append(list(columns))
        for r in rows:
            ws.append(list(r))
        for title, (cols, rws) in (extra or {}).items():
            w = wb.create_sheet(title)
            w.append(list(cols))
            for r in rws:
                w.append(list(r))
        path = tmp_path / name
        wb.save(path)
        return path

    return build
