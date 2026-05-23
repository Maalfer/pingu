"""Gastos compartidos con amigos."""
import json
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from .models import Transaction
from apps.friends.models import Friendship
from apps.accounts.models import User


def _balance_for(friendship, me):
    """Saldo positivo: el otro me debe; negativo: yo le debo."""
    total = Decimal(0)
    for tx in friendship.transactions.all():
        if tx.user_id == me.id:
            total += tx.amount
        else:
            total -= tx.amount
    return total


@login_required
def index(request):
    me = request.user
    friendships = Friendship.objects.filter(
        Q(requester=me) | Q(addressee=me), status="accepted"
    )
    rows = []
    for f in friendships:
        other = f.addressee if f.requester == me else f.requester
        rows.append({"friendship": f, "friend": other, "balance": _balance_for(f, me)})
    return render(request, "gastos.html", {"friends": rows})


@login_required
def detail(request, friend_id):
    me = request.user
    other = get_object_or_404(User, pk=friend_id)
    friendship = Friendship.objects.filter(
        Q(requester=me, addressee=other) | Q(requester=other, addressee=me),
        status="accepted"
    ).first()
    if not friendship:
        return render(request, "gastos_detail.html", {
            "friend": other, "transactions": [], "balance": 0
        })
    transactions = friendship.transactions.all().order_by("-created_at")
    return render(request, "gastos_detail.html", {
        "friend": other, "transactions": transactions,
        "balance": _balance_for(friendship, me),
        "friendship": friendship,
    })


@login_required
@require_POST
def api_add(request):
    data = json.loads(request.body or "{}")
    friend_id = data.get("friend_id")
    amount = data.get("amount")
    description = (data.get("description") or "").strip()
    if not friend_id or amount is None or not description:
        return JsonResponse({"error": "Faltan campos"}, status=400)
    me = request.user
    friend = get_object_or_404(User, pk=friend_id)
    friendship = Friendship.objects.filter(
        Q(requester=me, addressee=friend) | Q(requester=friend, addressee=me),
        status="accepted"
    ).first()
    if not friendship:
        return JsonResponse({"error": "No sois amigos"}, status=400)
    tx = Transaction.objects.create(
        friendship=friendship, user=me,
        amount=Decimal(str(amount)), description=description[:400]
    )
    return JsonResponse({"success": True, "id": tx.id,
                         "balance": str(_balance_for(friendship, me))})


@login_required
@require_POST
def api_delete(request, pk):
    me = request.user
    tx = get_object_or_404(Transaction.objects.filter(user=me), pk=pk)
    tx.delete()
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_delete_legacy(request):
    import json
    data = json.loads(request.body or "{}")
    tx = get_object_or_404(Transaction.objects.filter(user=request.user), pk=data.get("id") or data.get("transaction_id"))
    tx.delete()
    return JsonResponse({"success": True})
