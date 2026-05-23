"""Chat 1-a-1."""
import json
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Max, Count
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from .models import Message
from apps.accounts.models import User
from apps.friends.models import Friendship


@login_required
def index(request):
    me = request.user
    # Conversaciones = amigos aceptados.
    friends = Friendship.objects.filter(
        Q(requester=me) | Q(addressee=me), status="accepted"
    )
    convos = []
    for f in friends:
        other = f.addressee if f.requester == me else f.requester
        last = Message.objects.filter(
            Q(sender=me, receiver=other) | Q(sender=other, receiver=me)
        ).order_by("-created_at").first()
        unread = Message.objects.filter(sender=other, receiver=me, read_at__isnull=True).count()
        convos.append({"friend": other, "last_msg": last, "unread": unread})
    convos.sort(key=lambda c: c["last_msg"].created_at if c["last_msg"] else timezone.now(), reverse=True)
    return render(request, "chat/chat.html", {"conversations": convos})


@login_required
def detail(request, friend_id):
    other = get_object_or_404(User, pk=friend_id)
    me = request.user
    msgs = Message.objects.filter(
        Q(sender=me, receiver=other) | Q(sender=other, receiver=me)
    ).order_by("created_at")
    # Marcar como leídos los recibidos
    Message.objects.filter(sender=other, receiver=me, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return render(request, "chat/detail.html", {"other_user": other, "messages": msgs})


@login_required
@require_POST
def api_send(request):
    data = json.loads(request.body or "{}")
    other_id = data.get("receiver_id") or data.get("to")
    content = (data.get("content") or "").strip()
    if not content:
        return JsonResponse({"error": "Vacío"}, status=400)
    if len(content) > 1000:
        return JsonResponse({"error": "Mensaje demasiado largo"}, status=400)
    other = get_object_or_404(User, pk=other_id)
    m = Message.objects.create(sender=request.user, receiver=other, content=content)
    return JsonResponse({"success": True, "id": m.id, "created_at": m.created_at.isoformat()})


@login_required
@require_GET
def api_messages(request, friend_id):
    me = request.user
    other = get_object_or_404(User, pk=friend_id)
    qs = Message.objects.filter(
        Q(sender=me, receiver=other) | Q(sender=other, receiver=me)
    ).order_by("-created_at")[:200]
    return JsonResponse({"messages": [{
        "id": m.id, "from": m.sender_id, "to": m.receiver_id,
        "content": m.content, "created_at": m.created_at.isoformat(),
        "read": bool(m.read_at),
    } for m in reversed(list(qs))]})
