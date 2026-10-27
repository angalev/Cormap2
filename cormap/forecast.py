"""Коррозионный прогноз: допустимая толщина, скорость коррозии, остаточный ресурс.

Ручной расчёт по данным пользователя, с картами не связан (ТЗ, раздел 9.1).
Размеры - в мм, давление и напряжения - в МПа, время - в годах. Модуль не зависит
от Django. Формулы - по разделу 5 справочной программы, перенесены заново.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

METHODS = ("pressure", "sp36", "manual")
METHOD_LABELS = {
    "pressure": "расчёт по внутреннему давлению",
    "sp36": "расчёт по СП 36.13330 (с минимумами по DN)",
    "manual": "ручной ввод",
}


class ForecastError(ValueError):
    """Ошибка исходных данных прогноза; текст понятен пользователю."""


@dataclass(frozen=True)
class PreviousSurvey:
    """Предыдущее обследование (необязательный блок)."""

    year: float | None = None
    thickness: float | None = None  # δпред, мм
    depth: float | None = None  # cпред, мм


@dataclass(frozen=True)
class Coupon:
    """Контрольный образец (необязательный блок)."""

    mass_loss: float | None = None  # Δm, г
    area: float | None = None  # A, см²
    hours: float | None = None  # τ, ч
    density: float | None = None  # ρ, г/см³


@dataclass(frozen=True)
class Pitting:
    """Язвенная коррозия (необязательный блок)."""

    size: float | None = None  # d, мм
    pressure: float | None = None  # P, МПа
    yield_strength: float | None = None  # σт, МПа


@dataclass(frozen=True)
class ForecastInput:
    name: str
    year_start: float
    year_survey: float
    diameter: float  # D, мм
    nominal: float  # δ0, мм
    # допустимая толщина
    method: str  # pressure / sp36 / manual
    dn: float | None = None
    pressure: float | None = None  # p, МПа
    n: float | None = None
    r1n: float | None = None
    m: float | None = None
    k1: float | None = None
    kn: float | None = None
    addition: float = 0.0  # C, мм
    manual_allow: float | None = None  # δдоп при ручном вводе
    # состояние
    current: float = 0.0  # δф, мм
    depth: float = 0.0  # cф, мм
    depth_allow: float = 0.0  # cдоп, мм
    horizon: float = 0.0  # лет
    direct_rate: float | None = None  # мм/год
    # необязательные блоки
    previous: PreviousSurvey | None = None
    coupon: Coupon | None = None
    pitting: Pitting | None = None


@dataclass(frozen=True)
class ForecastResult:
    method: str
    age: float  # t, лет
    allow: float  # δдоп, мм
    margin: float  # δф − δдоп
    r1: float | None  # R1, МПа (нет при ручном вводе)
    delta_p: float | None
    dn_floor: float | None
    construct_floor: float | None
    v_service: float
    v_between: float | None
    v_depth: float | None
    v_mass: float | None
    v_direct: float | None
    v_accepted: float
    life_thk: float
    life_depth: float
    h_crit: float | None
    v_pit: float | None
    life_pit: float | None
    life_min: float
    limit_year: float
    forecast_thk: float
    forecast_depth: float


def _pos(value: float | None, title: str) -> float:
    if value is None or not math.isfinite(value) or value <= 0:
        raise ForecastError(f"{title}: нужно число больше нуля.")
    return float(value)


def calc_forecast(data: ForecastInput) -> ForecastResult:
    """Расчёт прогноза. Ошибки ввода - ForecastError."""
    if data.method not in METHODS:
        raise ForecastError("Неизвестный способ определения допустимой толщины.")
    d_out = _pos(data.diameter, "Наружный диаметр D")
    nominal = _pos(data.nominal, "Номинальная толщина стенки δ0")
    current = _pos(data.current, "Минимальная фактическая толщина δф")
    depth_allow = _pos(data.depth_allow, "Допустимая глубина дефекта cдоп")
    depth = max(0.0, data.depth)
    horizon = max(0.0, data.horizon)
    addition = max(0.0, data.addition)

    age = data.year_survey - data.year_start
    if not age > 0:
        raise ForecastError("Год обследования должен быть позже года ввода в эксплуатацию.")

    r1 = delta_p = dn_floor = construct_floor = None
    if data.method == "manual":
        allow = _pos(data.manual_allow, "Допустимая толщина δдоп")
    else:
        dn = _pos(data.dn, "DN")
        p = _pos(data.pressure, "Рабочее давление p")
        n = _pos(data.n, "Коэффициент n")
        r1n = _pos(data.r1n, "R1н")
        m = _pos(data.m, "Коэффициент m")
        k1 = _pos(data.k1, "Коэффициент k1")
        kn = _pos(data.kn, "Коэффициент kн")
        r1 = r1n * m / (k1 * kn)
        delta_p = n * p * d_out / (2 * (r1 + n * p))
        dn_floor = dn / 100
        construct_floor = 12.0 if dn >= 1000 else (3.0 if dn <= 200 else 4.0)
        base = max(delta_p, dn_floor, construct_floor) if data.method == "sp36" else delta_p
        allow = base + addition
    if allow >= d_out / 2:
        raise ForecastError(
            "Допустимая толщина получилась не меньше радиуса трубы: проверьте исходные данные."
        )
    if allow > nominal:
        raise ForecastError("Минимально допустимая толщина не может превышать номинальную.")
    if current > nominal:
        raise ForecastError("Текущая минимальная толщина не может превышать номинальную.")

    v_service = max(0.0, (nominal - current) / age)
    v_between = v_depth = None
    prev = data.previous
    if prev is not None and prev.year is not None:
        dt = data.year_survey - prev.year
        if dt > 0:
            if prev.thickness is not None and prev.thickness >= current:
                v_between = (prev.thickness - current) / dt
            if prev.depth is not None and depth >= prev.depth:
                v_depth = (depth - prev.depth) / dt
    v_mass = None
    cp = data.coupon
    if cp is not None:
        dm, area, hours, rho = cp.mass_loss, cp.area, cp.hours, cp.density
        if dm and area and hours and rho and min(dm, area, hours, rho) > 0:
            v_mass = 8.76e4 * dm / (area * hours * rho)
    v_direct = None if data.direct_rate is None else max(0.0, data.direct_rate)

    rates = [v for v in (v_service, v_between, v_depth, v_mass, v_direct) if v and v > 0]
    if not rates:
        raise ForecastError("Ни одним методом не получена положительная скорость коррозии.")
    v = max(rates)

    life_thk = 0.0 if current <= allow else (current - allow) / v
    life_depth = 0.0 if depth >= depth_allow else (depth_allow - depth) / v

    h_crit = v_pit = life_pit = None
    pit = data.pitting
    if pit is not None and pit.size is not None and pit.size > 0:
        sigma = _pos(pit.yield_strength, "Предел текучести σт")
        if pit.pressure is None or pit.pressure < 0:
            raise ForecastError("Давление P для язвенной коррозии: нужно число не меньше нуля.")
        h_crit = nominal - pit.size * math.sqrt(0.19 * pit.pressure / (0.75 * sigma))
        v_cor = v_service if v_service > 0 else v
        v_pit = (v_cor + pit.size / age) / 2
        life_pit = 0.0 if depth >= h_crit else (h_crit - depth) / v_pit

    life_min = min(x for x in (life_thk, life_depth, life_pit) if x is not None)
    return ForecastResult(
        method=data.method,
        age=age,
        allow=allow,
        margin=current - allow,
        r1=r1,
        delta_p=delta_p,
        dn_floor=dn_floor,
        construct_floor=construct_floor,
        v_service=v_service,
        v_between=v_between,
        v_depth=v_depth,
        v_mass=v_mass,
        v_direct=v_direct,
        v_accepted=v,
        life_thk=life_thk,
        life_depth=life_depth,
        h_crit=h_crit,
        v_pit=v_pit,
        life_pit=life_pit,
        life_min=life_min,
        limit_year=data.year_survey + life_min,
        forecast_thk=max(0.0, current - v * horizon),
        forecast_depth=depth + v * horizon,
    )


def num(x: float | None, digits: int = 6) -> str:
    """Число для текста: десятичная запятая, без группировки, без лишних нулей."""
    if x is None or not math.isfinite(x):
        return "нет данных"
    text = f"{x:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", ",")


def build_protocol(data: ForecastInput, res: ForecastResult, made_at: str) -> str:
    """Текст протокола с подстановкой чисел в формулы. made_at - дата формирования."""
    v = num
    t = res.age
    prev = data.previous
    cp = data.coupon
    pit = data.pitting
    depth = max(0.0, data.depth)
    horizon = max(0.0, data.horizon)
    lines = [
        "ПРОТОКОЛ РАСЧЁТА МИНИМАЛЬНО ДОПУСТИМОЙ ТОЛЩИНЫ И КОРРОЗИОННОГО ПРОГНОЗА",
        "",
        f"Объект: {data.name}",
        f"Год ввода в эксплуатацию: {v(data.year_start, 0)}",
        f"Год обследования: {v(data.year_survey, 0)}",
        f"Продолжительность эксплуатации: {v(t, 2)} лет",
        f"Наружный диаметр трубы D: {v(data.diameter, 3)} мм",
        f"Номинальная толщина стенки δ0: {v(data.nominal, 3)} мм",
        f"Минимальная фактическая толщина δф: {v(data.current, 3)} мм",
        f"Фактическая глубина дефекта cф: {v(depth, 3)} мм",
        "",
        "1. РАСЧЁТ МИНИМАЛЬНО ДОПУСТИМОЙ ТОЛЩИНЫ",
    ]
    if res.method == "manual":
        lines.append(f"Минимально допустимая толщина принята вручную: {v(res.allow, 3)} мм.")
    else:
        sp36 = res.method == "sp36"
        lines += [
            "Режим: "
            + (
                "СП 36.13330 с конструктивными минимумами по DN"
                if sp36
                else "эксплуатационная оценка прямой трубы по внутреннему давлению"
            ),
            f"Исходные данные: p = {v(data.pressure, 3)} МПа; DN = {v(data.dn, 0)} мм; "
            f"n = {v(data.n, 3)}; R1н = {v(data.r1n, 3)} МПа; m = {v(data.m, 3)}; "
            f"k1 = {v(data.k1, 3)}; kн = {v(data.kn, 3)}; C = {v(data.addition, 3)} мм.",
            "",
            "Происхождение характеристик и коэффициентов:",
            "R1н — минимальное нормативное временное сопротивление металла растяжению по "
            "сертификату трубы либо стандарту/ТУ на трубу (СП 36.13330, п. 12.1.1).",
            "m — коэффициент условий работы по категории участка, таблица 1 СП 36: "
            "В — 0,660; I–II — 0,825; III–IV — 0,990.",
            "k1 — коэффициент надёжности по материалу по способу изготовления и объёму "
            "контроля трубы, таблица 10 СП 36: 1,34; 1,40; 1,47 или 1,55.",
            "kн — коэффициент надёжности по ответственности, выбираемый по DN и рабочему "
            "давлению, таблица 12 СП 36.",
            "n — коэффициент надёжности по нагрузке от внутреннего давления, таблица 14 СП 36; "
            "для газопроводов обычно 1,10.",
            "C — дополнительная суммарная прибавка. Она не входит в формулу (10) СП 36 и "
            "применяется только при наличии требования проекта или другого применимого "
            "документа. Может учитывать минусовый допуск заготовки, технологическое утонение "
            "и запас на коррозию/эрозию.",
            "При отдельном прогнозе утонения прибавку на будущую коррозию нельзя учитывать "
            "повторно без обоснования, поскольку это может привести к двойному учёту одного "
            "износа.",
            "",
            "Расчётное сопротивление растяжению:",
            f"R1 = R1н·m/(k1·kн) = {v(data.r1n, 3)}·{v(data.m, 3)}/"
            f"({v(data.k1, 3)}·{v(data.kn, 3)}) = {v(res.r1, 3)} МПа.",
            "",
            "Толщина, требуемая по внутреннему давлению:",
            f"δp = n·p·D/[2(R1+n·p)] = {v(data.n, 3)}·{v(data.pressure, 3)}·"
            f"{v(data.diameter, 3)}/[2·({v(res.r1, 3)}+{v(data.n, 3)}·{v(data.pressure, 3)})]"
            f" = {v(res.delta_p, 3)} мм.",
        ]
        if sp36:
            lines += [
                f"Минимум 1/100 DN: {v(res.dn_floor, 3)} мм.",
                f"Конструктивный минимум для DN {v(data.dn, 0)}: {v(res.construct_floor, 3)} мм.",
                f"Итог: δдоп = max({v(res.delta_p, 3)}; {v(res.dn_floor, 3)}; "
                f"{v(res.construct_floor, 3)}) + {v(data.addition, 3)} = {v(res.allow, 3)} мм.",
            ]
        else:
            lines.append(
                f"Итог: δдоп = δp + C = {v(res.delta_p, 3)} + {v(data.addition, 3)} = "
                f"{v(res.allow, 3)} мм."
            )
        lines.append(
            f"Запас фактической толщины: δф − δдоп = {v(data.current, 3)} − {v(res.allow, 3)}"
            f" = {v(res.margin, 3)} мм."
        )

    lines += ["", "2. ОПРЕДЕЛЕНИЕ СКОРОСТИ КОРРОЗИИ"]
    lines.append(
        f"За весь срок эксплуатации: Vэкспл = (δ0−δф)/t = ({v(data.nominal, 3)}−"
        f"{v(data.current, 3)})/{v(t, 2)} = {v(res.v_service)} мм/год."
    )
    dt = data.year_survey - prev.year if prev is not None and prev.year is not None else None
    if res.v_between is not None:
        lines.append(
            f"Между обследованиями: Vмеж = (δпред−δф)/Δt = ({v(prev.thickness, 3)}−"
            f"{v(data.current, 3)})/{v(dt, 2)} = {v(res.v_between)} мм/год."
        )
    else:
        lines.append(
            "Между обследованиями: не рассчитано — недостаточно согласованных исходных данных."
        )
    if res.v_depth is not None:
        lines.append(
            f"По росту дефекта: Vдеф = (cф−cпред)/Δt = ({v(depth, 3)}−{v(prev.depth, 3)})/"
            f"{v(dt, 2)} = {v(res.v_depth)} мм/год."
        )
    else:
        lines.append(
            "По росту дефекта: не рассчитано — недостаточно согласованных исходных данных."
        )
    if res.v_mass is not None:
        lines.append(
            f"По потере массы: Vмасс = 8,76·10⁴·Δm/(A·τ·ρ) = 8,76·10⁴·{v(cp.mass_loss, 3)}/"
            f"({v(cp.area, 3)}·{v(cp.hours, 3)}·{v(cp.density, 3)}) = {v(res.v_mass)} мм/год."
        )
    else:
        lines.append(
            "По потере массы контрольного образца: не рассчитано — потеря массы не задана."
        )
    lines += [
        f"Непосредственно заданная скорость: {v(res.v_direct)} мм/год.",
        "Для консервативной оценки принята максимальная положительная скорость "
        f"V = {v(res.v_accepted)} мм/год.",
        "",
        "3. ОСТАТОЧНЫЙ РЕСУРС",
        f"По толщине стенки: Tδ = (δф−δдоп)/V = ({v(data.current, 3)}−{v(res.allow, 3)})/"
        f"{v(res.v_accepted)} = {v(res.life_thk, 2)} лет.",
        f"По глубине дефекта: Tc = (cдоп−cф)/V = ({v(data.depth_allow, 3)}−{v(depth, 3)})/"
        f"{v(res.v_accepted)} = {v(res.life_depth, 2)} лет.",
    ]
    if res.life_pit is not None:
        lines += [
            f"Язвенная коррозия: hкр = δ0−d·√[0,19P/(0,75σт)] = {v(res.h_crit, 3)} мм.",
            f"Скорость роста язвы Vк1 = {v(res.v_pit)} мм/год; "
            f"остаточный ресурс Tпит = {v(res.life_pit, 2)} лет.",
            f"Для расчёта использованы d = {v(pit.size, 3)} мм; P = {v(pit.pressure, 3)} МПа; "
            f"σт = {v(pit.yield_strength, 3)} МПа.",
        ]
    lines += [
        "",
        "4. ПРОГНОЗ НА ЗАДАННЫЙ ПЕРИОД",
        f"Горизонт прогноза: {v(horizon, 2)} лет.",
        f"Прогнозная толщина: δпр = δф−V·T = {v(data.current, 3)}−{v(res.v_accepted)}·"
        f"{v(horizon, 2)} = {v(res.forecast_thk, 3)} мм.",
        f"Прогнозная глубина дефекта: cпр = cф+V·T = {v(depth, 3)}+{v(res.v_accepted)}·"
        f"{v(horizon, 2)} = {v(res.forecast_depth, 3)} мм.",
        "",
        "5. ЗАКЛЮЧЕНИЕ",
        f"Минимальный расчётный остаточный ресурс: {v(res.life_min, 2)} лет.",
        f"Прогнозный год достижения предельного состояния: {v(res.limit_year, 2)}.",
    ]
    if res.life_min <= 0:
        lines.append(
            "На дату обследования один из проверяемых пределов уже достигнут или превышен. "
            "Требуется инженерная оценка и решение эксплуатирующей организации."
        )
    else:
        lines.append(
            "При неизменной принятой скорости коррозии расчётный предел не будет достигнут "
            f"ранее чем через {v(res.life_min, 2)} лет."
        )
    lines += [
        "",
        "Примечание. Расчёт прямой трубы по внутреннему давлению не учитывает продольные "
        "напряжения, устойчивость, сейсмические воздействия, локальное взаимодействие "
        "дефектов, трещины, состояние сварных соединений и фасонных деталей. Исходные "
        "характеристики материала и коэффициенты подлежат подтверждению по проектной и "
        "исполнительной документации.",
        "",
        f"Дата формирования протокола: {made_at}",
    ]
    return "\n".join(lines)
