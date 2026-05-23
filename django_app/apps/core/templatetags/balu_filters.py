"""Filtros custom para plantillas."""
from django import template
from django.conf import settings

register = template.Library()


@register.filter
def avatar_url(user_id):
    """Devuelve `/static/avatars/<id>.jpg?v=<mtime>` si existe el avatar; "" si no.

    El cache-buster por mtime garantiza que al actualizar el avatar el browser
    refresque la imagen aunque nginx la sirva con cache largo.
    """
    if not user_id:
        return ""
    try:
        path = settings.BALUHOME_AVATARS_ROOT / f"{int(user_id)}.jpg"
    except (TypeError, ValueError):
        return ""
    try:
        mtime = int(path.stat().st_mtime)
    except OSError:
        return ""
    return f"/static/avatars/{int(user_id)}.jpg?v={mtime}"


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
