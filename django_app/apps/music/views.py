"""App de música — biblioteca de canciones."""
import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST, require_GET

from apps.core.constants import YOUTUBE_DOMAINS
from .models import Song

log = logging.getLogger(__name__)


@login_required
def index(request):
    return render(request, "music/app.html", {})


@login_required
@require_GET
def fragment(request, page):
    if page == "library":
        q = (request.GET.get("search") or "").strip()
        songs = request.user.songs.all()
        if q:
            songs = songs.filter(Q(title__icontains=q) | Q(artist__icontains=q))
        songs_list = list(songs.values("id", "title", "artist", "thumbnail", "duration", "file_path"))
        return render(request, "music/fragments/library.html", {
            "songs": songs, "search": q,
            "songs_json": json.dumps(songs_list),
        })
    raise Http404


@login_required
@require_GET
def stream(request, song_id):
    song = get_object_or_404(Song, pk=song_id, user=request.user)
    try:
        return FileResponse(open(song.file_path, "rb"), content_type="audio/mpeg")
    except FileNotFoundError:
        raise Http404("Archivo no encontrado")


@login_required
@require_POST
def download(request):
    """Descarga audio de YouTube usando yt-dlp.

    Archivos: `<BALUHOME_UPLOADS_ROOT>/songs/<user_id>_<youtube_id>.mp3`
    (mismo patrón que se venía usando históricamente — aísla colecciones por usuario).
    """
    data = json.loads(request.body or "{}")
    url = (data.get("url") or "").strip()
    if not url or not any(d in url for d in YOUTUBE_DOMAINS):
        return JsonResponse({"error": "URL de YouTube inválida"}, status=400)
    try:
        import yt_dlp
    except ImportError:
        return JsonResponse({"error": "yt-dlp no instalado"}, status=500)

    # Extracción previa (sin descargar) para obtener el id y poder reutilizar
    # archivos ya descargados por el mismo usuario.
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as probe:
            info = probe.extract_info(url, download=False)
    except Exception as exc:
        return JsonResponse({"error": f"No se pudo leer el vídeo: {exc}"}, status=400)
    yid = info.get("id")
    if not yid:
        return JsonResponse({"error": "No se pudo identificar el vídeo"}, status=400)
    if Song.objects.filter(user=request.user, youtube_id=yid).exists():
        return JsonResponse({"error": "Esta canción ya está en tu biblioteca"}, status=400)

    out_dir = settings.BALUHOME_UPLOADS_ROOT / "songs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{request.user.id}_{yid}"
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / f"{stem}.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        "quiet": True, "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        return JsonResponse({"error": f"Error al descargar: {exc}"}, status=500)

    file_path = str(out_dir / f"{stem}.mp3")
    s = Song.objects.create(
        user=request.user,
        title=info.get("title", "Unknown"),
        artist=info.get("uploader", "Unknown Artist"),
        youtube_url=url, youtube_id=yid,
        file_path=file_path,
        thumbnail=info.get("thumbnail", ""),
        duration=info.get("duration", 0),
    )
    return JsonResponse({"success": True, "song": {
        "id": s.id, "title": s.title, "artist": s.artist,
        "thumbnail": s.thumbnail, "duration": s.duration,
    }})


@login_required
@require_POST
def delete_song(request):
    data = json.loads(request.body or "{}")
    song = get_object_or_404(Song, pk=data.get("song_id"), user=request.user)
    try:
        Path(song.file_path).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("delete_song: failed to unlink %s: %s", song.file_path, exc)
    song.delete()
    return JsonResponse({"success": True})
