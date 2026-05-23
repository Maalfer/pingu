import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from .models import ShoppingItem


@login_required
def index(request):
    items = request.user.shopping_items.all()
    return render(request, "shopping/shopping.html", {"items": items})


@login_required
def api_list(request):
    items = list(request.user.shopping_items.values("id", "text", "done", "added_by_name", "created_at"))
    return JsonResponse({"items": items})


@login_required
@require_POST
def api_add(request):
    data = json.loads(request.body or "{}")
    text = (data.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "Texto vacío"}, status=400)
    it = ShoppingItem.objects.create(user=request.user, text=text, added_by_name=request.user.username)
    return JsonResponse({"id": it.id, "text": it.text, "done": it.done})


@login_required
@require_POST
def api_toggle(request, pk):
    it = get_object_or_404(ShoppingItem, pk=pk, user=request.user)
    it.done = not it.done
    it.save(update_fields=["done"])
    return JsonResponse({"id": it.id, "done": it.done})


@login_required
@require_POST
def api_toggle_legacy(request):
    """ID viene en el cuerpo JSON (compat. con el JS antiguo)."""
    data = json.loads(request.body or "{}")
    pk = data.get("id")
    return api_toggle(request, pk)


@login_required
@require_POST
def api_delete(request, pk):
    get_object_or_404(ShoppingItem, pk=pk, user=request.user).delete()
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_delete_legacy(request):
    data = json.loads(request.body or "{}")
    pk = data.get("id")
    return api_delete(request, pk)


@login_required
@require_POST
def api_clear(request):
    # En el JS antiguo "clear-done" → borra solo los marcados como hechos.
    request.user.shopping_items.filter(done=True).delete()
    return JsonResponse({"success": True})
