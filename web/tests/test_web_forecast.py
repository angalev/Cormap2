"""Страница «Калькулятор ЭХЗ» (ТЗ, 9.1): перенос программы исполнителя как есть."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client
from maps.models import AuditLog

from tools import port_calculator

ROOT = Path(__file__).resolve().parents[2]
URL = "/forecast/"

SECTIONS = (
    "1. Паспорт участка и геометрия трубы",
    "2. Нормативное сопротивление покрытия",
    "3. Продольное сопротивление трубы",
    "4. Переходное сопротивление",
    "5. Коррозионный прогноз",
    "6. Подробный протокол коррозионного прогноза",
    "7. Итоговый протокол",
)
# плитки промежуточных результатов и кнопки, на которые опирается сценарий
OUTPUT_IDS = (
    "rNormOut",
    "rtOut",
    "rpAvgOut",
    "rIsoOut",
    "bandROut",
    "allowR1Out",
    "allowPressureOut",
    "allowDnOut",
    "allowConstructOut",
    "allowFinalOut",
    "allowMarginOut",
    "corVService",
    "corVAccepted",
    "corLifeMin",
    "corLimitYear",
    "corrosionProtocol",
    "report",
)


def _entries():
    return list(AuditLog.objects.filter(action=AuditLog.Action.FORECAST))


def test_anonymous_redirected(client, db):
    for response in (client.get(URL), client.post(URL, {"kind": "calc"})):
        assert response.status_code == 302 and "/login/" in response["Location"]
    assert _entries() == []


def test_page_has_whole_calculator(client, users, login):
    login(users["a"])
    response = client.get(URL)
    assert response.status_code == 200
    html = response.content.decode()
    for title in SECTIONS:
        assert f"<h2>{title}</h2>" in html, title
    for name in OUTPUT_IDS:
        assert f'id="{name}"' in html, name
    assert "Откуда берутся R₁н и коэффициенты" in html  # справочная таблица
    assert html.count('class="formula') == 8  # блоки формул
    assert 'value="1020"' in html and 'value="0.99"' in html  # значения по умолчанию


def test_page_fits_content_security_policy(client, users, login, settings):
    """Встроенных стилей, сценариев и обработчиков на странице нет: всё в файлах статики."""
    login(users["a"])
    response = client.get(URL)
    assert response["Content-Security-Policy"] == settings.CORMAP_CSP
    html = response.content.decode()
    assert "<style" not in html and not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+=", html)
    assert re.findall(r"<script[^>]*>", html) == ['<script src="/static/maps/calculator.js">']
    assert 'href="/static/maps/calculator.css"' in html
    assert "http://" not in html and "https://" not in html
    script = (ROOT / port_calculator.SCRIPT).read_text(encoding="utf-8")
    assert "style=" not in script and "eval(" not in script and "http" not in script


def test_site_files_match_source():
    """Страница, стили и сценарий - ровно то, что собирается из файла исполнителя."""
    if not port_calculator.SOURCE.is_file():
        pytest.skip("нет исходного файла starter/Калькулятор ЭХЗ.html")
    source = port_calculator.SOURCE.read_text(encoding="utf-8")
    for rel, text in port_calculator.port(source).items():
        assert (ROOT / rel).read_text(encoding="utf-8") == text, (
            f"{rel.as_posix()} расходится с исходником: python tools/port_calculator.py"
        )


def test_source_code_is_carried_over_unchanged():
    """Сценарий исходника входит в файл сайта целиком; отличие одно - класс вместо style."""
    if not port_calculator.SOURCE.is_file():
        pytest.skip("нет исходного файла starter/Калькулятор ЭХЗ.html")
    source = port_calculator.SOURCE.read_text(encoding="utf-8").replace("\r\n", "\n")
    original = source.split("<script>", 1)[1].split("</script>", 1)[0].strip("\n")
    ported = (ROOT / port_calculator.SCRIPT).read_text(encoding="utf-8")
    assert original.replace(port_calculator.HIT_STYLE, port_calculator.HIT_CLASS) in ported
    styles = source.split("<style>", 1)[1].split("</style>", 1)[0]
    site_styles = (ROOT / port_calculator.STYLE).read_text(encoding="utf-8")
    for line in styles.strip().splitlines():
        assert line.strip() in site_styles


def test_port_refuses_unknown_inline_style():
    source = port_calculator.SOURCE.read_text(encoding="utf-8")
    broken = source.replace('style="margin-top:14px"', 'style="color:red"', 1)
    with pytest.raises(port_calculator.PortError):
        port_calculator.port(broken)


def test_menu_link(client, users, login):
    login(users["a"])
    html = client.get("/").content.decode()
    assert 'href="/forecast/"' in html and "Калькулятор ЭХЗ" in html
    assert 'href="/"' in client.get(URL).content.decode()  # ссылка возврата на сайт


# --- журнал: только факт расчёта -------------------------------------------------------------


def test_calc_is_logged_without_data(client, users, login):
    login(users["a"])
    response = client.post(URL, {"kind": "calc", "name": "Секретный участок", "diameter": "1020"})
    assert response.status_code == 204 and response.content == b""
    (entry,) = _entries()
    assert entry.user == users["a"] and entry.details == "расчёт на странице"
    text = " ".join((entry.details, entry.object_repr, entry.username))
    assert "Секретный" not in text and "1020" not in text


def test_download_is_logged(client, users, login):
    login(users["a"])
    assert client.post(URL, {"kind": "download"}).status_code == 204
    (entry,) = _entries()
    assert entry.details == "скачивание протокола"


@pytest.mark.parametrize("data", [{}, {"kind": ""}, {"kind": "что-то ещё"}])
def test_unknown_mark_is_not_logged(client, users, login, data):
    login(users["a"])
    assert client.post(URL, data).status_code == 400
    assert _entries() == []


def test_mark_needs_csrf_token(users):
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(users["a"])
    assert strict.post(URL, {"kind": "calc"}).status_code == 403
    page = strict.get(URL)
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode())
    assert token, "на странице нет токена для отметки в журнал"
    ok = strict.post(URL, {"kind": "calc"}, HTTP_X_CSRFTOKEN=token.group(1))
    assert ok.status_code == 204
    assert len(_entries()) == 1


def test_script_sends_mark_only_from_buttons():
    """Отметка уходит по нажатию кнопок, а не при открытии страницы."""
    script = (ROOT / port_calculator.SCRIPT).read_text(encoding="utf-8")
    added = script.split("// --- добавлено при переносе на сайт", 1)[1]
    assert added.count("fetch(") == 1 and "addEventListener('click'" in added
    for button in ("calcCorrosion", "calcAllowThickness", "allCalc", "downloadCorrosionProtocol"):
        assert button in added
    assert "FormData" not in added and ".value" not in added.replace("token.value", "")
