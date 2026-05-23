"""Videoteca: descarga de torrents en background con aria2c + streaming con range."""
import json
import logging
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponse, Http404, JsonResponse, StreamingHttpResponse,
)
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST, require_GET

from apps.core.api import error_response as _err
from apps.core.constants import (
    ARIA2_MAX_PEERS, ARIA2_STOP_TIMEOUT,
    MAX_VIDEO_TITLE_LENGTH, STREAM_CHUNK_SIZE,
)
from .models import Video

log = logging.getLogger(__name__)


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg"}
VIDEO_MIMES = {
    ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv", ".m4v": "video/mp4",
    ".ts": "video/mp2t", ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
}
_ARIA2_PROG_RE = re.compile(
    r"\[#[0-9a-fA-F]+\s+([\d.]+[KMGTP]?i?B)/([\d.]+[KMGTP]?i?B)\((\d+)%\)"
    r"(?:[^\]]*?DL:([\d.]+[KMGTP]?i?B))?(?:[^\]]*?ETA:([^\]\s]+))?[^\]]*\]"
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_stream_tokens: dict = {}


def _videos_root() -> Path:
    return settings.BALUHOME_VIDEOS_ROOT


def _find_video_file(directory: Path) -> Optional[Path]:
    candidates = [p for p in directory.rglob("*")
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
                  and not p.name.startswith(".")]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


def _extract_magnet_title(magnet: str) -> str:
    m = re.search(r"[&?]dn=([^&]+)", magnet)
    return urllib.parse.unquote_plus(m.group(1)) if m else "Vídeo"


def _video_duration(file_path: Path) -> int:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(file_path)],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            info = json.loads(out.stdout)
            return int(float(info.get("format", {}).get("duration", 0)))
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        log.debug("_video_duration_seconds: ffprobe error: %s", exc)
    return 0


def _aria2_last_error(log_path: Path) -> str:
    try:
        lines = [_ANSI_RE.sub("", l).strip()
                 for l in log_path.read_text(errors="replace").splitlines() if l.strip()]
        err = next((l for l in reversed(lines) if "ERROR" in l.upper() or "Exception" in l), None)
        return (err or (lines[-1] if lines else "Error desconocido"))[:300]
    except Exception:
        return "Error desconocido"


def _parse_aria2_progress(log_path: Path):
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    matches = _ARIA2_PROG_RE.findall(tail)
    if not matches:
        return None
    done_h, total_h, pct, dl, eta = matches[-1]
    if total_h == "0B":
        return None
    return {
        "percent": int(pct),
        "downloaded_h": done_h, "total_h": total_h,
        "speed_h": (dl + "/s") if dl else None,
        "eta": eta or None,
    }


def _do_torrent_download(video_id: int, source: str, is_file_path: bool):
    download_dir = _videos_root() / str(video_id)
    download_dir.mkdir(parents=True, exist_ok=True)
    log_path = download_dir / ".aria2.log"
    args = [
        "aria2c", f"--dir={download_dir}", "--seed-time=0", "--file-allocation=none",
        "--max-connection-per-server=16", "--split=16", "--min-split-size=1M",
        "--enable-dht=true", "--enable-peer-exchange=true",
        "--follow-torrent=true", f"--bt-stop-timeout={ARIA2_STOP_TIMEOUT}", "--bt-tracker-timeout=60",
        f"--bt-max-peers={ARIA2_MAX_PEERS}", "--piece-length=1M", "--summary-interval=1",
        "--max-overall-download-limit=0", source,
    ]
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(args, stdout=logf, stderr=subprocess.STDOUT, timeout=7200)
        if proc.returncode != 0:
            Video.objects.filter(pk=video_id).update(
                status="error", error_msg=_aria2_last_error(log_path)
            )
            return
        video_file = _find_video_file(download_dir)
        if not video_file:
            Video.objects.filter(pk=video_id).update(
                status="error", error_msg="No se encontró archivo de vídeo"
            )
            return
        Video.objects.filter(pk=video_id).update(
            status="ready",
            file_path=str(video_file),
            title=video_file.stem.replace(".", " ").replace("_", " "),
            duration=_video_duration(video_file),
            size=video_file.stat().st_size,
        )
    except subprocess.TimeoutExpired:
        Video.objects.filter(pk=video_id).update(status="error", error_msg="Tiempo de descarga agotado")
    except Exception as e:
        Video.objects.filter(pk=video_id).update(status="error", error_msg=str(e)[:300])
    finally:
        if is_file_path:
            try:
                Path(source).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("torrent download cleanup failed for %s: %s", source, exc)


def _fetch_torrent_url(url: str) -> dict:
    TORRENT_MAGIC = b"d8:announce"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/x-bittorrent,application/octet-stream,*/*",
    }
    try:
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True, stream=True)
        if r.status_code == 403:
            return {"error": "El sitio bloquea descargas directas (403). Usa magnet o sube el .torrent."}
        if r.status_code != 200:
            return {"error": f"Error HTTP {r.status_code}"}
        data = b"".join(r.iter_content(1024 * 256))
        if not (data[:10] == TORRENT_MAGIC or data[:1] == b"d"):
            ct = r.headers.get("content-type", "")
            if "html" in ct or "text" in ct:
                return {"error": "El sitio devuelve HTML, no un .torrent."}
        _videos_root().mkdir(parents=True, exist_ok=True)
        tmp_path = _videos_root() / f"tmp_{secrets.token_hex(8)}.torrent"
        tmp_path.write_bytes(data)
        title = Path(url.split("?")[0].split("/")[-1]).stem.replace("_", " ").replace("+", " ")
        return {"path": str(tmp_path), "title": title or "Video"}
    except Exception as e:
        return {"error": f"Error al descargar: {str(e)[:100]}"}


# ════════════ Vistas ════════════

@login_required
def index(request):
    return render(request, "videos/videos.html", {"videos": request.user.videos.all()})


@login_required
@require_GET
def list_videos(request):
    qs = request.user.videos.values(
        "id", "title", "status", "duration", "size", "error_msg", "created_at"
    )
    return JsonResponse({"videos": list(qs)})


@login_required
@require_GET
def status(request, video_id):
    v = get_object_or_404(Video, pk=video_id, user=request.user)
    row = {
        "id": v.id, "title": v.title, "status": v.status,
        "duration": v.duration, "size": v.size, "error_msg": v.error_msg,
    }
    if v.status == "downloading":
        dl_dir = _videos_root() / str(v.id)
        row["downloaded_bytes"] = sum(
            f.stat().st_size for f in dl_dir.rglob("*") if f.is_file()
        ) if dl_dir.exists() else 0
        prog = _parse_aria2_progress(dl_dir / ".aria2.log")
        if prog:
            row.update(prog)
    return JsonResponse(row)


@login_required
@require_POST
def add(request):
    """Acepta multipart con torrent_file o JSON con magnet."""
    magnet = source = None
    title = "Vídeo"
    is_file_path = False
    content_type = request.headers.get("content-type", "")

    if "multipart" in content_type:
        magnet = (request.POST.get("magnet") or "").strip()
        f = request.FILES.get("torrent_file")
        if f and f.name:
            _videos_root().mkdir(parents=True, exist_ok=True)
            tmp_path = _videos_root() / f"tmp_{secrets.token_hex(8)}.torrent"
            with open(tmp_path, "wb") as fp:
                for chunk in f.chunks():
                    fp.write(chunk)
            source = str(tmp_path)
            is_file_path = True
            title = Path(f.name).stem
    else:
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _err("JSON inválido")
        magnet = (data.get("magnet") or "").strip()

    if not source and magnet:
        if not (magnet.startswith("magnet:") or magnet.startswith("http")):
            return _err("URL inválida. Usa magnet: o un .torrent")
        if magnet.startswith("magnet:"):
            source = magnet
            title = _extract_magnet_title(magnet)
        else:
            r = _fetch_torrent_url(magnet)
            if "error" in r:
                return _err(r["error"])
            source = r["path"]
            is_file_path = True
            title = r.get("title", "Video")
    if not source:
        return _err("Proporciona un enlace magnet o un archivo .torrent")

    v = Video.objects.create(
        user=request.user, title=title, status="downloading",
        torrent_source=magnet or Path(source).name,
    )
    # Background thread (daemon): la descarga sigue mientras se devuelve la response.
    threading.Thread(
        target=_do_torrent_download,
        args=(v.id, source, is_file_path),
        daemon=True,
    ).start()
    return JsonResponse({"success": True, "video_id": v.id, "title": title})


@login_required
@require_POST
def delete_video(request):
    try:
        data = json.loads(request.body or "{}")
        video_id = int(data.get("video_id") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _err("Datos inválidos")
    v = get_object_or_404(Video, pk=video_id, user=request.user)
    dl_dir = _videos_root() / str(v.id)
    if dl_dir.exists():
        shutil.rmtree(dl_dir, ignore_errors=True)
    v.delete()
    return JsonResponse({"success": True})


@login_required
@require_POST
def rename_video(request):
    try:
        data = json.loads(request.body or "{}")
        video_id = int(data.get("video_id") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _err("Datos inválidos")
    title = (data.get("title") or "").strip()
    if not title:
        return _err("Título vacío")
    v = get_object_or_404(Video, pk=video_id, user=request.user)
    v.title = title[:MAX_VIDEO_TITLE_LENGTH]
    v.save(update_fields=["title"])
    return JsonResponse({"success": True})


@login_required
@require_GET
def get_stream_token(request, video_id):
    v = get_object_or_404(Video, pk=video_id, user=request.user, status="ready")
    token = secrets.token_urlsafe(24)
    _stream_tokens[token] = (v.id, request.user.id, time.time() + 3600)
    return JsonResponse({"token": token})


def progress(request, video_id):  # legacy alias
    return status(request, video_id)


def stream(request, video_id):
    """Streaming con soporte de Range. Acepta cookie de sesión O token temporal (cast)."""
    user_id = None
    token = request.GET.get("token", "")
    if token:
        entry = _stream_tokens.get(token)
        if entry and entry[0] == video_id and entry[2] > time.time():
            user_id = entry[1]
    if user_id is None:
        if not request.user.is_authenticated:
            return JsonResponse({"error": "No autenticado"}, status=401)
        user_id = request.user.id

    v = Video.objects.filter(pk=video_id, user_id=user_id).first()
    if not v:
        raise Http404
    if v.status != "ready" or not v.file_path:
        return JsonResponse({"error": "El vídeo aún no está listo"}, status=409)
    file_path = Path(v.file_path)
    if not file_path.exists():
        raise Http404("Archivo no encontrado")

    suffix = file_path.suffix.lower()
    mime = VIDEO_MIMES.get(suffix, "video/mp4")
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range") or request.META.get("HTTP_RANGE", "")
    start, end = 0, file_size - 1
    status_code = 200
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if m:
            s, e = m.group(1), m.group(2)
            start = int(s) if s else 0
            end = int(e) if e else file_size - 1
            end = min(end, file_size - 1)
            status_code = 206

    chunk_size = STREAM_CHUNK_SIZE

    def iterfile():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    resp = StreamingHttpResponse(iterfile(), status=status_code, content_type=mime)
    resp["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    resp["Accept-Ranges"] = "bytes"
    resp["Content-Length"] = str(end - start + 1)
    resp["Cache-Control"] = "no-store"
    return resp
