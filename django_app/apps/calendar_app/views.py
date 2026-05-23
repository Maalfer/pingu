import json, datetime
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST
from .models import CalendarEvent


@login_required
def index(request):
    today = datetime.date.today()
    return render(request, "calendar_app/calendar.html", {
        "events": request.user.calendar_events.all(),
        "today_day": today.day, "today_month": today.month, "today_year": today.year,
    })


@login_required
def api_events(request):
    return JsonResponse({"events": list(request.user.calendar_events.values(
        "id","title","day","month","color","description","is_all_day","start_time","end_time"
    ))})


def _save(e, d):
    for fld in ("title","color","description","start_time","end_time"):
        if fld in d: setattr(e, fld, d[fld] if d[fld] is not None else "")
    if "day" in d: e.day = int(d["day"])
    if "month" in d: e.month = int(d["month"])
    if "is_all_day" in d: e.is_all_day = bool(d["is_all_day"])


@login_required
@require_POST
def api_add(request):
    d = json.loads(request.body or "{}")
    e = CalendarEvent(user=request.user)
    _save(e, d)
    if not e.title: return JsonResponse({"error":"Falta título"}, status=400)
    e.save()
    return JsonResponse({"id": e.id, "success": True})


@login_required
@require_POST
def api_update_legacy(request):
    d = json.loads(request.body or "{}")
    e = get_object_or_404(CalendarEvent, pk=d.get("id"), user=request.user)
    _save(e, d)
    e.save()
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_delete_legacy(request):
    d = json.loads(request.body or "{}")
    get_object_or_404(CalendarEvent, pk=d.get("id"), user=request.user).delete()
    return JsonResponse({"success": True})


# Backwards compat (URL with int pk)
@login_required
@require_POST
def api_update(request, pk):
    d = json.loads(request.body or "{}"); d["id"] = pk
    return api_update_legacy(request)


@login_required
@require_POST
def api_delete(request, pk):
    d = json.loads(request.body or "{}"); d["id"] = pk
    return api_delete_legacy(request)
