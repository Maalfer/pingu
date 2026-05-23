"""Filtros custom para plantillas: format_duration, money, format."""
from django import template

register = template.Library()


@register.filter
def format_duration(seconds):
    """Segundos → 'MM:SS' o 'HH:MM:SS'."""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return "0:00"
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{ss:02d}"
    return f"{m}:{ss:02d}"


@register.filter
def money(amount):
    """Formato europeo: 12.50€"""
    try:
        return f"{float(amount):.2f}€"
    except (TypeError, ValueError):
        return "0.00€"


@register.filter(name="format")
def fmt(value, spec):
    """Implementa {{ x|format:"0.2f" }} parecido a Python."""
    try:
        return format(value, spec)
    except (ValueError, TypeError):
        return value


@register.filter
def abs_value(value):
    try:
        return abs(value)
    except (TypeError, ValueError):
        return value
