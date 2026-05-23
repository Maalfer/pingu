"""Middleware que inyecta variables globales (tema, header_count) en request."""
from django.utils.deprecation import MiddlewareMixin


class GlobalContextMiddleware(MiddlewareMixin):
    """Lee la cookie bh_theme y la expone como request.bh_theme."""

    def process_request(self, request):
        theme = request.COOKIES.get("bh_theme", "")
        if theme not in ("dark", "light", "dracula", "pink", "aqua"):
            theme = ""
        # Si el usuario está autenticado y tiene un theme guardado, ese gana.
        if request.user.is_authenticated and getattr(request.user, "theme", None):
            theme = request.user.theme
        request.bh_theme = theme or "dark"
        return None

    def process_response(self, request, response):
        # Refresca la cookie con el tema actual para que el script inline del <html>
        # pueda leerlo antes de que cargue el CSS.
        theme = getattr(request, "bh_theme", None)
        if theme and request.COOKIES.get("bh_theme") != theme:
            response.set_cookie(
                "bh_theme", theme, max_age=60 * 60 * 24 * 365 * 5, samesite="Lax"
            )
        return response
