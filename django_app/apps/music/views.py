"""App de música — biblioteca de canciones."""
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST, require_GET

from .models import Song


@login_required
def index(request):
    return render(request, "app.html", {})


@login_required
@require_GET
def fragment(request, page):
    if page == "library":
        q = (request.GET.get("search") or "").strip()
        songs = request.user.songs.all()
        if q:
            songs = songs.filter(Q(title__icontains=q) | Q(artist__icontains=q))
        songs_list = list(songs.values("id", "title", "artist", "thumbnail", "duration", "file_path"))
        return render(request, "fragments/library.html", {
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
    """Descarga audio de YouTube usando yt-dlp."""
    data = json.loads(request.body or "{}")
    url = (data.get("url") or "").strip()
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return JsonResponse({"error": "URL de YouTube inválida"}, status=400)
    try:
        import yt_dlp
    except ImportError:
        return JsonResponse({"error": "yt-dlp no instalado"}, status=500)
    out_dir = settings.BALUHOME_UPLOADS_ROOT / "songs"
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        "quiet": True, "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        return JsonResponse({"error": f"Error al descargar: {e}"}, status=500)
    yid = info.get("id")
    if Song.objects.filter(youtube_id=yid).exists():
        return JsonResponse({"error": "Esta canción ya está en tu biblioteca"}, status=400)
    file_path = str(out_dir / f"{yid}.mp3")
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
    except Exception:
        pass
    song.delete()
    return JsonResponse({"success": True})
