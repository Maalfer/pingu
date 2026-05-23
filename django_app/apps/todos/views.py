import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST
from .models import Todo


@login_required
def index(request):
    return render(request, "todos/todos.html", {"items": request.user.todos.all()})


@login_required
def api_list(request):
    return JsonResponse({"items": list(request.user.todos.values("id", "title", "done", "created_at"))})


@login_required
@require_POST
def api_add(request):
    data = json.loads(request.body or "{}")
    title = (data.get("title") or data.get("text") or "").strip()
    if not title:
        return JsonResponse({"error": "Vacío"}, status=400)
    t = Todo.objects.create(user=request.user, title=title)
    return JsonResponse({"id": t.id, "title": t.title, "done": t.done})


@login_required
@require_POST
def api_toggle_legacy(request):
    data = json.loads(request.body or "{}")
    t = get_object_or_404(Todo, pk=data.get("id"), user=request.user)
    t.done = not t.done
    t.save(update_fields=["done"])
    return JsonResponse({"id": t.id, "done": t.done})


@login_required
@require_POST
def api_delete_legacy(request):
    data = json.loads(request.body or "{}")
    get_object_or_404(Todo, pk=data.get("id"), user=request.user).delete()
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_clear(request):
    request.user.todos.filter(done=True).delete()
    return JsonResponse({"success": True})
