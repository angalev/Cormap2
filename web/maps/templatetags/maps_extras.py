from django import template

from cormap.model import fmt_num

register = template.Library()


@register.filter
def ru_num(value):
    """Число по-русски: десятичная запятая, без лишних нулей; пусто для None."""
    if value is None or value == "":
        return "—"
    try:
        return fmt_num(float(value))
    except (TypeError, ValueError):
        return str(value)


@register.filter
def state_class(state):
    """CSS-класс метки состояния."""
    return f"st-{state}"
