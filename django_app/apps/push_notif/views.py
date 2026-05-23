"""Web Push: subscribe + unsubscribe + envío server-side via pywebpush."""
import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET

from .models import PushSubscription

log = logging.getLogger(__name__)


@require_GET
def vapid_key(request):
    return JsonResponse({"key": settings.VAPID_PUBLIC_KEY or ""})


@login_required
@require_POST
def subscribe(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)
    endpoint = (data.get("endpoint") or "").strip()
    p256dh = (data.get("p256dh") or "").strip()
    auth = (data.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return JsonResponse({"error": "Datos incompletos"}, status=400)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={"user": request.user, "p256dh": p256dh, "auth": auth},
    )
    return JsonResponse({"success": True})


@login_required
@require_POST
def unsubscribe(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)
    endpoint = (data.get("endpoint") or "").strip()
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({"success": True})


def push_to_user(user, *, title, body, url="/home/", tag=None):
    """Envía una notificación web push a todas las suscripciones de un usuario.

    Llamar desde otras apps (p. ej. chat al recibir mensaje).
    """
    if not settings.VAPID_PRIVATE_KEY:
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning("pywebpush no instalado")
        return
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    claims = {"sub": settings.VAPID_CLAIM_EMAIL}
    sent = failed = 0
    for sub in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=claims,
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            if e.response is not None and e.response.status_code in (404, 410):
                # Suscripción muerta → eliminar.
                sub.delete()
    return {"sent": sent, "failed": failed}
