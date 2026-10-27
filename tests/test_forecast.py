"""Коррозионный прогноз: ядро (ТЗ, 9.1)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from cormap.forecast import (
    Coupon,
    ForecastError,
    ForecastInput,
    Pitting,
    PreviousSurvey,
    build_protocol,
    calc_forecast,
    num,
)

BASE = ForecastInput(
    name="МГ Тест",
    year_start=2005,
    year_survey=2026,
    diameter=1020,
    nominal=12,
    method="pressure",
    dn=1000,
    pressure=1.2,
    n=1.1,
    r1n=510,
    m=0.99,
    k1=1.4,
    kn=1.1,
    addition=0,
    current=10.5,
    depth=1.5,
    depth_allow=4,
    horizon=5,
    direct_rate=0.05,
    previous=PreviousSurvey(2021, 10.9, 1.1),
    coupon=Coupon(0, 100, 8760, 7.85),
    pitting=Pitting(4, 1.2, 216),
)


def test_control_example():
    r = calc_forecast(BASE)
    assert r.r1 == pytest.approx(327.857, abs=1e-3)
    assert r.delta_p == pytest.approx(2.0451, abs=1e-4)
    assert r.allow == pytest.approx(r.delta_p)
    assert r.v_service == pytest.approx(0.0714286, abs=1e-7)
    assert r.v_between == pytest.approx(0.08)
    assert r.v_depth == pytest.approx(0.08)
    assert r.v_mass is None
    assert r.v_accepted == pytest.approx(0.08)
    assert r.life_thk == pytest.approx(105.69, abs=0.01)
    assert r.life_depth == pytest.approx(31.25)
    assert r.h_crit == pytest.approx(11.8499, abs=1e-4)
    assert r.v_pit == pytest.approx(0.130952, abs=1e-6)
    assert r.life_pit == pytest.approx(79.04, abs=0.01)
    assert r.life_min == pytest.approx(31.25)
    assert r.limit_year == pytest.approx(2057.25)
    assert r.forecast_thk == pytest.approx(10.1)
    assert r.forecast_depth == pytest.approx(1.9)


def test_sp36_uses_constructive_minimum():
    r = calc_forecast(replace(BASE, method="sp36", addition=0.5, nominal=14))
    assert r.allow == pytest.approx(12.5)  # DN 1000 -> конструктивный минимум 12
    assert r.life_thk == 0.0  # δф 10,5 < δдоп
    assert r.margin < 0
    r = calc_forecast(replace(BASE, method="sp36", dn=150))
    assert r.construct_floor == 3 and r.dn_floor == 1.5
    r = calc_forecast(replace(BASE, method="sp36", dn=500))
    assert r.construct_floor == 4


def test_manual_allow():
    data = replace(BASE, method="manual", manual_allow=5.0, dn=None, r1n=None)
    r = calc_forecast(data)
    assert r.allow == 5.0 and r.r1 is None and r.delta_p is None
    assert r.life_thk == pytest.approx((10.5 - 5.0) / 0.08)
    text = build_protocol(data, r, "дата")
    assert "принята вручную: 5 мм" in text
    assert "R1 = " not in text


def test_manual_without_value():
    with pytest.raises(ForecastError, match="δдоп"):
        calc_forecast(replace(BASE, method="manual", manual_allow=None))


def test_optional_blocks_absent():
    data = replace(BASE, previous=None, coupon=None, pitting=None, direct_rate=None)
    r = calc_forecast(data)
    assert r.v_between is None and r.v_depth is None and r.v_mass is None
    assert r.h_crit is None and r.life_pit is None
    assert r.v_accepted == pytest.approx(r.v_service)
    text = build_protocol(data, r, "дата")
    assert "Язвенная коррозия" not in text
    assert "не рассчитано" in text


def test_coupon_rate():
    r = calc_forecast(replace(BASE, coupon=Coupon(10, 100, 8760, 7.85)))
    assert r.v_mass == pytest.approx(8.76e4 * 10 / (100 * 8760 * 7.85))


def test_pit_rate_uses_accepted_when_no_service_rate():
    r = calc_forecast(replace(BASE, current=12, previous=None, direct_rate=0.1))
    assert r.v_service == 0 and r.v_accepted == 0.1
    assert r.v_pit == pytest.approx((0.1 + 4 / 21) / 2)


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"year_survey": 2005}, "позже года ввода"),
        ({"current": 12.5}, "не может превышать номинальную"),
        ({"method": "manual", "manual_allow": 13.0}, "не может превышать номинальную"),
        ({"method": "manual", "manual_allow": 600.0}, "радиуса трубы"),
        ({"current": 12, "previous": None, "direct_rate": None}, "положительная скорость"),
        ({"diameter": 0}, "Наружный диаметр"),
        ({"pitting": Pitting(4, 1.2, None)}, "текучести"),
    ],
)
def test_input_errors(change, text):
    with pytest.raises(ForecastError, match=text):
        calc_forecast(replace(BASE, **change))


def test_protocol_text():
    r = calc_forecast(BASE)
    text = build_protocol(BASE, r, "07.10.2026 12:00:00")
    for part in (
        "1. РАСЧЁТ МИНИМАЛЬНО ДОПУСТИМОЙ ТОЛЩИНЫ",
        "2. ОПРЕДЕЛЕНИЕ СКОРОСТИ КОРРОЗИИ",
        "3. ОСТАТОЧНЫЙ РЕСУРС",
        "4. ПРОГНОЗ НА ЗАДАННЫЙ ПЕРИОД",
        "5. ЗАКЛЮЧЕНИЕ",
        "Примечание.",
        "Объект: МГ Тест",
        "R1 = R1н·m/(k1·kн) = 510·0,99/(1,4·1,1) = 327,857 МПа.",
        "Vэкспл = (δ0−δф)/t = (12−10,5)/21 = 0,071429 мм/год.",
        "V = 0,08 мм/год",
        "Tc = (cдоп−cф)/V = (4−1,5)/0,08 = 31,25 лет.",
        "Tпит = 79,04 лет",
        "Прогнозный год достижения предельного состояния: 2057,25.",
        "Дата формирования протокола: 07.10.2026 12:00:00",
    ):
        assert part in text
    assert "Rнорм" not in text


def test_num_format():
    assert num(1234.5, 2) == "1234,5"
    assert num(None) == "нет данных"
    assert num(-0.0000001, 3) == "0"
