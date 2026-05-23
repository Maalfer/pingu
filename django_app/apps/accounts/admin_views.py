"""Vistas del panel de admin (acciones POST + APIs admin)."""
import json
import logging
import re
import secrets
import subprocess
from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_GET

from .models import User, InviteToken, ActivityLog

log = logging.getLogger(__name__)

# admin_only es el equivalente Django-canónico de "sólo si role == admin".
# `User.save()` mantiene is_staff sincronizado con role.
admin_only = staff_member_required(login_url="/")


def _log(user, action, detail=""):
    ActivityLog.objects.create(user=user, username=user.username, action=action, detail=detail)


def _err(msg, status=400):
    return JsonResponse({"error": msg}, status=status)


@admin_only
@require_POST
def delete_user(request):
    data = json.loads(request.body or "{}")
    try:
        target_id = int(data.get("user_id") or 0)
    except (TypeError, ValueError):
        return _err("ID inválido")
    if target_id == request.user.id:
        return _err("No puedes eliminarte a ti mismo")
    target = User.objects.filter(pk=target_id).first()
    if not target:
        return _err("Usuario no encontrado", 404)
    # Borrar archivos físicos de canciones antes de borrar al usuario (CASCADE en DB se encarga de la BD).
    for path in target.songs.values_list("file_path", flat=True):
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
    InviteToken.objects.filter(used_by=target).update(used_by=None)
    target.delete()
    _log(request.user, "admin_delete_user", f"Eliminó usuario ID {target_id}")
    return JsonResponse({"success": True})


@admin_only
@require_POST
def change_user_password(request):
    data = json.loads(request.body or "{}")
    try:
        target_id = int(data.get("user_id") or 0)
    except (TypeError, ValueError):
        return _err("ID inválido")
    new_password = data.get("new_password", "")
    target = get_object_or_404(User, pk=target_id)
    try:
        validate_password(new_password, user=target)
    except ValidationError as exc:
        return _err(" ".join(exc.messages))
    target.set_password(new_password)
    target.save()
    return JsonResponse({"success": True})


@admin_only
@require_POST
def change_user_username(request):
    data = json.loads(request.body or "{}")
    try:
        target_id = int(data.get("user_id") or 0)
    except (TypeError, ValueError):
        return _err("ID inválido")
    new_username = (data.get("new_username") or "").strip()
    if len(new_username) < 3 or len(new_username) > 30:
        return _err("El usuario debe tener entre 3 y 30 caracteres")
    if not re.match(r"^[a-zA-Z0-9_.-]+$", new_username):
        return _err("Caracteres no válidos")
    target = get_object_or_404(User, pk=target_id)
    if User.objects.filter(username__iexact=new_username).exclude(pk=target_id).exists():
        return _err("Ese nombre de usuario ya existe")
    target.username = new_username
    target.save(update_fields=["username"])
    return JsonResponse({"success": True, "new_username": new_username})


@admin_only
@require_POST
def change_user_role(request):
    data = json.loads(request.body or "{}")
    try:
        target_id = int(data.get("user_id") or 0)
    except (TypeError, ValueError):
        return _err("ID inválido")
    new_role = data.get("new_role", "")
    if new_role not in ("admin", "user"):
        return _err("Rol inválido")
    if target_id == request.user.id:
        return _err("No puedes cambiar tu propio rol")
    target = get_object_or_404(User, pk=target_id)
    target.role = new_role
    target.save(update_fields=["role"])
    return JsonResponse({"success": True})


@admin_only
@require_POST
def create_invite(request):
    token = secrets.token_hex(24)
    InviteToken.objects.create(token=token, created_by=request.user)
    scheme = "https" if request.is_secure() else "http"
    host = request.get_host()
    link = f"{scheme}://{host}/register?token={token}"
    return JsonResponse({"success": True, "link": link, "token": token})


@admin_only
@require_POST
def create_user(request):
    data = json.loads(request.body or "{}")
    username = (data.get("username") or "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")
    if len(username) < 3 or len(username) > 30:
        return _err("El usuario debe tener entre 3 y 30 caracteres")
    if not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        return _err("El usuario solo puede contener letras, números, _, . y -")
    try:
        validate_password(password)
    except ValidationError as exc:
        return _err(" ".join(exc.messages))
    if role not in ("admin", "user"):
        role = "user"
    if User.objects.filter(username__iexact=username).exists():
        return _err("Ese nombre de usuario ya existe")
    u = User.objects.create_user(username=username, password=password, role=role)
    _log(request.user, "admin_create_user", f"Creó usuario {username} con rol {role}")
    return JsonResponse({"success": True, "id": u.id, "username": username, "role": role})


@admin_only
@require_GET
def disk_usage(request):
    total = used = 0
    try:
        r = subprocess.run(["df", "-B1", "/"], capture_output=True, text=True, timeout=5, check=True)
        df = r.stdout.splitlines()[1].split()
        total, used = int(df[1]), int(df[2])
    except (subprocess.SubprocessError, IndexError, ValueError) as exc:
        log.warning("disk_usage: df fallo (%s)", exc)
    app_root = settings.BALUHOME_DATA_ROOT
    app_used = 0
    if app_root.exists():
        try:
            app_used = sum(f.stat().st_size for f in app_root.rglob("*") if f.is_file())
        except OSError as exc:
            log.warning("disk_usage: rglob fallo (%s)", exc)
    return JsonResponse({"vps_total": total, "vps_used": used, "app_used": app_used})


@admin_only
@require_GET
def activity(request):
    rows = ActivityLog.objects.order_by("-created_at")[:100].values(
        "id", "username", "action", "detail", "created_at"
    )
    return JsonResponse({"activity": list(rows)})
