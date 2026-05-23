"""Amigos: enviar, aceptar/rechazar, eliminar."""
import json
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from .models import Friendship
from apps.accounts.models import User


@login_required
def index(request):
    me = request.user
    accepted = Friendship.objects.filter(Q(requester=me) | Q(addressee=me), status="accepted")
    received = Friendship.objects.filter(addressee=me, status="pending")
    sent = Friendship.objects.filter(requester=me, status="pending")
    related_ids = set()
    for f in accepted:
        related_ids.add(f.requester_id)
        related_ids.add(f.addressee_id)
    for f in received:
        related_ids.add(f.requester_id)
    for f in sent:
        related_ids.add(f.addressee_id)
    related_ids.add(me.id)
    others = User.objects.exclude(pk__in=related_ids)
    return render(request, "friends/friends.html", {
        "accepted": accepted, "received": received, "sent": sent, "others": others,
    })


@login_required
@require_POST
def api_request(request):
    data = json.loads(request.body or "{}")
    other_id = data.get("user_id")
    if other_id == request.user.id:
        return JsonResponse({"error": "No puedes añadirte a ti mismo"}, status=400)
    other = get_object_or_404(User, pk=other_id)
    me = request.user
    # ¿Ya hay una relación en cualquier dirección?
    if Friendship.objects.filter(
        Q(requester=me, addressee=other) | Q(requester=other, addressee=me)
    ).exists():
        return JsonResponse({"error": "Ya existe una relación"}, status=400)
    Friendship.objects.create(requester=me, addressee=other, status="pending")
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_respond(request):
    data = json.loads(request.body or "{}")
    fid = data.get("friendship_id")
    accept = bool(data.get("accept", False))
    f = get_object_or_404(Friendship, pk=fid, addressee=request.user, status="pending")
    if accept:
        f.status = "accepted"
        f.save(update_fields=["status"])
    else:
        f.delete()
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_remove(request):
    data = json.loads(request.body or "{}")
    fid = data.get("friendship_id")
    f = get_object_or_404(
        Friendship.objects.filter(Q(requester=request.user) | Q(addressee=request.user)),
        pk=fid,
    )
    f.delete()
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_send_legacy(request):
    import json
    data = json.loads(request.body or "{}")
    other_id = data.get("user_id") or data.get("to_user_id")
    if not other_id:
        return JsonResponse({"error": "Falta user_id"}, status=400)
    if int(other_id) == request.user.id:
        return JsonResponse({"error": "No puedes añadirte a ti mismo"}, status=400)
    other = get_object_or_404(User, pk=other_id)
    if Friendship.objects.filter(
        Q(requester=request.user, addressee=other) | Q(requester=other, addressee=request.user)
    ).exists():
        return JsonResponse({"error": "Ya existe una relación"}, status=400)
    Friendship.objects.create(requester=request.user, addressee=other, status="pending")
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_accept_legacy(request):
    import json
    data = json.loads(request.body or "{}")
    fid = data.get("friendship_id") or data.get("id")
    f = get_object_or_404(Friendship, pk=fid, addressee=request.user, status="pending")
    f.status = "accepted"
    f.save(update_fields=["status"])
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_reject_legacy(request):
    import json
    data = json.loads(request.body or "{}")
    fid = data.get("friendship_id") or data.get("id")
    f = Friendship.objects.filter(
        Q(requester=request.user) | Q(addressee=request.user),
        pk=fid,
    ).first()
    if f:
        f.delete()
    return JsonResponse({"success": True})


@login_required
def api_list_legacy(request):
    me = request.user
    accepted = Friendship.objects.filter(Q(requester=me)|Q(addressee=me), status="accepted")
    users = []
    for f in accepted:
        other = f.addressee if f.requester == me else f.requester
        users.append({"id": other.id, "username": other.username, "friendship_id": f.id})
    return JsonResponse({"friends": users})


@login_required
def api_pending_count(request):
    n = Friendship.objects.filter(addressee=request.user, status="pending").count()
    return JsonResponse({"count": n})
