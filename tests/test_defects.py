"""Тесты расчёта и ручных данных повреждений ИП (этап 3).

Критерии в тестах условные: они задаются во временных файлах и не являются
критериями заказчика.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cormap.defects import (
    Criteria,
    CriteriaError,
    DefectSegment,
    calc_defects,
    collect_defects,
    load_criteria,
    manual_defects,
    strip_steps,
)
from cormap.model import DefectsBlock, GradientBlock, IntensiveBlock, Survey

# --- вспомогательное -------------------------------------------------------


def write_toml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "defects.toml"
    path.write_text(text, encoding="utf-8")
    return path


def conditional(
    *,
    norm: str = 'mode = "constant"\nvalue = 3.0',
    background: str = "",
    merge: str = "max_gap_m = 4.0\nmin_length_m = 0.0",
    strength: str = "class_1 = 3.0\nclass_2 = 6.0\nclass_3 = 9.0",
    length: str = "",
    final: str = "max",
    columns: str = '["grad_left_on"]',
    combine: str = "max_abs",
    source: str = "calc",
) -> str:
    parts = [
        f'enabled = true\nsource = "{source}"\nfinal_class = "{final}"',
        f'[signal]\ncolumns = {columns}\ncombine = "{combine}"',
        f"[norm]\n{norm}",
        f"[background]\n{background}",
        f"[merge]\n{merge}",
        f"[class_strength]\n{strength}",
        f"[class_length]\n{length}",
    ]
    return "\n".join(parts)


def criteria(tmp_path: Path, **kw) -> Criteria:
    return load_criteria(write_toml(tmp_path, conditional(**kw)))


def grad_survey(values: dict[float, float | None], **extra) -> Survey:
    """Градиенты left_on: фон 1.0 на сетке 0..100 с шагом 2, поверх - values."""
    dist = tuple(float(d) for d in range(0, 101, 2))
    left_on = tuple(values.get(d, 1.0) for d in dist)
    return Survey("t", gradients=GradientBlock(dist=dist, left_on=left_on, **extra))


# Два всплеска: 20..26 (пик 5) и 60..64 (пик 9, но 62 пропущен), одиночная точка 90 (пик 4).
SPIKES: dict[float, float | None] = {
    20.0: 4.0, 22.0: 5.0, 24.0: 4.0, 26.0: 3.5,
    60.0: 7.0, 62.0: None, 64.0: 9.0,
    90.0: 4.0,
}  # fmt: skip


# --- загрузка ---------------------------------------------------------------


def test_shipped_defaults():
    c = load_criteria()
    assert c.enabled is False
    assert c.source == "manual"
    assert c.final_class == "max"
    assert c.strength is None and c.columns is None
    assert calc_defects(grad_survey(SPIKES), c) == []


def test_enabled_without_criteria(tmp_path):
    with pytest.raises(CriteriaError, match="signal.columns"):
        load_criteria(write_toml(tmp_path, "enabled = true"))


def test_enabled_lists_all_missing(tmp_path):
    with pytest.raises(CriteriaError) as err:
        load_criteria(write_toml(tmp_path, "enabled = true"))
    for name in ("norm.mode", "norm.value", "merge.max_gap_m", "class_strength"):
        assert name in str(err.value)


def test_unknown_key(tmp_path):
    with pytest.raises(CriteriaError, match="Неизвестные ключи"):
        load_criteria(write_toml(tmp_path, "enabled = false\nbogus = 1"))
    with pytest.raises(CriteriaError, match=r"\[norm\]"):
        load_criteria(write_toml(tmp_path, "[norm]\nvalu = 1"))


@pytest.mark.parametrize(
    "text",
    [
        'source = "other"',
        'enabled = "yes"',
        'final_class = "avg"',
        '[signal]\ncolumns = ["bogus"]',
        '[signal]\ncolumns = ["grad_left_on", "u_on"]',
        '[signal]\ncolumns = ["grad_left_on", "grad_left_off"]\ncombine = "single"',
        '[norm]\nmode = "ratio"\nvalue = -1',
        "[class_strength]\nclass_1 = 1\nclass_2 = 2",
        "[class_strength]\nclass_1 = 1\nclass_2 = 5\nclass_3 = 4",
        '[merge]\nmax_gap_m = "x"',
        "это не toml",
    ],
)
def test_invalid_values(tmp_path, text):
    with pytest.raises(CriteriaError):
        load_criteria(write_toml(tmp_path, text))


def test_missing_file(tmp_path):
    with pytest.raises(CriteriaError):
        load_criteria(tmp_path / "нет.toml")


def test_ratio_requires_background(tmp_path):
    with pytest.raises(CriteriaError, match="background.method"):
        criteria(tmp_path, norm='mode = "ratio"\nvalue = 2.0')


# --- расчёт: constant --------------------------------------------------------


def test_constant_mode(tmp_path):
    c = criteria(tmp_path)
    segs = calc_defects(grad_survey(SPIKES), c)
    assert [(s.start, s.end, s.length) for s in segs] == [
        (20.0, 26.0, 6.0),
        (60.0, 64.0, 4.0),  # пропуск на 62 участок не рвёт
        (90.0, 90.0, 0.0),
    ]
    assert [s.max_deviation for s in segs] == [5.0, 9.0, 4.0]
    # пороги: [3, 6) - 1, [6, 9) - 2, от 9 - 3
    assert [s.class_strength for s in segs] == [1, 3, 1]
    assert [s.class_final for s in segs] == [1, 3, 1]
    assert all(s.source == "calc" and s.row is None for s in segs)


def test_norm_boundary_is_strict(tmp_path):
    # значение ровно на границе нормы - ещё норма
    c = criteria(tmp_path)
    assert calc_defects(grad_survey({10.0: 3.0}), c) == []


def test_class_boundaries(tmp_path):
    c = criteria(tmp_path, merge="max_gap_m = 0.0\nmin_length_m = 0.0")
    s = grad_survey({10.0: 5.99, 20.0: 6.0, 30.0: 8.99, 40.0: 9.0, 50.0: 100.0})
    assert [x.class_strength for x in calc_defects(s, c)] == [1, 2, 2, 3, 3]


def test_gap_merging(tmp_path):
    values = {10.0: 4.0, 14.0: 4.0, 20.0: 4.0}
    # разрыв 4 - ровно max_gap: склеивается; разрыв 6 - нет
    segs = calc_defects(grad_survey(values), criteria(tmp_path))
    assert [(x.start, x.end) for x in segs] == [(10.0, 14.0), (20.0, 20.0)]
    big = criteria(tmp_path, merge="max_gap_m = 6.0\nmin_length_m = 0.0")
    assert [(x.start, x.end) for x in calc_defects(grad_survey(values), big)] == [(10.0, 20.0)]


def test_min_length_drops_short(tmp_path):
    c = criteria(tmp_path, merge="max_gap_m = 4.0\nmin_length_m = 4.0")
    segs = calc_defects(grad_survey(SPIKES), c)
    # 6 м и 4 м остаются (граница включена), точка 90 отброшена
    assert [(x.start, x.end) for x in segs] == [(20.0, 26.0), (60.0, 64.0)]


def test_none_only_points_ignored(tmp_path):
    c = criteria(tmp_path)
    segs = calc_defects(grad_survey({10.0: None, 12.0: None}), c)
    assert segs == []


def test_signal_combinations(tmp_path):
    dist = (0.0, 2.0, 4.0)
    s = Survey(
        "t",
        gradients=GradientBlock(dist=dist, left_on=(2.0, -5.0, None), right_on=(None, 1.0, 1.0)),
    )
    cols = '["grad_left_on", "grad_right_on"]'
    merge = "max_gap_m = 0.0\nmin_length_m = 0.0"
    mx = criteria(tmp_path, columns=cols, merge=merge)
    assert [x.max_deviation for x in calc_defects(s, mx)] == [5.0]  # модуль -5
    sm = criteria(tmp_path, columns=cols, combine="sum_abs", merge=merge)
    assert [(x.start, x.max_deviation) for x in calc_defects(s, sm)] == [(2.0, 6.0)]


def test_potential_signal_uses_intensive_dist(tmp_path):
    s = Survey(
        "t",
        intensive=IntensiveBlock(
            dist=(0.0, 1.0, 2.0), u_on=(-1.0, -4.0, -1.0), u_off=(None, None, None)
        ),
    )
    c = criteria(tmp_path, columns='["u_on"]', combine="single")
    segs = calc_defects(s, c)
    assert [(x.start, x.end, x.max_deviation) for x in segs] == [(1.0, 1.0, 4.0)]


# --- расчёт: ratio -----------------------------------------------------------


RATIO_NORM = 'mode = "ratio"\nvalue = 1.5'
RATIO_STRENGTH = "class_1 = 1.5\nclass_2 = 2.0\nclass_3 = 4.0"


def test_ratio_median(tmp_path):
    c = criteria(
        tmp_path,
        norm=RATIO_NORM,
        background='method = "median"',
        strength=RATIO_STRENGTH,
    )
    # фон = медиана = 1.0; кратности равны значениям
    segs = calc_defects(grad_survey({20.0: 1.6, 22.0: 2.0, 60.0: 4.0, 62.0: 8.0}), c)
    assert [(x.start, x.end, x.max_deviation, x.class_strength) for x in segs] == [
        (20.0, 22.0, 2.0, 2),
        (60.0, 62.0, 8.0, 3),
    ]


def test_ratio_rolling_median(tmp_path):
    # левая половина трассы - фон 1, правая - фон 10 (по всей трассе медиана
    # дала бы 1 или 10 и поймала бы лишнее)
    dist = tuple(float(d) for d in range(0, 101, 2))
    vals = [1.0 if d < 50 else 10.0 for d in dist]
    vals[dist.index(20.0)] = 3.0  # 3 раза от локального фона 1
    vals[dist.index(80.0)] = 15.0  # 1.5 раза от фона 10 - норма
    vals[dist.index(90.0)] = 50.0  # 5 раз от фона 10
    s = Survey("t", gradients=GradientBlock(dist=dist, left_on=tuple(vals)))
    local = criteria(
        tmp_path,
        norm=RATIO_NORM,
        background='method = "rolling_median"\nwindow_m = 20.0',
        strength=RATIO_STRENGTH,
    )
    segs = calc_defects(s, local)
    assert [(x.start, x.max_deviation, x.class_strength) for x in segs] == [
        (20.0, 3.0, 2),
        (90.0, 5.0, 3),
    ]
    glob = criteria(
        tmp_path, norm=RATIO_NORM, background='method = "median"', strength=RATIO_STRENGTH
    )
    starts = [x.start for x in calc_defects(s, glob)]
    assert starts != [20.0, 90.0]  # глобальный фон даёт другой результат


def test_ratio_constant_background(tmp_path):
    c = criteria(
        tmp_path,
        norm=RATIO_NORM,
        background='method = "constant"\nvalue = 2.0',
        strength=RATIO_STRENGTH,
    )
    segs = calc_defects(grad_survey({20.0: 8.0}), c)
    assert [(x.max_deviation, x.class_strength) for x in segs] == [(4.0, 3)]


# --- протяжённость и объединение классов -------------------------------------

LENGTH = "class_1 = 0.0\nclass_2 = 5.0\nclass_3 = 20.0"


def test_length_class_and_combine(tmp_path):
    # SPIKES: длины 6, 4, 0; классы по силе 1, 3, 1; по длине 2, 1, 1
    expected = {
        "max": [2, 3, 1],
        "min": [1, 1, 1],
        "strength": [1, 3, 1],
        "length": [2, 1, 1],
    }
    for mode, finals in expected.items():
        c = criteria(tmp_path, length=LENGTH, final=mode)
        segs = calc_defects(grad_survey(SPIKES), c)
        assert [x.class_length for x in segs] == [2, 1, 1]
        assert [x.class_final for x in segs] == finals, mode


def test_no_length_thresholds(tmp_path):
    segs = calc_defects(grad_survey(SPIKES), criteria(tmp_path))
    assert [x.class_length for x in segs] == [None, None, None]


def test_length_mode_requires_thresholds(tmp_path):
    with pytest.raises(CriteriaError, match="class_length"):
        criteria(tmp_path, final="length")


def test_changing_thresholds_changes_result(tmp_path):
    """Приёмка, п. 9: смена критериев в файле меняет результат без правки кода."""
    survey = grad_survey(SPIKES)
    first = calc_defects(survey, criteria(tmp_path))
    second = calc_defects(
        survey,
        criteria(
            tmp_path,
            norm='mode = "constant"\nvalue = 4.5',
            strength="class_1 = 4.5\nclass_2 = 5.0\nclass_3 = 8.0",
            merge="max_gap_m = 0.0\nmin_length_m = 0.0",
        ),
    )
    assert first != second
    assert [(x.start, x.class_final) for x in second] == [(22.0, 2), (60.0, 2), (64.0, 3)]


def test_calc_is_pure(tmp_path):
    survey = grad_survey(SPIKES)
    c = criteria(tmp_path)
    assert calc_defects(survey, c) == calc_defects(survey, c)


# --- ручные данные -----------------------------------------------------------


def test_manual_block():
    block = DefectsBlock(
        dist=(30.0, 10.0, 20.0, 40.0, 50.0),
        values=(3, 2.0, "класс 1", "крупный", "1,5"),
    )
    segs, issues = manual_defects(block)
    assert [(s.start, s.end, s.class_final) for s in segs] == [
        (10.0, 10.0, 2),
        (20.0, 20.0, 1),
        (30.0, 30.0, 3),
        (40.0, 40.0, None),
        (50.0, 50.0, None),
    ]
    assert all(s.source == "manual" and s.max_deviation is None for s in segs)
    assert [i.code for i in issues] == ["defect_class_unknown"] * 2
    assert all(i.level == "warning" for i in issues)
    assert "40" in issues[0].message


def test_manual_ambiguous_text():
    segs, issues = manual_defects(DefectsBlock(dist=(1.0,), values=("1 или 2",)))
    assert segs[0].class_final is None and len(issues) == 1


def test_pip_marks():
    marks = [
        (100.0, 130.0, "ПИП 2", 17),
        (50.0, None, "ПИП", 9),
        (70.0, None, "ПИП 3", None),
    ]
    segs, issues = manual_defects(DefectsBlock(), marks)
    assert issues == []  # подпись без класса - без предупреждения
    assert [(s.start, s.end, s.length, s.class_final, s.row) for s in segs] == [
        (50.0, 50.0, 0.0, None, 9),
        (70.0, 70.0, 0.0, 3, None),
        (100.0, 130.0, 30.0, 2, 17),
    ]


def test_collect_disabled_gives_manual_only(tmp_path):
    survey = grad_survey(SPIKES)
    survey = Survey(
        survey.sheet_name, gradients=survey.gradients, defects=DefectsBlock((5.0,), (2,))
    )
    base = Criteria(enabled=False, source="calc")  # source игнорируется
    segs, _ = collect_defects(survey, base, [(8.0, 9.0, "ПИП 1", 4)])
    assert [(s.source, s.start) for s in segs] == [("manual", 5.0), ("manual", 8.0)]
    shipped, _ = collect_defects(survey, load_criteria())
    assert [s.source for s in shipped] == ["manual"]


def test_collect_sources(tmp_path):
    base = grad_survey(SPIKES)
    survey = Survey("t", gradients=base.gradients, defects=DefectsBlock((5.0,), (2,)))
    calc, _ = collect_defects(survey, criteria(tmp_path, source="calc"))
    assert {s.source for s in calc} == {"calc"}
    manual, _ = collect_defects(survey, criteria(tmp_path, source="manual"))
    assert {s.source for s in manual} == {"manual"}
    both, _ = collect_defects(survey, criteria(tmp_path, source="both"))
    assert [s.start for s in both] == sorted(s.start for s in both)
    assert {s.source for s in both} == {"calc", "manual"}


# --- полоса «Площадь дефекта» ------------------------------------------------


def seg(start: float, end: float, cls: int | None) -> DefectSegment:
    return DefectSegment(start, end, end - start, None, None, None, cls, "manual")


def test_strip_steps_basic_and_clip():
    segs = [seg(-10, 5, 1), seg(20, 30, 2), seg(90, 120, 3)]
    assert strip_steps(segs, 0, 100) == [(0, 5, 1), (20, 30, 2), (90, 100, 3)]


def test_strip_steps_overlap_takes_larger():
    segs = [seg(0, 20, 1), seg(10, 15, 3), seg(15, 30, 2)]
    assert strip_steps(segs, 0, 100) == [(0, 10, 1), (10, 15, 3), (15, 30, 2)]


def test_strip_steps_merge_same_class_and_none():
    segs = [seg(0, 10, 2), seg(10, 20, 2), seg(30, 40, None), seg(5, 5, 3), seg(200, 300, 1)]
    assert strip_steps(segs, 0, 100) == [(0, 20, 2)]
    assert strip_steps([], 0, 100) == []
