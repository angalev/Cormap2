"""Медленные тесты (запуск: pytest -m slow)."""

import time

import pytest

from cormap.check import check_workbook
from tests.synth import make_workbook


@pytest.mark.slow
def test_read_300km_step_2m(tmp_path, capsys):
    t0 = time.perf_counter()
    path = make_workbook(tmp_path / "long.xlsx", length_m=300_000, step_m=2.0)
    t_gen = time.perf_counter() - t0
    t0 = time.perf_counter()
    survey, report, notes = check_workbook(path)
    t_read = time.perf_counter() - t0
    with capsys.disabled():
        print(f"\n[slow] генерация {t_gen:.1f} с, чтение и проверка {t_read:.1f} с")
    assert survey is not None and report.ok
    assert len(survey.intensive.dist) == 150_001
    assert notes is not None
    assert t_read <= 60
