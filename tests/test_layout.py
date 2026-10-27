"""Этап 5: разбиение на листы и размещение значков."""

from __future__ import annotations

import time
import tomllib

import pytest

from cormap import assets, cli
from cormap.check import check_and_split
from cormap.layout import (
    LayoutError,
    Section,
    format_km,
    nice_step,
    parse_split_spec,
    place_objects,
    split_auto,
    split_manual,
    tick_positions,
    zone_extents_mm,
)
from cormap.layout_settings import (
    LAYOUT_TOML,
    LayoutSettingsError,
    load_settings,
    parse_settings,
    plot_width_mm,
)
from cormap.notes import TrackObject
from tests.synth import make_workbook

S = load_settings()
_ICONS = assets._settings()


def obj(dist, key="kip", label=None, end=None, voltage=None, row=2):
    text = label if label is not None else f"{key.upper()}-{int(dist)}"
    return TrackObject(key, text, dist, end, row, None, voltage)


def one_sheet(length_m, objects, start=0.0, paper="A4"):
    """Один участок [start, start + length]: удобная геометрия для проверки размещения."""
    res = split_manual(start, start + length_m, float(length_m), S, objects, paper)
    assert len(res.sections) == 1
    return res.sections[0]


def covers(result, start, end):
    secs = result.sections
    assert secs[0].start_m == start and secs[-1].end_m == end
    for a, b in zip(secs, secs[1:], strict=False):
        assert a.end_m == b.start_m
    assert [s.index for s in secs] == list(range(1, len(secs) + 1))


# --- настройки ---------------------------------------------------------------


def test_settings_values():
    assert S.plot_width_mm("A4") == 224.0
    assert plot_width_mm("a3") == 420 - 20 - 35 - 12 - 6
    assert (S.margin_mm, S.label_column_mm, S.plot_pad_left_mm, S.plot_pad_right_mm) == (
        10,
        35,
        12,
        6,
    )
    assert S.lengths_km == (0.5, 1, 2, 5, 10, 20, 50, 100)
    assert S.tick_steps_km == (0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10)
    assert (S.auto_max_length_km, S.auto_min_length_km, S.tiers, S.max_sections) == (
        100,
        0.5,
        2,
        300,
    )
    assert (S.font_pt, S.gap_mm, S.callout_levels, S.callout_step_mm) == (7, 0.5, 3, 3)
    assert S.min_extended_width_mm == 1.5
    assert S.min_cross_band_width_mm == 3.5


def test_unknown_paper():
    with pytest.raises(LayoutError, match="A5"):
        S.paper("A5")


def _data():
    with LAYOUT_TOML.open("rb") as fh:
        return tomllib.load(fh)


def test_settings_strict():
    parse_settings(_data())  # исходный файл корректен
    d = _data()
    d["labels"]["lишний"] = 1
    with pytest.raises(LayoutSettingsError):
        parse_settings(d)
    d = _data()
    del d["page"]["margin_mm"]
    with pytest.raises(LayoutSettingsError):
        parse_settings(d)
    d = _data()
    d["sheet"]["tick_steps_km"] = [0.05]
    with pytest.raises(LayoutSettingsError):
        parse_settings(d)
    d = _data()
    d["auto"]["max_length_km"] = 70.0  # нет в ряду
    with pytest.raises(LayoutSettingsError):
        parse_settings(d)
    d = _data()
    d["labels"]["font_pt"] = "семь"
    with pytest.raises(LayoutSettingsError):
        parse_settings(d)
    d = _data()
    d["extra"] = {}
    with pytest.raises(LayoutSettingsError):
        parse_settings(d)


# --- форматы, деления ---------------------------------------------------------


def test_format_km():
    assert format_km(12.5) == "12,50"
    assert format_km(0) == "0,00"
    assert format_km(-0.001) == "0,00"
    assert format_km(1234.567) == "1234,57"


@pytest.mark.parametrize(("km", "step"), list(zip(S.lengths_km, S.tick_steps_km, strict=True)))
def test_tick_step_by_series(km, step):
    """Шаг делений по таблице раздела 6 ТЗ для каждой длины ряда."""
    d = _data()
    d["auto"]["max_length_km"] = km
    d["auto"]["min_length_km"] = km
    cfg = parse_settings(d)
    res = split_auto([], 0, km * 500, cfg)  # половина листа, пусто
    (sec,) = res.sections
    assert sec.nominal_length_m == km * 1000
    assert sec.tick_step_m == pytest.approx(step * 1000)
    assert round(km / step) == 10  # на листе десять делений


def test_nice_step():
    assert nice_step(1000) == 100
    assert nice_step(3000) == 500
    assert nice_step(1200) == 100
    assert nice_step(1300) == 200
    assert nice_step(2500) == 500
    for length in (50, 777, 1000, 4321, 100000, 250000):
        n = length / nice_step(length)
        assert 4.8 <= n <= 12.001


def test_tick_positions():
    sec = Section(1, 1000, 3000, 2000, 200, 0, False, 224.0)
    ticks = tick_positions(sec)
    assert ticks[0] == 1000 and ticks[-1] == 3000 and len(ticks) == 11
    assert all(abs(b - a - 200) < 1e-9 for a, b in zip(ticks, ticks[1:], strict=False))
    # участок не кратен шагу: деления только в пределах участка
    sec = Section(1, 0, 1050, 2000, 200, 0, False, 224.0)
    assert tick_positions(sec)[-1] == 1000
    assert sec.length_m == 1050


def test_section_x_mm():
    sec = Section(1, 1000, 2000, 1000, 100, 0, False, 224.0)
    assert sec.x_mm(1000) == 0
    assert sec.x_mm(1500) == pytest.approx(112)
    # последний участок короче номинала: ось всё равно охватывает номинал
    short = Section(2, 0, 250, 1000, 100, 0, False, 224.0)
    assert short.x_mm(250) == pytest.approx(56)


# --- разбор строки формы и ручной режим ----------------------------------------


def test_parse_split_spec():
    assert parse_split_spec("5000") == 5000
    assert parse_split_spec("0; 3000; 5000; 6000; 15000") == (0, 3000, 5000, 6000, 15000)
    assert parse_split_spec("2500,5") == 2500.5
    assert parse_split_spec("0; 1500,5; 3000;") == (0, 1500.5, 3000)
    for bad in ("", " ; ", "abc", "0; x; 5"):
        with pytest.raises(LayoutError):
            parse_split_spec(bad)


def test_manual_single_length():
    res = split_manual(0, 12000, 5000, S)
    assert res.mode == "manual"
    assert [(s.start_m, s.end_m) for s in res.sections] == [
        (0, 5000),
        (5000, 10000),
        (10000, 12000),
    ]
    assert all(s.nominal_length_m == 5000 for s in res.sections)
    assert res.sections[0].tick_step_m == 500
    covers(res, 0, 12000)
    # строка формы тоже принимается
    assert split_manual(0, 12000, "5000", S).sections == res.sections


def test_manual_boundaries_exact():
    res = split_manual(0, 15000, parse_split_spec("0; 3000; 5000; 6000; 15000"), S)
    assert [(s.start_m, s.end_m) for s in res.sections] == [
        (0, 3000),
        (3000, 5000),
        (5000, 6000),
        (6000, 15000),
    ]
    assert [s.nominal_length_m for s in res.sections] == [3000, 2000, 1000, 9000]
    assert not res.issues


def test_manual_boundaries_not_covering_route_warns():
    res = split_manual(0, 15000, (1000, 5000), S)
    assert res.sections[0].start_m == 1000 and res.sections[0].end_m == 5000
    assert [i.code for i in res.issues] == ["layout_not_covered"]


def test_manual_errors():
    cases = [
        (0, 1000, (0, 500, 500)),  # не возрастают
        (0, 1000, (0, 800, 500)),
        (0, 1000, (500,)),  # меньше двух границ
        (0, 1000, ()),
        (0, 1000, 0),  # длина <= 0
        (0, 1000, -5),
        (0, 1000, float("nan")),
        (0, 400_000, 1000),  # 400 участков > 300
        (0, 1000, tuple(range(0, 400))),
    ]
    for start, end, spec in cases:
        with pytest.raises(LayoutError):
            split_manual(start, end, spec, S)
    with pytest.raises(LayoutError, match="Увеличьте длину листа"):
        split_manual(0, 400_000, 1000, S)


def test_manual_counts_and_overlaps():
    objs = [obj(100), obj(200), obj(5500)]
    res = split_manual(0, 10000, 5000, S, objs)
    assert [s.n_objects for s in res.sections] == [2, 1]
    assert not res.has_overlaps
    res = split_manual(0, 100, 100, S, [obj(i * 0.5) for i in range(40)])
    assert res.sections[0].has_overlaps
    assert [i.code for i in res.issues] == ["layout_overlap"]


# --- автоматический режим: сценарии приёмки -------------------------------------


def test_auto_100km_five_objects_one_sheet():
    objs = [obj(d) for d in (5_000, 20_000, 48_000, 70_000, 99_000)]
    res = split_auto(objs, 0, 100_000, S)
    assert res.mode == "auto"
    assert [(s.start_m, s.end_m, s.nominal_length_m) for s in res.sections] == [
        (0, 100_000, 100_000)
    ]
    assert res.sections[0].n_objects == 5 and not res.has_overlaps
    assert not res.issues


def test_auto_30_kip_on_one_km_gets_large_scale_sheet():
    objs = [obj(50_000 + i * 33) for i in range(30)]
    res = split_auto(objs, 0, 100_000, S)
    covers(res, 0, 100_000)
    assert res.sections[-1].nominal_length_m == 50_000  # хвост 51-100 км
    dense = [s for s in res.sections if s.n_objects == 30]
    assert len(dense) == 1
    assert dense[0].nominal_length_m <= 2_000
    assert not any(s.has_overlaps for s in res.sections)
    empties = [s for s in res.sections if s.n_objects == 0]
    assert empties and all(s.nominal_length_m >= 20_000 for s in empties)
    assert not res.issues


def test_auto_30_kip_unaligned_cluster():
    objs = [obj(48_900 + i * 33) for i in range(30)]
    res = split_auto(objs, 0, 100_000, S)
    covers(res, 0, 100_000)
    assert not any(s.has_overlaps for s in res.sections)
    assert sum(s.n_objects for s in res.sections) >= 30


def test_auto_empty_300km_three_sheets():
    res = split_auto([], 0, 300_000, S)
    assert [(s.start_m, s.end_m, s.nominal_length_m) for s in res.sections] == [
        (0, 100_000, 100_000),
        (100_000, 200_000, 100_000),
        (200_000, 300_000, 100_000),
    ]
    assert [s.tick_step_m for s in res.sections] == [10_000] * 3


def test_auto_250km_last_sheet_nominal_fits_remainder():
    """Последний участок: номинал - наименьшая длина из ряда, не меньше остатка."""
    res = split_auto([], 0, 250_000, S)
    assert [(s.length_m, s.nominal_length_m) for s in res.sections] == [
        (100_000, 100_000),
        (100_000, 100_000),
        (50_000, 50_000),
    ]
    assert res.sections[-1].tick_step_m == 5_000
    covers(res, 0, 250_000)


def test_auto_short_tail_nominal():
    for tail, nominal, tick in ((49_000, 50_000, 5_000), (300, 500, 50), (1_500, 2_000, 200)):
        res = split_auto([], 0, 100_000 + tail, S)
        last = res.sections[-1]
        assert (last.length_m, last.nominal_length_m, last.tick_step_m) == (tail, nominal, tick)
        assert res.sections[0].nominal_length_m == 100_000


def test_auto_short_tail_recomputes_overlaps():
    objs = [obj(100_000 + i * 2.5) for i in range(40)]  # плотный хвост 100 м
    res = split_auto(objs, 0, 100_100, S)
    assert res.sections[-1].nominal_length_m == 500
    assert res.sections[-1].has_overlaps == res.has_overlaps


def test_auto_nonzero_start_and_short_route():
    res = split_auto([obj(1500)], 1000, 1800, S)
    (sec,) = res.sections
    assert (sec.start_m, sec.end_m, sec.n_objects) == (1000, 1800, 1)


def test_auto_point_at_end_belongs_to_next_sheet_last_one_is_closed():
    objs = [obj(100_000), obj(200_000), obj(300_000)]
    res = split_auto(objs, 0, 300_000, S)
    assert [s.n_objects for s in res.sections] == [0, 1, 2]


def test_auto_unresolvable_overlap_at_min_scale():
    objs = [obj(i * 2.5) for i in range(40)]
    res = split_auto(objs, 0, 100, S)
    (sec,) = res.sections
    assert sec.has_overlaps and sec.nominal_length_m == 500
    assert [i.code for i in res.issues] == ["layout_overlap"]
    assert all(i.level == "warning" for i in res.issues)


def test_auto_section_limit_dense_300km():
    objs = [obj(i * 20.0) for i in range(15_001)]
    t0 = time.perf_counter()
    res = split_auto(objs, 0, 300_000, S)
    elapsed = time.perf_counter() - t0
    assert len(res.sections) <= S.max_sections
    covers(res, 0, 300_000)
    codes = [i.code for i in res.issues]
    assert "layout_min_scale_raised" in codes and "layout_overlap" in codes
    assert min(s.nominal_length_m for s in res.sections) >= 1000
    assert elapsed < 10


def test_auto_3000_objects_fast():
    objs = [obj(i * 100.0) for i in range(3000)]
    t0 = time.perf_counter()
    res = split_auto(objs, 0, 300_000, S)
    assert time.perf_counter() - t0 < 5
    covers(res, 0, 300_000)
    assert sum(s.n_objects for s in res.sections) == 3000


def test_auto_a3_fits_more_than_a4():
    objs = [obj(i * 90.0) for i in range(60)]
    a4 = split_auto(objs, 0, 5400, S, "A4")
    a3 = split_auto(objs, 0, 5400, S, "A3")
    assert len(a3.sections) <= len(a4.sections)
    assert a3.paper == "A3"
    with pytest.raises(LayoutError):
        split_auto(objs, 0, 5400, S, "A5")


# --- размещение -------------------------------------------------------------------


def test_x_mm_matches_scale():
    objs = [obj(250), obj(1000.0 * 0.123)]
    sec = one_sheet(1000, objs)
    pl = place_objects(objs, sec, S)
    for p in pl.objects:
        assert p.x_mm == pytest.approx(sec.x_mm(p.obj.start), abs=0.01)
    by_dist = {p.obj.start: p for p in pl.objects}
    assert by_dist[250].x_mm == pytest.approx(250 / 1000 * 224, abs=0.01)


def test_icon_rect_uses_size_and_anchor():
    sec = one_sheet(224, [obj(100, label="")])
    p = place_objects([obj(100, label="")], sec, S).objects[0]
    r = p.icon_rect  # КИП: 4 x 12, привязка низ-центр
    assert (r.width, r.height) == (4.0, 12.0)
    assert r.x0 == pytest.approx(98) and r.y0 == 0 and p.label is None and p.label_rect is None
    crane = place_objects([obj(100, "crane", "")], sec, S).objects[0]
    assert crane.icon_rect.y0 == pytest.approx(-2) and crane.icon_rect.y1 == pytest.approx(2)
    pit = place_objects([obj(100, "pit", "")], sec, S).objects[0]
    assert pit.icon_rect.y1 == 0 and pit.icon_rect.y0 == pytest.approx(-4)


def test_close_icons_go_to_different_tiers():
    objs = [obj(100), obj(102)]  # 2 мм друг от друга при масштабе 1 мм/м
    sec = one_sheet(224, objs)
    pl = place_objects(objs, sec, S)
    assert [p.tier for p in pl.objects] == [0, 1]
    assert not pl.has_overlaps
    assert pl.objects[1].icon_rect.y0 > pl.objects[0].icon_rect.y1


def test_three_in_one_point_with_two_tiers_overlap():
    objs = [obj(100, label=""), obj(100, "ukz", ""), obj(100, "udz", "")]
    sec = one_sheet(224, objs)
    pl = place_objects(objs, sec, S)
    assert [p.overlap for p in pl.objects] == [False, False, True]
    assert pl.has_overlaps
    assert one_sheet(224, objs).has_overlaps


def test_same_cell_kip_and_ukz_is_not_overlap():
    objs = [obj(100, "ukz", "УКЗ-3"), obj(100, "kip", "КИП")]
    pl = place_objects(objs, one_sheet(224, objs), S)
    assert not pl.has_overlaps
    assert sorted(p.tier for p in pl.objects) == [0, 1]


def test_labels_rise_on_callout():
    objs = [obj(100, "ukz", "УКЗ-1"), obj(103, "ukz", "УКЗ-2"), obj(106, "ukz", "УКЗ-3")]
    pl = place_objects(objs, one_sheet(224, objs), S)
    assert [p.tier for p in pl.objects] == [0, 1, 0]
    assert pl.objects[0].callout_level == 0
    assert pl.objects[2].callout_level >= 1
    assert not pl.has_overlaps
    low, high = pl.objects[0].label_rect, pl.objects[2].label_rect
    assert high.y0 > low.y1  # подпись поднята над соседней
    assert low.y0 > pl.objects[0].icon_rect.y1  # подпись над значком


def test_label_without_free_level_is_overlap():
    objs = [obj(100 + i * 0.5, "ukz", "УКЗ-длинная-подпись") for i in range(2)]
    objs += [obj(100.2, "ukz", "ещё одна длинная подпись")]
    pl = place_objects(objs, one_sheet(224, objs), S)
    assert pl.has_overlaps


def test_below_label_is_under_icon():
    objs = [obj(100, "pit", "Ш-1")]
    p = place_objects(objs, one_sheet(224, objs), S).objects[0]
    assert p.label_rect.y1 < p.icon_rect.y0


def test_vl_label_is_voltage_and_cross_line_strip():
    objs = [obj(100, "vl", "ЛЭП", voltage="110 кВ")]
    p = place_objects(objs, one_sheet(224, objs), S).objects[0]
    assert p.label == "110 кВ"
    assert p.icon_rect.y0 < 0 < p.icon_rect.y1  # линия пересекает трубу
    assert p.label_rect.y1 < p.icon_rect.y0  # метка под трубой


def test_cross_line_pushes_kip_to_upper_tier():
    objs = [obj(100, "vl", "", voltage="110 кВ"), obj(101, "kip", "")]
    pl = place_objects(objs, one_sheet(224, objs), S)
    assert pl.objects[1].tier == 1 and not pl.has_overlaps


def test_extended_object_clipped_on_both_sections():
    river = obj(400, "water", "р. Упа", end=1600)
    res = split_manual(0, 2000, 1000, S, [river])
    assert [s.n_objects for s in res.sections] == [1, 1]
    p1 = place_objects([river], res.sections[0], S).objects[0]
    p2 = place_objects([river], res.sections[1], S).objects[0]
    assert (p1.start_clipped, p1.end_clipped) == (False, True)
    assert (p2.start_clipped, p2.end_clipped) == (True, False)
    assert p1.x_mm == pytest.approx(0.4 * 224, abs=0.01)
    assert p1.x_end_mm == pytest.approx(224, abs=0.01)  # обрезано по границе 1000
    assert p2.x_mm == 0 and p2.x_end_mm == pytest.approx(0.6 * 224, abs=0.01)
    assert p1.band_rect.y1 == 0 and p1.band_rect.y0 < 0  # полоса под трубой
    assert p1.band_rect.width == pytest.approx(p1.x_end_mm - p1.x_mm, abs=0.01)


def test_extended_object_spanning_whole_section():
    river = obj(-500, "water", "", end=5000)
    res = split_manual(0, 3000, 1000, S, [river])
    assert [s.n_objects for s in res.sections] == [1, 1, 1]


def test_narrow_extended_object_gets_minimal_width():
    river = obj(100, "water", "", end=100.5)  # 0,22 мм на бумаге
    sec = one_sheet(224, [river])
    p = place_objects([river], sec, S).objects[0]
    assert p.band_rect.width == pytest.approx(assets.min_width_mm("water"))
    assert (p.band_rect.x0 + p.band_rect.x1) / 2 == pytest.approx(100.25, abs=0.01)  # центр участка
    road = obj(150, "road", "")  # точечная дорога: ширина значка
    q = place_objects([road], one_sheet(224, [road]), S).objects[0]
    assert q.band_rect.width == pytest.approx(5.0)
    # короткая дорога (20 м на листе 11,2 км = 0,4 мм на бумаге) - не уже min_cross_band_width_mm
    short_road = obj(100, "road", "", end=120)
    r = place_objects([short_road], one_sheet(11_200, [short_road]), S).objects[0]
    assert r.band_rect.width == pytest.approx(S.min_cross_band_width_mm)
    assert r.band_rect.width > S.min_extended_width_mm


def test_min_width_values_by_type():
    expected = {
        "water": 4.0,
        "ravine": 4.0,
        "open_area": 4.0,
        "air_crossing": 5.0,
        "swamp": assets.icon_size_mm("swamp")[0],  # ширина одного значка
        "forest": assets.icon_size_mm("forest")[0],
    }
    for key, mm in expected.items():
        assert assets.min_width_mm(key) == mm
        short = obj(100, key, "", end=100.5)
        p = place_objects([short], one_sheet(224, [short]), S).objects[0]
        assert p.band_rect.width == pytest.approx(mm)
    assert assets.min_width_mm("road") is None  # общий минимум


def test_type_without_own_min_uses_common_minimum(monkeypatch):
    monkeypatch.setattr(
        assets,
        "_settings",
        lambda: {
            k: {kk: vv for kk, vv in v.items() if kk != "min_width_mm"} for k, v in _ICONS.items()
        },
    )
    short = obj(100, "water", "", end=100.5)
    p = place_objects([short], one_sheet(224, [short]), S).objects[0]
    assert p.band_rect.width == pytest.approx(S.min_extended_width_mm)


@pytest.mark.parametrize("bad", [0, -1.0, "4", True, float("nan")])
def test_bad_min_width_is_error(monkeypatch, bad):
    data = {k: dict(v) for k, v in _ICONS.items()}
    data["water"]["min_width_mm"] = bad
    monkeypatch.setattr(assets, "_settings", lambda: data)
    with pytest.raises(ValueError, match="water.*min_width_mm"):
        assets.min_width_mm("water")


def test_narrow_band_near_sheet_edge_shifts_inside():
    w = assets.min_width_mm("water")
    left = obj(0.5, "water", "", end=1.0)
    p = place_objects([left], one_sheet(224, [left]), S).objects[0]
    assert p.band_rect.x0 == pytest.approx(0.0) and p.band_rect.width == pytest.approx(w)
    right = obj(223.0, "water", "", end=223.5)
    p = place_objects([right], one_sheet(224, [right]), S).objects[0]
    assert p.band_rect.x1 == pytest.approx(224.0) and p.band_rect.width == pytest.approx(w)


def test_wide_extended_band_keeps_actual_width():
    river = obj(50, "water", "", end=150)
    p = place_objects([river], one_sheet(224, [river]), S).objects[0]
    assert p.band_rect.width == pytest.approx(100.0)


def test_point_extended_type_gets_min_width_centered_on_point():
    for key in ("water", "air_crossing", "open_area"):
        o = obj(100, key, "")
        p = place_objects([o], one_sheet(224, [o]), S).objects[0]
        assert p.band_rect.width == pytest.approx(assets.min_width_mm(key))
        assert (p.band_rect.x0 + p.band_rect.x1) / 2 == pytest.approx(100.0)


def test_min_width_counts_in_overlap_check():
    # участки 0,2 мм каждый, центры в 3 мм друг от друга: при ширине 4 мм полосы налезают
    a, b = obj(100, "water", "", end=100.2), obj(103, "water", "", end=103.2)
    assert place_objects([a, b], one_sheet(224, [a, b]), S).has_overlaps
    far = [a, obj(110, "water", "", end=110.2)]
    assert not place_objects(far, one_sheet(224, far), S).has_overlaps


def test_overlapping_bands_are_overlap_but_do_not_block_icons():
    bands = [obj(100, "water", "", end=150), obj(120, "swamp", "", end=180)]
    assert place_objects(bands, one_sheet(224, bands), S).has_overlaps
    mixed = [obj(100, "water", "", end=150), obj(120, "kip", "")]
    assert not place_objects(mixed, one_sheet(224, mixed), S).has_overlaps


def test_zone_extents_and_placement_extents():
    above, below = zone_extents_mm(S)
    assert above > 12 and below > 11
    objs = [obj(100, "kip", "КИП-1"), obj(101, "kip", "КИП-2")]
    pl = place_objects(objs, one_sheet(224, objs), S)
    assert 0 < pl.above_extent_mm <= above


def test_point_exactly_on_section_boundary_goes_to_next():
    res = split_manual(0, 2000, 1000, S, [obj(1000)])
    assert [s.n_objects for s in res.sections] == [0, 1]
    last = split_manual(0, 1000, 1000, S, [obj(1000)])
    assert last.sections[0].n_objects == 1  # конец трассы - последний участок замкнут


# --- таблица ----------------------------------------------------------------------


def test_table_with_start_km():
    res = split_manual(0, 12000, 5000, S, [obj(100)], start_km=10.5)
    assert res.table() == [
        ("1", "10,50", "15,50", "5,00", "1", "нет"),
        ("2", "15,50", "20,50", "5,00", "0", "нет"),
        ("3", "20,50", "22,50", "2,00", "0", "нет"),
    ]
    assert res.table(start_km=0)[0][1:3] == ("0,00", "5,00")
    text = res.format_text()
    assert "Лист" in text and "10,50" in text and "ручное" in text


# --- сквозные тесты и CLI ----------------------------------------------------------


def test_check_and_split_end_to_end(tmp_path):
    notes = [
        (1000, "КИП, КИП-1"),
        (1000, "УКЗ, УКЗ-1"),
        (4000, "река начало, р. Упа"),
        (4400, "река конец"),
    ]
    path = make_workbook(tmp_path / "b.xlsx", length_m=10_000, step_m=2.0, notes=notes)
    survey, report, notes_res, split = check_and_split(path, split="auto")
    assert report.ok and split is not None
    assert [(s.start_m, s.end_m) for s in split.sections] == [(0, 10_000)]
    # 3 объекта из комментариев + 7 ручных повреждений из блока «Повреждения» (они тоже объекты)
    assert split.sections[0].n_objects == 3 + 7
    _, report, _, split = check_and_split(path, split="0;3000;10000", start_km=2)
    # повреждения на 0, 1429, 2857 м - в первом участке; на 4286...8571 м (4 шт.) - во втором
    assert [s.n_objects for s in split.sections] == [2 + 3, 1 + 4]
    assert split.table()[0][1] == "2,00"
    # ошибка параметров разбиения - блокирующая ошибка отчёта
    _, report, _, split = check_and_split(path, split="5000;3000")
    assert split is None and not report.ok
    assert report.errors[-1].code == "split_error"


def test_cli_check_prints_split_table(tmp_path, capsys):
    notes = [(500, "КИП, КИП-1"), (7000, "кран")]
    path = make_workbook(tmp_path / "b.xlsx", length_m=10_000, step_m=2.0, notes=notes)
    code = cli.main(["check", str(path), "--split", "0;3000;5000;10000", "--start-km", "1,5"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Разбиение на листы (ручное, A4, участков: 3)" in out
    assert "1,50" in out and "4,50" in out and "11,50" in out
    code = cli.main(["check", str(path), "--paper", "a3"])
    out = capsys.readouterr().out
    assert code == 0 and "автоматическое, A3" in out
    code = cli.main(["check", str(path), "--split", "abc"])
    out = capsys.readouterr().out
    assert code == 1 and "Не число" in out


# --- значки «на трубе», неустранимые наложения, сообщения о наложениях -------------


def test_on_pipe_icon_over_above_icon_is_not_overlap():
    """Кран на основании КИП (как в образце на 0,00 км) - нормальный вид."""
    objs = [obj(100, "kip", "КИП-1"), obj(100, "crane", "")]
    pl = place_objects(objs, one_sheet(224, objs), S)
    assert not pl.has_overlaps
    objs = [obj(100, "crane", ""), obj(100, "kip", "КИП-1")]
    assert not place_objects(objs, one_sheet(224, objs), S).has_overlaps


def test_on_pipe_icons_still_overlap_each_other_and_cross_line():
    objs = [obj(100, "crane", ""), obj(100, "vei", "")]
    pl = place_objects(objs, one_sheet(224, objs), S)
    assert [p.overlap for p in pl.objects] == [False, True]
    assert pl.objects[1].conflict_with is pl.objects[0].obj
    objs = [obj(100, "crane", ""), TrackObject("vl", "", 100, None, 3, None, "110 кВ")]
    assert place_objects(objs, one_sheet(224, objs), S).has_overlaps


def test_auto_unavoidable_overlap_does_not_force_min_scale():
    """Три значка в одной точке (третий неустранимо) не нарезают трассу по 0,5 км."""
    objs = [
        obj(500),
        obj(2500, "kip", "КИП-А"),
        obj(2500, "ukz", "УКЗ-Б"),
        obj(2500, "udz", "УДЗ-В"),
        obj(5200),
    ]
    res = split_auto(objs, 0, 5500, S, start_km=12.0)
    assert len(res.sections) <= 2
    assert res.sections[0].nominal_length_m >= 5000
    assert res.sections[0].has_overlaps
    (issue,) = res.issues
    assert issue.code == "layout_overlap"
    assert "лист 1:" in issue.message and "на км 14,50" in issue.message
    assert "УДЗ" in issue.message or "Устройство" in issue.message
    assert res.sections[0].conflicts and res.sections[0].conflicts[0][2] == 2500


def test_overlap_message_names_objects_limited_to_five():
    objs = [obj(i * 100.0 + j, "kip", f"К{i}-{j}") for i in range(10) for j in range(3)]
    objs = [o for o in objs for _ in (0, 1, 2)]  # по три значка в точке
    res = split_manual(0, 1000, 1000, S, objs, start_km=12.5)
    (issue,) = res.issues
    assert issue.message.count("на км") <= 5
    assert "лист 1:" in issue.message and " и " in issue.message


def test_dense_cluster_still_gets_large_scale_with_unavoidable_elsewhere():
    """Правило раздела 6 ТЗ: плотный участок получает крупный масштаб, кроме неустранимого."""
    objs = [obj(50_000 + i * 33) for i in range(30)]
    objs += [obj(10_000), obj(10_000, "ukz", "УКЗ"), obj(10_000, "udz", "УДЗ")]
    res = split_auto(objs, 0, 100_000, S)
    dense = [s for s in res.sections if s.n_objects == 30]
    assert len(dense) == 1 and dense[0].nominal_length_m <= 2_000
    assert not dense[0].has_overlaps
