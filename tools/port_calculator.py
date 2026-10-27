"""Перенос программы исполнителя «Калькулятор ЭХЗ.html» на сайт (ТЗ, 9.1).

Из исходного файла собираются три файла сайта: шаблон страницы, стили и сценарий.
Содержимое берётся как есть; меняется только то, чего требует Content-Security-Policy
сайта (встроенные стили и сценарии запрещены):

- <style> и <script> вынесены в отдельные файлы статики;
- атрибуты style="..." заменены классами (INLINE_STYLES, HIT_STYLE);
- в шапку добавлена ссылка возврата на сайт, в конец сценария - отметка в журнал.

Запуск: python tools/port_calculator.py [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "starter" / "Калькулятор ЭХЗ.html"
TEMPLATE = Path("web/maps/templates/maps/calculator.html")
STYLE = Path("web/maps/static/maps/calculator.css")
SCRIPT = Path("web/maps/static/maps/calculator.js")

# встроенный стиль исходника -> класс (правило добавляется в конец стилей)
INLINE_STYLES = {
    "margin-top:14px": ("mt14", "margin-top:14px"),
    "margin-top:12px": ("mt12", "margin-top:12px"),
    "max-height:none": ("nomax", "max-height:none"),
}
# выделение выбранной ячейки таблицы Rт: в исходнике - атрибут style в строке сценария
HIT_STYLE = "' style=\"background:#bcebed;font-weight:800\"'"
HIT_CLASS = "' class=\"hit\"'"

BACK_LINK = '<p class="site-back"><a href="{% url \'map_list\' %}">← Коррозионные карты</a></p>'

SITE_STYLES = """
/* --- добавлено при переносе на сайт (tools/port_calculator.py) --- */
.hit{background:#bcebed;font-weight:800}
.site-back{margin-top:10px}.site-back a{color:#9fdfe3}
@media print{.site-back{display:none!important}}
"""

SITE_SCRIPT = """
// --- добавлено при переносе на сайт (tools/port_calculator.py) ---
// Отметка в журнал сайта: только факт расчёта, без введённых данных и результатов.
(() => {
  'use strict';
  const box = document.getElementById('siteLog');
  if (!box || !window.fetch) return;
  const token = box.querySelector('input[name=csrfmiddlewaretoken]');
  const send = kind => {
    const body = new URLSearchParams({ kind });
    fetch(box.dataset.url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': token ? token.value : '' },
      body,
    }).catch(() => {});
  };
  const kinds = {
    calcCorrosion: 'calc',
    calcAllowThickness: 'calc',
    allCalc: 'calc',
    downloadCorrosionProtocol: 'download',
  };
  for (const [id, kind] of Object.entries(kinds)) {
    const button = document.getElementById(id);
    if (button) button.addEventListener('click', () => send(kind));
  }
})();
"""


class PortError(Exception):
    pass


def _between(text: str, start: str, end: str) -> str:
    try:
        head = text.index(start) + len(start)
        return text[head : text.index(end, head)]
    except ValueError:
        raise PortError(f"в исходнике не найдено «{start}» ... «{end}»") from None


def _classes_instead_of_styles(body: str) -> str:
    def swap(match: re.Match[str]) -> str:
        tag, classes, style = match.group(1), match.group(2), match.group(3)
        if style not in INLINE_STYLES:
            raise PortError(f"неизвестный встроенный стиль: {style}")
        return f'{tag}class="{classes} {INLINE_STYLES[style][0]}"'

    body = re.sub(r'(<\w+ )class="([^"]*)" style="([^"]*)"', swap, body)
    if re.search(r"\sstyle=", body):
        raise PortError("в разметке остался атрибут style: дополните INLINE_STYLES")
    if re.search(r"\son\w+=", body):
        raise PortError("в разметке есть встроенный обработчик события (on...=)")
    if "{%" in body or "{{" in body:
        raise PortError("в разметке есть скобки шаблонов Django")
    return body


def port(source: str) -> dict[Path, str]:
    """Тексты трёх файлов сайта по тексту исходника."""
    source = source.replace("\r\n", "\n")
    title = _between(source, "<title>", "</title>")
    style = textwrap.dedent(_between(source, "<style>", "</style>")).strip("\n")
    script = textwrap.dedent(_between(source, "<script>", "</script>")).strip("\n")
    body = _between(source, "<body>", "<script>").strip("\n")

    body = _classes_instead_of_styles(body)
    if body.count("</header>") != 1:
        raise PortError("ожидался один заголовок <header>")
    body = body.replace("</header>", BACK_LINK + "</header>")

    if script.count(HIT_STYLE) != 1:
        raise PortError("в сценарии не найдено выделение ячейки таблицы Rт")
    script = script.replace(HIT_STYLE, HIT_CLASS)
    if "style=" in script or "<style" in script:
        raise PortError("в сценарии остался встроенный стиль")

    rules = "".join(f".{name}{{{rule}}}" for name, rule in INLINE_STYLES.values())
    note = "Собрано из starter/Калькулятор ЭХЗ.html (tools/port_calculator.py). Не править руками."
    template = (
        f"{{% load static %}}{{# {note} #}}<!doctype html>\n"
        '<html lang="ru">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"  <title>{title}</title>\n"
        '  <link rel="stylesheet" href="{% static \'maps/calculator.css\' %}">\n'
        "</head>\n<body>\n"
        f"{body}\n"
        '<div id="siteLog" data-url="{% url \'forecast\' %}" hidden>{% csrf_token %}</div>\n'
        "<script src=\"{% static 'maps/calculator.js' %}\"></script>\n"
        "</body>\n</html>\n"
    )
    return {
        TEMPLATE: template,
        STYLE: f"/* {note} */\n{style}\n{SITE_STYLES}{rules}\n",
        SCRIPT: f"// {note}\n{script}\n{SITE_SCRIPT}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Перенос калькулятора ЭХЗ на сайт.")
    parser.add_argument("--check", action="store_true", help="только сверить, не записывать")
    args = parser.parse_args()
    files = port(SOURCE.read_text(encoding="utf-8"))
    stale = []
    for rel, text in files.items():
        path = ROOT / rel
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(rel)
            if not args.check:
                path.write_text(text, encoding="utf-8", newline="\n")
    for rel in stale:
        print(("устарел: " if args.check else "записан: ") + rel.as_posix())
    if not stale:
        print("Файлы калькулятора совпадают с исходником.")
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
