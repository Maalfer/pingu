"""'Mi nube' — árbol de archivos y carpetas por usuario.

Los archivos físicos viven en settings.BALUHOME_UPLOADS_ROOT/files/<user_id>/<file_id>.
Los metadatos (nombre, padre, mime, tamaño) están en la tabla FileNode.
"""
import logging
import re
import urllib.parse
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .models import FileNode

log = logging.getLogger(__name__)

ALLOWED_FOLDER_COLORS = {
    "#06b6d4", "#22c55e", "#eab308", "#f97316",
    "#ef4444", "#a855f7", "#ec4899", "#3b82f6",
    "#6b7280",
}
# Cap server-side; nginx debe permitir al menos esto en client_max_body_size.
MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1 GB


# ════════════ Helpers ════════════

def _files_root() -> Path:
    return settings.BALUHOME_UPLOADS_ROOT / "files"


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[\x00-\x1f/\\]+", "", name)
    return name[:200] or "Sin nombre"


def _serialize(node: FileNode) -> dict:
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "name": node.name,
        "is_folder": int(node.is_folder),  # mantener formato 0/1 que espera el JS
        "color": node.color,
        "storage_path": node.storage_path,
        "mime_type": node.mime_type,
        "size": node.size,
        "created_at": node.created_at.isoformat() if node.created_at else None,
    }


def _ancestors(node: FileNode) -> list:
    """Cadena de padres root → padre directo (excluye el propio nodo)."""
    chain = []
    current = node
    while current.parent_id:
        parent = FileNode.objects.filter(pk=current.parent_id, user=current.user).first()
        if not parent:
            break
        chain.append({"id": parent.id, "name": parent.name})
        current = parent
    chain.reverse()
    return chain


def _is_descendant(candidate_id: int, ancestor_id: int, user) -> bool:
    """True si candidate_id es ancestor_id o desciende de él."""
    if candidate_id == ancestor_id:
        return True
    current = FileNode.objects.filter(pk=candidate_id, user=user).first()
    while current and current.parent_id:
        if current.parent_id == ancestor_id:
            return True
        current = FileNode.objects.filter(pk=current.parent_id, user=user).first()
    return False


def _collect_storage_paths(root_node: FileNode, user) -> list:
    """Storage paths de root_node y todos sus descendientes (sólo archivos)."""
    paths = []
    stack_ids = [root_node.id]
    if not root_node.is_folder and root_node.storage_path:
        paths.append(root_node.storage_path)
    while stack_ids:
        parent_ids = stack_ids
        stack_ids = []
        children = FileNode.objects.filter(user=user, parent_id__in=parent_ids).values_list(
            "id", "is_folder", "storage_path"
        )
        for cid, is_folder, sp in children:
            if is_folder:
                stack_ids.append(cid)
            elif sp:
                paths.append(sp)
    return paths


def _err(msg, status=400):
    return JsonResponse({"error": msg}, status=status)


def _parse_json(request):
    import json
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


# ════════════ Views ════════════

@login_required
def index(request):
    return render(request, "files.html", {})


@login_required
@require_GET
def api_list(request):
    user = request.user
    try:
        parent_id = int(request.GET.get("parent_id") or 0)
    except ValueError:
        parent_id = 0
    parent = None
    if parent_id:
        parent = FileNode.objects.filter(pk=parent_id, user=user, is_folder=True).first()
        if not parent:
            return _err("Carpeta no encontrada", 404)
    qs = FileNode.objects.filter(user=user, parent=parent).order_by("-is_folder", "name")
    return JsonResponse({
        "success": True,
        "parent": {"id": parent.id, "name": parent.name} if parent else None,
        "ancestors": _ancestors(parent) if parent else [],
        "items": [_serialize(n) for n in qs],
    })


@login_required
@require_POST
def api_create_folder(request):
    data = _parse_json(request)
    name = _safe_name(data.get("name") or "")
    color = (data.get("color") or "").strip()
    if color and color not in ALLOWED_FOLDER_COLORS:
        color = ""
    parent_id = data.get("parent_id") or None
    parent = None
    if parent_id:
        parent = FileNode.objects.filter(pk=parent_id, user=request.user, is_folder=True).first()
        if not parent:
            return _err("Carpeta padre inválida")
    node = FileNode.objects.create(
        user=request.user, parent=parent, name=name, is_folder=True,
        color=color or None,
    )
    return JsonResponse({"success": True, "item": _serialize(node)})


@login_required
@require_POST
def api_upload(request):
    f = request.FILES.get("file")
    if not f:
        log.warning("upload: missing file field (user=%s, POST keys=%s, FILES keys=%s)",
                    request.user.id, list(request.POST.keys()), list(request.FILES.keys()))
        return _err("Falta archivo")
    if f.size == 0:
        return _err("Archivo vacío")
    if f.size > MAX_FILE_SIZE:
        log.info("upload: file too big (user=%s, size=%s, name=%r)",
                 request.user.id, f.size, f.name)
        return _err(f"Máximo {MAX_FILE_SIZE // (1024 * 1024)} MB por archivo")
    try:
        parent_id = int(request.POST.get("parent_id") or 0)
    except ValueError:
        parent_id = 0
    parent = None
    if parent_id:
        parent = FileNode.objects.filter(pk=parent_id, user=request.user, is_folder=True).first()
        if not parent:
            log.warning("upload: invalid parent_id=%s for user=%s", parent_id, request.user.id)
            return _err("Carpeta padre inválida")

    name = _safe_name(f.name or "archivo")
    mime = f.content_type or "application/octet-stream"

    node = FileNode.objects.create(
        user=request.user, parent=parent, name=name,
        is_folder=False, mime_type=mime, size=f.size,
    )
    user_dir = _files_root() / str(request.user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    storage_path = user_dir / str(node.id)
    try:
        with open(storage_path, "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
    except OSError as exc:
        node.delete()
        try:
            storage_path.unlink(missing_ok=True)
        except OSError:
            pass
        return _err(f"Error guardando archivo: {exc}", 500)
    node.storage_path = str(storage_path)
    node.save(update_fields=["storage_path"])
    return JsonResponse({"success": True, "item": _serialize(node)})


@login_required
@require_POST
def api_rename(request):
    data = _parse_json(request)
    try:
        node_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        node_id = 0
    new_name = _safe_name(data.get("name") or "")
    if not node_id or not new_name:
        return _err("Datos inválidos")
    node = FileNode.objects.filter(pk=node_id, user=request.user).first()
    if not node:
        return _err("No encontrado", 404)
    node.name = new_name
    node.save(update_fields=["name"])
    return JsonResponse({"success": True, "name": new_name})


@login_required
@require_POST
def api_set_color(request):
    data = _parse_json(request)
    try:
        node_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        node_id = 0
    color = (data.get("color") or "").strip()
    if color and color not in ALLOWED_FOLDER_COLORS:
        return _err("Color no permitido")
    node = FileNode.objects.filter(pk=node_id, user=request.user, is_folder=True).first()
    if not node:
        return _err("Solo carpetas")
    node.color = color or None
    node.save(update_fields=["color"])
    return JsonResponse({"success": True, "color": node.color})


@login_required
@require_POST
def api_move(request):
    data = _parse_json(request)
    try:
        node_id = int(data.get("id") or 0)
        new_parent_id = int(data.get("parent_id") or 0) or None
    except (TypeError, ValueError):
        return _err("Datos inválidos")
    node = FileNode.objects.filter(pk=node_id, user=request.user).first()
    if not node:
        return _err("No encontrado", 404)
    new_parent = None
    if new_parent_id:
        new_parent = FileNode.objects.filter(
            pk=new_parent_id, user=request.user, is_folder=True
        ).first()
        if not new_parent:
            return _err("Destino inválido")
        if node.is_folder and _is_descendant(new_parent_id, node_id, request.user):
            return _err("No puedes mover una carpeta dentro de sí misma")
    node.parent = new_parent
    node.save(update_fields=["parent"])
    return JsonResponse({"success": True})


@login_required
@require_POST
def api_delete(request):
    data = _parse_json(request)
    try:
        node_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        return _err("Datos inválidos")
    node = FileNode.objects.filter(pk=node_id, user=request.user).first()
    if not node:
        return _err("No encontrado", 404)
    storage_paths = _collect_storage_paths(node, request.user)
    # CASCADE en el modelo se encarga de los descendientes en la DB.
    node.delete()
    for p in storage_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    return JsonResponse({"success": True})


def _node_for_serve(request, file_id) -> tuple[FileNode, Path]:
    node = FileNode.objects.filter(pk=file_id, user=request.user, is_folder=False).first()
    if not node or not node.storage_path:
        raise Http404("No encontrado")
    path = Path(node.storage_path)
    if not path.exists():
        raise Http404("Archivo no encontrado en disco")
    return node, path


@login_required
@require_GET
def api_download(request, file_id):
    """Descarga forzada (`attachment`). Sin Range, suficiente para descargas."""
    node, path = _node_for_serve(request, file_id)
    quoted = urllib.parse.quote(node.name)

    def iterfile():
        with open(path, "rb") as fp:
            while True:
                chunk = fp.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    resp = StreamingHttpResponse(
        iterfile(),
        content_type=node.mime_type or "application/octet-stream",
    )
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quoted}"
    resp["Content-Length"] = str(path.stat().st_size)
    return resp


@login_required
@require_GET
def api_view(request, file_id):
    """Sirve el archivo `inline` con soporte de Range para reproducción/seeking.

    Usado por el reproductor de video/audio del frontend. `FileResponse` añade
    Accept-Ranges y maneja peticiones parciales transparentemente.
    """
    node, path = _node_for_serve(request, file_id)
    resp = FileResponse(
        open(path, "rb"),
        content_type=node.mime_type or "application/octet-stream",
        as_attachment=False,
        filename=node.name,
    )
    resp["Accept-Ranges"] = "bytes"
    return resp
