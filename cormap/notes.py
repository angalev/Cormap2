"""Словарь объектов и разбор колонки «Комментарий» (раздел 4.3 ТЗ).

Модуль не зависит от Django и от openpyxl.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from cormap.model import WARNING, CommentItem, Issue, fmt_num, normalize_text

# Виды размещения на схеме.
ABOVE = "above"  # над трубой
ON = "on"  # на трубе
BELOW = "below"  # под трубой
CROSS_LINE = "cross_line"  # линия поперёк трубы
CROSS_BAND = "cross_band"  # полоса поперёк трубы
AREA_BELOW = "area_below"  # участок под трубой

# Группы словаря.
LEGEND = "legend"  # новая легенда (таблица 1 раздела 4.3)
LEGACY = "legacy"  # объекты старого словаря (значки временные)

# Роли пометки.
POINT = "point"
START = "start"
END = "end"
ROLE_TITLES = {POINT: "точка", START: "начало", END: "конец"}


@dataclass(frozen=True, slots=True)
class ObjectType:
    """Тип объекта схемы. Образцы в `patterns` записаны в нормализованном виде."""

    key: str
    title: str
    patterns: tuple[str, ...]
    placement: str
    icon: int | None
    group: str
    whole_word: bool = False  # образцы ищутся только отдельным словом
    cross_label: str | None = None  # метка пересечения: К, Н, В, Г
    extended: bool = False  # может быть протяжённым (начало/конец)


def _t(
    key: str,
    title: str,
    patterns: tuple[str, ...],
    placement: str,
    icon: int | None,
    group: str,
    **kw: object,
) -> ObjectType:
    return ObjectType(key, title, patterns, placement, icon, group, **kw)  # type: ignore[arg-type]


OBJECT_TYPES: tuple[ObjectType, ...] = (
    # --- новая легенда (значки 1-18) ---
    _t("kip", "Контрольно-измерительный пункт", ("кип",), ABOVE, 13, LEGEND),
    _t("ukz", "Станция катодной защиты", ("укз",), ABOVE, 16, LEGEND),
    _t("udz", "Станция дренажной защиты", ("удз",), ABOVE, 17, LEGEND),
    _t("crane", "Крановый узел", ("кран",), ON, 14, LEGEND),
    _t("kzou", "КЗОУ, КПОУ", ("кзоу", "кпоу"), ABOVE, 15, LEGEND),
    _t("vei", "ВЭИ (ИФС)", ("изолирующая вставка", "фланец"), ON, 12, LEGEND),
    _t(
        "vl",
        "Высоковольтная линия",
        ("вл", "лэп"),
        CROSS_LINE,
        1,
        LEGEND,
        whole_word=True,
        extended=True,
    ),
    _t(
        "cable",
        "Кабельная линия",
        ("пересечение с кабелем",),
        CROSS_LINE,
        2,
        LEGEND,
        cross_label="К",
        extended=True,
    ),
    _t(
        "oil_pipe",
        "Нефтепровод",
        ("пересечение с нефтепроводом",),
        CROSS_LINE,
        3,
        LEGEND,
        cross_label="Н",
        extended=True,
    ),
    _t(
        "water_pipe",
        "Водопровод",
        ("пересечение с водопроводом",),
        CROSS_LINE,
        4,
        LEGEND,
        cross_label="В",
        extended=True,
    ),
    _t(
        "gas_pipe",
        "Газопровод",
        ("пересечение с газопроводом",),
        CROSS_LINE,
        5,
        LEGEND,
        cross_label="Г",
        extended=True,
    ),
    _t(
        "road",
        "Автомобильная дорога",
        ("асфальт а/д", "грунт а/д"),
        CROSS_BAND,
        6,
        LEGEND,
        extended=True,
    ),
    _t("rail", "Железная дорога", ("ж/д",), CROSS_BAND, 7, LEGEND, extended=True),
    _t("crossing", "Переезд", ("переезд",), CROSS_LINE, 8, LEGEND),
    _t(
        "water",
        "Водная преграда",
        ("река", "ручей", "озеро", "пруд"),
        AREA_BELOW,
        9,
        LEGEND,
        extended=True,
    ),
    _t("swamp", "Болото", ("болот",), AREA_BELOW, 10, LEGEND, extended=True),  # префикс
    _t("forest", "Лес, кусты", ("лес",), AREA_BELOW, 11, LEGEND, extended=True),
    _t("pip", "Повреждение ИП", ("пип",), ABOVE, 18, LEGEND, whole_word=True, extended=True),
    # --- старый словарь (CorrosionScheme.markers), значки временные ---
    _t("picket", "Пикет", ("пк",), ABOVE, None, LEGACY, whole_word=True),
    _t("vent", "Свеча", ("свеча",), ABOVE, None, LEGACY),
    _t("bend", "Отвод", ("отвод",), ABOVE, None, LEGACY),
    _t("grs", "ГРС", ("грс",), ABOVE, None, LEGACY, whole_word=True),
    _t("ks", "КС", ("кс",), ABOVE, None, LEGACY, whole_word=True),
    _t(
        "surface_exit",
        "Выход на поверхность",
        ("выход на поверхность", "выход"),
        ABOVE,
        None,
        LEGACY,
    ),
    _t(
        "open_area",
        "Открытый участок",
        ("открытый участок",),
        AREA_BELOW,
        None,
        LEGACY,
        extended=True,
    ),
    _t(
        "air_crossing",
        "Воздушный переход",
        ("воздушный переход",),
        AREA_BELOW,
        None,
        LEGACY,
        extended=True,
    ),
    _t("casing", "Кожух", ("кожух", "кожух б/с"), CROSS_LINE, None, LEGACY, extended=True),
    _t("pit", "Шурф", ("шурф",), BELOW, None, LEGACY),
    _t("turn_left", "Поворот трассы влево", ("поворот влево",), BELOW, None, LEGACY),
    _t("turn_right", "Поворот трассы вправо", ("поворот вправо",), BELOW, None, LEGACY),
    _t("ravine", "Овраг", ("овраг",), BELOW, None, LEGACY, extended=True),
    _t("kip_nr", "КИП нерабочий", ("кип нерабочий",), ABOVE, None, LEGACY),
    _t("ukz_nr", "УКЗ нерабочая", ("укз нерабочая",), ABOVE, None, LEGACY),
    _t("udz_nr", "УДЗ нерабочая", ("удз нерабочая",), ABOVE, None, LEGACY),
)

_BY_KEY = {t.key: t for t in OBJECT_TYPES}


def get_type(key: str) -> ObjectType:
    """Тип по ключу; KeyError, если такого нет."""
    return _BY_KEY[key]


def used_types(objects: Iterable[TrackObject]) -> tuple[ObjectType, ...]:
    """Типы, встретившиеся среди объектов (в порядке реестра)."""
    seen = {o.type_key for o in objects}
    return tuple(t for t in OBJECT_TYPES if t.key in seen)


@dataclass(frozen=True, slots=True)
class TrackObject:
    """Объект трассы: точечный (end = None) или протяжённый."""

    type_key: str
    label: str
    start: float
    end: float | None
    row_start: int
    row_end: int | None
    voltage: str | None = None  # для ВЛ, текстом: «110 кВ»
    variant: str = ""  # какой образец сработал («река», «озеро»...)


@dataclass(frozen=True, slots=True)
class NoteRecord:
    """Строка таблицы «строка - пометка - как распознана»."""

    row: int
    dist: float
    text: str  # исходный текст пометки (без крайних пробелов)
    type_key: str | None  # None - не распознана
    role: str  # POINT / START / END
    label: str
    voltage: str | None = None
    variant: str = ""


@dataclass(frozen=True, slots=True)
class NotesResult:
    objects: tuple[TrackObject, ...] = ()
    records: tuple[NoteRecord, ...] = ()
    issues: tuple[Issue, ...] = ()

    @property
    def recognized(self) -> int:
        return sum(r.type_key is not None for r in self.records)

    @property
    def unknown(self) -> int:
        return sum(r.type_key is None for r in self.records)

    @property
    def unpaired(self) -> int:
        return sum(i.code in UNPAIRED_CODES for i in self.issues)


UNPAIRED_CODES = frozenset({"note_start_without_end", "note_end_without_start"})


_L = r"[^\W\d_]"  # буква


def _compile(patterns: tuple[str, ...], whole_word: bool) -> list[tuple[str, re.Pattern[str]]]:
    result = []
    for p in patterns:
        body = re.escape(p)
        # Граница - «не буква» (цифры допустимы: ВЛ110, ПК12). Для обычных образцов
        # проверяется только начало: продолжение слова разрешено (лесополоса).
        rx = rf"(?<!{_L}){body}(?!{_L})" if whole_word else rf"(?<!{_L}){body}"
        result.append((p, re.compile(rx)))
    return result


_MATCHERS = [(t, _compile(t.patterns, t.whole_word)) for t in OBJECT_TYPES]
_START_RX = re.compile(rf"(?<!{_L})начало(?!{_L})")
_END_RX = re.compile(rf"(?<!{_L})конец(?!{_L})")
_ROLE_WORDS_RX = re.compile(rf"(?<!{_L})(?:начало|конец)(?!{_L})", re.IGNORECASE)
# После образца сразу номер: «14», «-14», «№3», « 12+50».
_NUMBER_TAIL_RX = re.compile(r"^[-\s]*(?:\d|№|no\s*\d)")
_VOLTAGE_RX = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:кв\b)?")
_VOLTAGE_LABEL_RX = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*кв\b", re.IGNORECASE)


def _match_type(norm: str) -> tuple[ObjectType, str, int] | None:
    """Самый длинный подходящий образец (тип, образец, конец совпадения).

    При равенстве длины побеждает тот, что раньше в реестре.
    """
    best: tuple[ObjectType, str, int] | None = None
    for obj_type, matchers in _MATCHERS:
        for pattern, rx in matchers:
            m = rx.search(norm)
            if m and (best is None or len(pattern) > len(best[1])):
                best = (obj_type, pattern, m.end())
    return best


def _voltage(norm_type_part: str, pattern: str, label: str) -> str | None:
    """Напряжение ВЛ: цифры после слова «вл»/«лэп» (дефис, пробел), иначе из подписи."""
    found = re.search(rf"(?<!{_L}){re.escape(pattern)}(?!{_L})", norm_type_part)
    tail = norm_type_part[found.end() :] if found else ""
    tail = tail.lstrip(" -")
    m = _VOLTAGE_RX.match(tail)
    if m:
        return f"{m.group(1)} кВ"
    m = _VOLTAGE_LABEL_RX.match(normalize_text(label))
    return f"{m.group(1)} кВ" if m else None


def _merge_decimal_comma(type_part: str, label: str) -> tuple[str, str]:
    """«ВЛ-0,4 кВ»: запятая в напряжении не должна отделять подпись."""
    m = re.match(r"(\d+)(.*)$", label)
    if not m or not type_part.rstrip()[-1:].isdigit():
        return type_part, label
    rest_type, _, rest_label = m.group(2).partition(",")
    return f"{type_part.rstrip()},{m.group(1)}{rest_type}", rest_label.strip()


def _split_notes(text: str) -> list[str]:
    return [p.strip() for p in text.split(";") if p.strip()]


def parse_comments(items: Iterable[CommentItem]) -> NotesResult:
    """Разбирает колонку «Комментарий»: записи, объекты и замечания."""
    records: list[NoteRecord] = []
    issues: list[Issue] = []

    def warn(code: str, message: str, row: int) -> None:
        issues.append(Issue(WARNING, code, message, row=row))

    for item in items:
        for part in _split_notes(item.text):
            type_part, _, label_raw = part.partition(",")
            label = label_raw.strip()
            norm = normalize_text(type_part)
            matched = _match_type(norm) if norm else None
            if matched is not None and matched[0].key == "vl":
                type_part, label = _merge_decimal_comma(type_part, label)
                norm = normalize_text(type_part)
            if matched is None:
                records.append(NoteRecord(item.row, item.dist, part, None, POINT, label))
                warn("note_unknown", f"Пометка «{part}» не распознана.", item.row)
                continue
            obj_type, pattern, match_end = matched
            has_start = bool(_START_RX.search(norm))
            has_end = bool(_END_RX.search(norm))
            role = POINT
            if has_start and has_end:
                warn(
                    "note_both_roles",
                    f"В пометке «{part}» есть и «начало», и «конец»: считается точкой.",
                    item.row,
                )
            elif has_start or has_end:
                if obj_type.extended:
                    role = START if has_start else END
                else:
                    warn(
                        "note_not_extended",
                        f"Объект «{obj_type.title}» не бывает протяжённым: "
                        f"пометка «{part}» считается точкой.",
                        item.row,
                    )
            if not label and obj_type.key != "vl" and "," not in part:
                # Подпись по умолчанию: обозначение с номером («КИП-14», «ПК 12+50»).
                if _NUMBER_TAIL_RX.match(_ROLE_WORDS_RX.sub(" ", norm[match_end:]).lstrip()):
                    label = " ".join(_ROLE_WORDS_RX.sub(" ", type_part).split())
            voltage = _voltage(norm, pattern, label) if obj_type.key == "vl" else None
            records.append(
                NoteRecord(item.row, item.dist, part, obj_type.key, role, label, voltage, pattern)
            )

    objects = _pair(records, issues)
    return NotesResult(tuple(objects), tuple(records), tuple(issues))


def _pair(records: list[NoteRecord], issues: list[Issue]) -> list[TrackObject]:
    """Собирает объекты; пары «начало - конец» ищутся внутри одного типа."""
    objects: list[TrackObject] = []
    open_: dict[str, NoteRecord] = {}

    def point(rec: NoteRecord) -> TrackObject:
        assert rec.type_key is not None
        return TrackObject(
            rec.type_key, rec.label, rec.dist, None, rec.row, None, rec.voltage, rec.variant
        )

    def unpaired_start(rec: NoteRecord) -> None:
        title = get_type(rec.type_key or "").title
        issues.append(
            Issue(
                WARNING,
                "note_start_without_end",
                f"«{title}»: «начало» без «конца» (пометка «{rec.text}»). "
                "Объект нарисован как точечный.",
                row=rec.row,
            )
        )
        objects.append(point(rec))

    ordered = sorted((r for r in records if r.type_key is not None), key=lambda r: (r.dist, r.row))
    for rec in ordered:
        key = rec.type_key
        assert key is not None
        if rec.role == POINT:
            objects.append(point(rec))
        elif rec.role == START:
            if key in open_:
                unpaired_start(open_[key])
            open_[key] = rec
        else:
            begin = open_.pop(key, None)
            if begin is None:
                title = get_type(key).title
                issues.append(
                    Issue(
                        WARNING,
                        "note_end_without_start",
                        f"«{title}»: «конец» без «начала» (пометка «{rec.text}»). "
                        "Объект нарисован как точечный.",
                        row=rec.row,
                    )
                )
                objects.append(point(rec))
            else:
                objects.append(
                    TrackObject(
                        key,
                        begin.label or rec.label,
                        begin.dist,
                        rec.dist,
                        begin.row,
                        rec.row,
                        begin.voltage or rec.voltage,
                        begin.variant,
                    )
                )
    for rec in open_.values():
        unpaired_start(rec)
    objects.sort(key=lambda o: (o.start, o.row_start))
    return objects


def format_notes(result: NotesResult) -> str:
    """Таблица «строка - пометка - как распознана» и сводка для отчёта проверки."""
    lines = ["Разбор комментариев:"]
    if not result.records:
        lines.append("  пометок нет")
    else:
        lines.append("  строка | дистанция, м | пометка | как распознана")
        for r in result.records:
            if r.type_key is None:
                what = "НЕ РАСПОЗНАНА"
            else:
                what = get_type(r.type_key).title
                if r.role != POINT:
                    what += f", {ROLE_TITLES[r.role]}"
                if r.voltage:
                    what += f", {r.voltage}"
                if r.label:
                    what += f", подпись «{r.label}»"
            lines.append(f"  {r.row} | {fmt_num(r.dist)} | {r.text} | {what}")
    lines.append(
        f"  Распознано: {result.recognized}, не распознано: {result.unknown}, "
        f"непарных: {result.unpaired}"
    )
    return "\n".join(lines)


__all__ = [
    "OBJECT_TYPES",
    "NoteRecord",
    "NotesResult",
    "ObjectType",
    "TrackObject",
    "format_notes",
    "get_type",
    "parse_comments",
    "used_types",
]
