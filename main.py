"""
main.py — FastAPI application for BaluHome
"""
import asyncio
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from PIL import Image
from fastapi import FastAPI, Request, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware

from db import (
    get_db, init_db, verify_password, hash_password,
    UPLOADS_PATH, DB_PATH
)

# Temporales de subida en DISCO, no en /tmp (que es tmpfs/RAM): una bóveda de ~1 GB
# subida en streaming se escribe aquí antes de descomprimirse, sin agotar la RAM.
_UPLOAD_TMP = (Path(__file__).resolve().parent / "data" / "uploadtmp")
_UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_UPLOAD_TMP)

# Subidas grandes (import de bóvedas) entran por este origen, fuera de Cloudflare.
COOKIE_DOMAIN = ".fatimaymariosecasan.es"
CORS_ORIGINS = ["https://baluhome.fatimaymariosecasan.es",
                "https://uploads.fatimaymariosecasan.es"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)
# Cookie de sesión compartida en *.fatimaymariosecasan.es para que el import
# autenticado funcione contra uploads.fatimaymariosecasan.es (otro host).
app.add_middleware(SessionMiddleware, secret_key="baluhome-secret-change-in-prod",
                   session_cookie="balu_session", domain=COOKIE_DOMAIN,
                   max_age=60*60*24*365*2, https_only=True, same_site="lax")
# CORS para que la página (baluhome.…) pueda subir a uploads.… con credenciales.
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

ASSET_VERSION = "v21"

templates = Jinja2Templates(directory="templates")
templates.env.globals["ASSET_VERSION"] = ASSET_VERSION


@app.middleware("http")
async def inject_user_globals(request: Request, call_next):
    """Make session user info available to every template via request.state.

    Templates read these through the context processor below to render the
    shared header / hamburger drawer without each route having to pass them.
    """
    sess = request.scope.get("session") or {}
    request.state.bh_user_id = sess.get("user_id")
    request.state.bh_username = sess.get("username")
    request.state.bh_user_role = sess.get("user_role")
    return await call_next(request)


def _ctx(request: Request, **extra) -> dict:
    """Helper: build template context with shared keys merged in."""
    base = {
        "username": getattr(request.state, "bh_username", None),
        "user_id": getattr(request.state, "bh_user_id", None),
        "user_role": getattr(request.state, "bh_user_role", None),
    }
    base.update(extra)
    return base


@app.middleware("http")
async def sync_theme_cookie(request: Request, call_next):
    """Ensure the bh_theme cookie matches the user's stored theme on every page load.

    Without this, a user that has cleared cookies or logged in from a fresh browser
    would see the default dark theme until they re-pick one from settings.
    """
    response = await call_next(request)
    try:
        if request.url.path.startswith("/api/") or request.url.path.startswith("/static/"):
            return response
        sess = request.scope.get("session") or {}
        if "user_id" not in sess:
            return response
        cookie_theme = request.cookies.get("bh_theme")
        if cookie_theme in ("light", "dark", "dracula", "pink"):
            return response  # already set, no refresh needed
        conn = get_db()
        row = conn.execute("SELECT theme FROM users WHERE id = ?", (sess["user_id"],)).fetchone()
        conn.close()
        theme = (row and row["theme"]) or "dark"
        if theme not in ("light", "dark", "dracula", "pink"):
            theme = "dark"
        response.set_cookie("bh_theme", theme, max_age=2*365*86400, samesite="lax", path="/")
    except Exception:
        pass
    return response

_executor = ThreadPoolExecutor(max_workers=3)


def format_duration(seconds: int) -> str:
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


templates.env.filters["format_duration"] = format_duration


def is_valid_youtube_url(url: str) -> bool:
    pattern = r"^https?://(www\.)?(youtube\.com/(watch\?.*v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
    return bool(re.match(pattern, url))


def extract_youtube_id(url: str) -> Optional[str]:
    m = re.search(r"(?:youtube\.com/(?:watch\?.*v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else None


def row_to_dict(row) -> dict:
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]


def get_session(request: Request):
    if "user_id" not in request.session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return request.session


def get_admin_session(session):
    if session.get("user_role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    return session


def ensure_csrf(session, token: str) -> bool:
    return secrets.compare_digest(session.get("csrf_token", ""), token)


def get_or_create_csrf(session) -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


# ── AUTH ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    if "user_id" in request.session:
        return RedirectResponse("/home", status_code=302)
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(request, "login.html", {"csrf": csrf, "error": ""})


@app.post("/", response_class=HTMLResponse)
async def login_action(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    csrf = get_or_create_csrf(request.session)
    error = ""

    if not ensure_csrf(request.session, csrf_token):
        error = "Solicitud inválida."
    elif not username.strip() or not password:
        error = "Introduce usuario y contraseña."
    else:
        conn = get_db()
        row = conn.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
        conn.close()

        if row and verify_password(password, row["password_hash"]):
            request.session["user_id"] = row["id"]
            request.session["username"] = row["username"]
            request.session["user_role"] = row["role"]
            request.session["csrf_token"] = secrets.token_hex(32)
            log_activity(row["id"], row["username"], "inicio_sesion", "Inició sesión")
            conn2 = get_db()
            trow = conn2.execute("SELECT theme FROM users WHERE id = ?", (row["id"],)).fetchone()
            conn2.close()
            user_theme = trow["theme"] if trow and trow["theme"] in ("light", "dark") else "dark"
            resp = RedirectResponse("/home", status_code=302)
            resp.set_cookie("bh_theme", user_theme, max_age=2*365*86400, samesite="lax", path="/")
            return resp
        else:
            error = "Usuario o contraseña incorrectos."

    return templates.TemplateResponse(
        request, "login.html",
        {"csrf": csrf, "error": error, "username_val": username},
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, token: str = ""):
    if "user_id" in request.session:
        return RedirectResponse("/home", status_code=302)
    csrf = get_or_create_csrf(request.session)
    invite = None
    if token:
        conn = get_db()
        invite = conn.execute(
            "SELECT * FROM invite_tokens WHERE token = ? AND is_used = 0", (token,)
        ).fetchone()
        conn.close()
    return templates.TemplateResponse(
        request, "register.html",
        {"csrf": csrf, "token": token, "invalid": (token != "" and invite is None),
         "success": "", "error": "", "username_val": ""},
    )


@app.post("/register", response_class=HTMLResponse)
async def register_action(
    request: Request,
    token: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    csrf_token: str = Form(""),
):
    if "user_id" in request.session:
        return RedirectResponse("/home", status_code=302)

    csrf = get_or_create_csrf(request.session)
    error = ""
    success = ""

    conn = get_db()
    invite = conn.execute(
        "SELECT * FROM invite_tokens WHERE token = ? AND is_used = 0", (token,)
    ).fetchone()

    if not invite:
        conn.close()
        return templates.TemplateResponse(
            request, "register.html",
            {"csrf": csrf, "token": token, "invalid": True, "success": "", "error": "", "username_val": ""},
        )

    if not ensure_csrf(request.session, csrf_token):
        error = "Solicitud inválida."
    elif len(username.strip()) < 3 or len(username.strip()) > 30:
        error = "El usuario debe tener entre 3 y 30 caracteres."
    elif not re.match(r"^[a-zA-Z0-9_.-]+$", username.strip()):
        error = "El usuario solo puede contener letras, números, _, . y -"
    elif len(password) < 4:
        error = "La contraseña debe tener al menos 4 caracteres."
    elif password != password2:
        error = "Las contraseñas no coinciden."
    else:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if existing:
            error = "Ese nombre de usuario ya existe."
        else:
            try:
                hashed = hash_password(password)
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'user')",
                    (username.strip(), hashed),
                )
                new_user_id = cur.lastrowid
                conn.execute(
                    "UPDATE invite_tokens SET is_used = 1, used_by = ?, used_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_user_id, invite["id"]),
                )
                conn.commit()
                success = "¡Cuenta creada! Ahora puedes iniciar sesión."
            except Exception:
                conn.rollback()
                error = "Error al crear la cuenta. Inténtalo de nuevo."

    conn.close()
    return templates.TemplateResponse(
        request, "register.html",
        {"csrf": csrf, "token": token, "invalid": False, "success": success,
         "error": error, "username_val": username},
    )




def log_activity(user_id: int, username: str, action: str, detail: str = ""):
    """Log user activity. Cleanup entries older than 3 days."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO activity_logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
            (user_id, username, action, detail)
        )
        conn.execute("DELETE FROM activity_logs WHERE created_at < datetime('now', '-3 days')")
        conn.commit()
        conn.close()
    except Exception:
        pass

# ── HOME ──────────────────────────────────────────────────────────────────────

@app.get("/home", response_class=HTMLResponse)
async def home_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)

    user_id = request.session["user_id"]
    conn = get_db()

    pending_friends = conn.execute(
        "SELECT COUNT(*) FROM friendships WHERE addressee_id = ? AND status = 'pending'",
        (user_id,)
    ).fetchone()[0]

    shopping_count = conn.execute(
        "SELECT COUNT(*) FROM shopping_items WHERE done = 0"
    ).fetchone()[0]

    unread_msgs = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE receiver_id = ? AND read_at IS NULL",
        (user_id,)
    ).fetchone()[0]

    todos_count = conn.execute(
        "SELECT COUNT(*) FROM todos WHERE user_id = ? AND done = 0",
        (user_id,)
    ).fetchone()[0]

    videos_count = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE user_id = ? AND status = 'ready'",
        (user_id,)
    ).fetchone()[0]

    conn.close()

    csrf = get_or_create_csrf(request.session)

    return templates.TemplateResponse(
        request, "home.html",
        {
            "username": request.session["username"],
            "user_role": request.session["user_role"],
            "pending_friends": pending_friends,
            "shopping_count": shopping_count,
            "unread_msgs": unread_msgs,
            "todos_count": todos_count,
            "videos_count": videos_count,
            "csrf_token": csrf,
            "user_id": user_id,
        }
    )


# ── MUSIC APP ─────────────────────────────────────────────────────────────────

@app.get("/app", response_class=HTMLResponse)
async def app_shell(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "app.html",
        {"username": request.session["username"],
         "user_role": request.session["user_role"],
         "csrf_token": csrf,
         "user_id": request.session["user_id"]},
    )


# ── MUSIC FRAGMENTS ───────────────────────────────────────────────────────────

@app.get("/fragments/library", response_class=HTMLResponse)
async def fragment_library(request: Request, search: str = ""):
    if "user_id" not in request.session:
        if not request.headers.get("X-Fragment"):
            return RedirectResponse("/app", status_code=302)
        return Response(status_code=401)
    if not request.headers.get("X-Fragment"):
        return RedirectResponse("/app", status_code=302)

    user_id = request.session["user_id"]
    conn = get_db()
    search = search.strip()
    if search:
        like = f"%{search}%"
        rows = conn.execute(
            """SELECT s.*, u.username AS added_by
               FROM songs s JOIN users u ON s.user_id = u.id
               WHERE s.user_id = ? AND (s.title LIKE ? OR s.artist LIKE ?)
               ORDER BY s.created_at DESC""",
            (user_id, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT s.*, u.username AS added_by
               FROM songs s JOIN users u ON s.user_id = u.id
               WHERE s.user_id = ?
               ORDER BY s.created_at DESC""",
            (user_id,),
        ).fetchall()
    conn.close()
    songs = rows_to_list(rows)
    songs_json = json.dumps(songs)
    return templates.TemplateResponse(
        request, "fragments/library.html",
        {"songs": songs, "search": search, "songs_json": songs_json},
    )


# ── MUSIC API ─────────────────────────────────────────────────────────────────

@app.get("/api/stream/{song_id}")
async def stream_audio(song_id: int, request: Request):
    if "user_id" not in request.session:
        raise HTTPException(status_code=401)
    user_id = request.session["user_id"]
    user_role = request.session.get("user_role", "user")
    conn = get_db()
    song = row_to_dict(conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone())
    conn.close()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    if song["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    file_path = Path(song["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    start = 0
    end = file_size - 1
    status_code = 200
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store", "Content-Type": "audio/mpeg"}
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if m:
            start = int(m.group(1)) if m.group(1) else 0
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            status_code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    content_length = end - start + 1
    headers["Content-Length"] = str(content_length)
    def iterfile():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = content_length
            chunk_size = 65536
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    return StreamingResponse(iterfile(), status_code=status_code, headers=headers, media_type="audio/mpeg")


def _do_download(url: str, user_id: int) -> dict:
    youtube_id = extract_youtube_id(url)
    if not youtube_id:
        return {"error": "No se pudo extraer el ID de YouTube"}
    UPLOADS_PATH.mkdir(parents=True, exist_ok=True)
    out_base = UPLOADS_PATH / f"{user_id}_{youtube_id}"
    mp3_path = out_base.with_suffix(".mp3")
    thumbnail = f"https://img.youtube.com/vi/{youtube_id}/hqdefault.jpg"
    title = "Unknown Title"
    artist = "Unknown Artist"
    duration = 0
    ffmpeg_dir = shutil.which("ffmpeg")
    ffmpeg_dir = str(Path(ffmpeg_dir).parent) if ffmpeg_dir else "/opt/homebrew/bin"
    YT_BASE = [
        sys.executable, "-m", "yt_dlp", "--no-playlist",
        "--extractor-args", "youtube:player_client=tv_embedded",
        "--ffmpeg-location", ffmpeg_dir,
    ]
    try:
        meta_result = subprocess.run(YT_BASE + ["--dump-json", url],
            capture_output=True, text=True, timeout=60)
        if meta_result.returncode == 0 and meta_result.stdout.strip():
            meta = json.loads(meta_result.stdout.strip())
            title = meta.get("title", title)
            artist = meta.get("uploader") or meta.get("channel") or artist
            duration = int(meta.get("duration") or 0)
            if meta.get("thumbnail"):
                thumbnail = meta["thumbnail"]
    except Exception:
        pass
    try:
        dl_result = subprocess.run(
            YT_BASE + ["-x", "--audio-format", "mp3", "--audio-quality", "192",
                       "-o", str(out_base) + ".%(ext)s", url],
            capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"error": "Tiempo de espera agotado al descargar"}
    if not mp3_path.exists():
        candidates = list(UPLOADS_PATH.glob(f"{user_id}_{youtube_id}*.mp3"))
        if candidates:
            mp3_path = candidates[0]
    if not mp3_path.exists():
        err_lines = dl_result.stderr.strip().split("\n")
        last_lines = "\n".join(err_lines[-5:]) if err_lines else ""
        return {"error": f"Error al descargar. {last_lines}"}
    return {"success": True, "youtube_id": youtube_id, "title": title,
            "artist": artist, "thumbnail": thumbnail, "duration": duration,
            "file_path": str(mp3_path), "url": url}


@app.post("/api/download")
async def api_download(request: Request):
    session = get_session(request)
    data = await request.json()
    url = (data.get("url") or "").strip()
    csrf_token = data.get("csrf_token", "")
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    if not url:
        return JSONResponse({"error": "URL requerida"})
    if not is_valid_youtube_url(url):
        return JSONResponse({"error": "URL de YouTube inválida."})
    youtube_id = extract_youtube_id(url)
    if not youtube_id:
        return JSONResponse({"error": "No se pudo extraer el ID de YouTube"})
    user_id = session["user_id"]
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM songs WHERE youtube_id = ? AND user_id = ?", (youtube_id, user_id)
    ).fetchone()
    conn.close()
    if existing:
        return JSONResponse({"error": "Esta canción ya está en tu biblioteca"})
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _do_download, url, user_id)
    if "error" in result:
        return JSONResponse(result)
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO songs (user_id, title, artist, youtube_url, youtube_id, file_path, thumbnail, duration)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, result["title"], result["artist"], result["url"],
             result["youtube_id"], result["file_path"], result["thumbnail"], result["duration"]),
        )
        song_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        Path(result["file_path"]).unlink(missing_ok=True)
        return JSONResponse({"error": "Error al guardar en la base de datos"})
    finally:
        conn.close()
    return JSONResponse({"success": True, "song": {
        "id": song_id, "title": result["title"], "artist": result["artist"],
        "thumbnail": result["thumbnail"], "duration": result["duration"],
        "youtube_id": result["youtube_id"],
    }})


@app.post("/api/delete")
async def api_delete(request: Request):
    session = get_session(request)
    data = await request.json()
    song_id = int(data.get("song_id") or 0)
    csrf_token = data.get("csrf_token", "")
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    if not song_id:
        return JSONResponse({"error": "ID inválido"})
    conn = get_db()
    song = row_to_dict(conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone())
    if not song:
        conn.close()
        return JSONResponse({"error": "Canción no encontrada"}, status_code=404)
    user_id = session["user_id"]
    user_role = session.get("user_role", "user")
    if song["user_id"] != user_id and user_role != "admin":
        conn.close()
        return JSONResponse({"error": "Sin permisos"}, status_code=403)
    file_path = Path(song["file_path"])
    if file_path.exists():
        file_path.unlink()
    conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


def _make_invite_link(request: Request, token: str) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", str(request.base_url.hostname))
    return f"{proto}://{host}/register?token={token}"


@app.post("/api/invite")
async def api_invite(request: Request):
    session = get_admin_session(get_session(request))
    data = await request.json()
    csrf_token = data.get("csrf_token", "")
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    token = secrets.token_hex(24)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("INSERT INTO invite_tokens (token, created_by) VALUES (?, ?)", (token, user_id))
    conn.commit()
    conn.close()
    link = _make_invite_link(request, token)
    return JSONResponse({"success": True, "link": link, "token": token})


@app.post("/api/update_user")
async def api_update_user(request: Request):
    session = get_session(request)
    data = await request.json()
    action = data.get("action", "")
    csrf_token = data.get("csrf_token", "")
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    conn = get_db()
    try:
        if action == "change_username":
            new_username = (data.get("new_username") or "").strip()
            if len(new_username) < 3 or len(new_username) > 30:
                return JSONResponse({"error": "El usuario debe tener entre 3 y 30 caracteres"})
            if not re.match(r"^[a-zA-Z0-9_.-]+$", new_username):
                return JSONResponse({"error": "Caracteres no válidos en el nombre de usuario"})
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE AND id != ?",
                (new_username, user_id),
            ).fetchone()
            if existing:
                return JSONResponse({"error": "Ese nombre de usuario ya existe"})
            conn.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user_id))
            conn.commit()
            session["username"] = new_username
            return JSONResponse({"success": True, "new_username": new_username})
        elif action == "change_password":
            current_pw = data.get("current_password", "")
            new_pw = data.get("new_password", "")
            new_pw2 = data.get("new_password2", "")
            if len(new_pw) < 4:
                return JSONResponse({"error": "La contraseña debe tener al menos 4 caracteres"})
            if new_pw != new_pw2:
                return JSONResponse({"error": "Las contraseñas no coinciden"})
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not verify_password(current_pw, row["password_hash"]):
                return JSONResponse({"error": "Contraseña actual incorrecta"})
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (hash_password(new_pw), user_id))
            conn.commit()
            return JSONResponse({"success": True})
        else:
            return JSONResponse({"error": "Acción no válida"})
    finally:
        conn.close()


# ── SHOPPING ─────────────────────────────────────────────────────────────────

@app.get("/shopping", response_class=HTMLResponse)
async def shopping_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    items = rows_to_list(conn.execute(
        "SELECT * FROM shopping_items ORDER BY done ASC, created_at DESC"
    ).fetchall())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "shopping.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "items": items, "csrf_token": csrf}
    )


@app.get("/api/shopping/list")
async def api_shopping_list(request: Request):
    if "user_id" not in request.session:
        raise HTTPException(status_code=401)
    conn = get_db()
    items = rows_to_list(conn.execute(
        "SELECT * FROM shopping_items ORDER BY done ASC, created_at DESC"
    ).fetchall())
    conn.close()
    return JSONResponse({"items": items})


@app.post("/api/shopping/add")
async def api_shopping_add(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "El texto no puede estar vacío"})
    if len(text) > 200:
        return JSONResponse({"error": "Texto demasiado largo"})
    user_id = session["user_id"]
    conn = get_db()
    username = session.get("username", "?")
    cur = conn.execute("INSERT INTO shopping_items (user_id, text, added_by_name) VALUES (?, ?, ?)", (user_id, text, username))
    item_id = cur.lastrowid
    conn.commit()
    conn.close()
    log_activity(user_id, username, "shopping_add", f"Añadió '{text}' a la lista de la compra")
    return JSONResponse({"success": True, "id": item_id, "text": text, "added_by_name": username})


@app.post("/api/shopping/toggle")
async def api_shopping_toggle(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    item_id = int(data.get("id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    item = row_to_dict(conn.execute(
        "SELECT * FROM shopping_items WHERE id = ?", (item_id,)
    ).fetchone())
    if not item:
        conn.close()
        return JSONResponse({"error": "Item no encontrado"}, status_code=404)
    new_done = 0 if item["done"] else 1
    conn.execute("UPDATE shopping_items SET done = ? WHERE id = ?", (new_done, item_id))
    conn.commit()
    conn.close()
    if new_done == 1:
        log_activity(user_id, session.get("username","?"), "shopping_done", f"Marcó '{item['text']}' como comprado")
    return JSONResponse({"success": True, "done": new_done})


@app.post("/api/shopping/delete")
async def api_shopping_delete(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    item_id = int(data.get("id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/shopping/clear-done")
async def api_shopping_clear_done(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("DELETE FROM shopping_items WHERE done = 1")
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


# ── TODOS ────────────────────────────────────────────────────────────────────

@app.get("/todos", response_class=HTMLResponse)
async def todos_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    items = rows_to_list(conn.execute(
        "SELECT * FROM todos WHERE user_id = ? ORDER BY done ASC, created_at DESC",
        (user_id,)
    ).fetchall())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "todos.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "items": items, "csrf_token": csrf}
    )


@app.post("/api/todos/add")
async def api_todos_add(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    title = (data.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "El título no puede estar vacío"})
    if len(title) > 200:
        return JSONResponse({"error": "Título demasiado largo"})
    user_id = session["user_id"]
    conn = get_db()
    cur = conn.execute("INSERT INTO todos (user_id, title) VALUES (?, ?)", (user_id, title))
    item_id = cur.lastrowid
    conn.commit()
    conn.close()
    log_activity(user_id, session.get("username","?"), "todo_add", f"Añadió tarea '{title}'")
    return JSONResponse({"success": True, "id": item_id, "title": title})


@app.post("/api/todos/toggle")
async def api_todos_toggle(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    item_id = int(data.get("id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    item = row_to_dict(conn.execute(
        "SELECT * FROM todos WHERE id = ? AND user_id = ?", (item_id, user_id)
    ).fetchone())
    if not item:
        conn.close()
        return JSONResponse({"error": "Tarea no encontrada"}, status_code=404)
    new_done = 0 if item["done"] else 1
    conn.execute("UPDATE todos SET done = ? WHERE id = ?", (new_done, item_id))
    conn.commit()
    conn.close()
    if new_done == 1:
        log_activity(user_id, session.get("username","?"), "todo_done", f"Completó tarea '{item['title']}'")
    return JSONResponse({"success": True, "done": new_done})


@app.post("/api/todos/delete")
async def api_todos_delete(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    item_id = int(data.get("id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("DELETE FROM todos WHERE id = ? AND user_id = ?", (item_id, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/todos/clear-done")
async def api_todos_clear_done(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("DELETE FROM todos WHERE user_id = ? AND done = 1", (user_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


# ── GASTOS ────────────────────────────────────────────────────────────────────

def _get_friends_with_balance(user_id: int, conn) -> list:
    friendships = rows_to_list(conn.execute("""
        SELECT f.id, f.requester_id, f.addressee_id, f.status, f.created_at,
               CASE WHEN f.requester_id = ? THEN u2.id ELSE u1.id END AS friend_id,
               CASE WHEN f.requester_id = ? THEN u2.username ELSE u1.username END AS friend_name
        FROM friendships f
        JOIN users u1 ON f.requester_id = u1.id
        JOIN users u2 ON f.addressee_id = u2.id
        WHERE (f.requester_id = ? OR f.addressee_id = ?) AND f.status = 'accepted'
    """, (user_id, user_id, user_id, user_id)).fetchall())

    for f in friendships:
        my_paid = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE friendship_id = ? AND user_id = ?",
            (f["id"], user_id)
        ).fetchone()[0]
        friend_paid = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE friendship_id = ? AND user_id = ?",
            (f["id"], f["friend_id"])
        ).fetchone()[0]
        f["balance"] = round(float(my_paid) - float(friend_paid), 2)
    return friendships


@app.get("/gastos", response_class=HTMLResponse)
async def gastos_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    friends = _get_friends_with_balance(user_id, conn)
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "gastos.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "friends": friends, "csrf_token": csrf}
    )


@app.get("/gastos/{friend_id}", response_class=HTMLResponse)
async def gastos_detail_page(request: Request, friend_id: int):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    friendship = row_to_dict(conn.execute("""
        SELECT f.* FROM friendships f
        WHERE ((f.requester_id = ? AND f.addressee_id = ?)
            OR (f.requester_id = ? AND f.addressee_id = ?))
        AND f.status = 'accepted'
    """, (user_id, friend_id, friend_id, user_id)).fetchone())
    if not friendship:
        conn.close()
        return RedirectResponse("/gastos", status_code=302)
    friend = row_to_dict(conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (friend_id,)
    ).fetchone())
    if not friend:
        conn.close()
        return RedirectResponse("/gastos", status_code=302)
    txns = rows_to_list(conn.execute("""
        SELECT t.*, u.username AS payer_name
        FROM transactions t JOIN users u ON t.user_id = u.id
        WHERE t.friendship_id = ? ORDER BY t.created_at ASC
    """, (friendship["id"],)).fetchall())
    balance = 0.0
    for t in txns:
        if t["user_id"] == user_id:
            balance += t["amount"]
        else:
            balance -= t["amount"]
        t["running_balance"] = round(balance, 2)
    balance = round(balance, 2)
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "gastos_detail.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "friend": friend, "friendship": friendship, "transactions": txns,
         "balance": balance, "csrf_token": csrf, "user_id": user_id}
    )



@app.post("/api/gastos/add-transaction")
async def api_gastos_add_transaction(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    friendship_id = int(data.get("friendship_id") or 0)
    paid_by = data.get("paid_by", "me")
    try:
        amount = float(data.get("amount") or 0)
    except (ValueError, TypeError):
        return JSONResponse({"error": "Cantidad inválida"})
    if amount <= 0:
        return JSONResponse({"error": "La cantidad debe ser mayor que 0"})
    if amount > 999999:
        return JSONResponse({"error": "Cantidad demasiado grande"})
    description = (data.get("description") or "").strip()
    if not description:
        return JSONResponse({"error": "Añade una descripción"})
    if len(description) > 200:
        return JSONResponse({"error": "Descripción demasiado larga"})
    user_id = session["user_id"]
    conn = get_db()
    friendship = row_to_dict(conn.execute(
        "SELECT * FROM friendships WHERE id = ? AND (requester_id = ? OR addressee_id = ?) AND status = 'accepted'",
        (friendship_id, user_id, user_id)
    ).fetchone())
    if not friendship:
        conn.close()
        return JSONResponse({"error": "Relación no encontrada"}, status_code=404)
    if paid_by == "me":
        payer_id = user_id
    else:
        payer_id = friendship["addressee_id"] if friendship["requester_id"] == user_id else friendship["requester_id"]
    cur = conn.execute(
        "INSERT INTO transactions (friendship_id, user_id, amount, description) VALUES (?, ?, ?, ?)",
        (friendship_id, payer_id, amount, description)
    )
    txn_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT created_at FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    created_at = row["created_at"] if row else ""
    conn.close()
    log_activity(user_id, session.get("username","?"), "gastos_txn", f"Registró {amount:.2f}€ por '{description}'")
    return JSONResponse({"success": True, "id": txn_id,
                         "amount": amount, "description": description,
                         "paid_by_me": (payer_id == user_id),
                         "created_at": created_at})


@app.post("/api/gastos/delete-transaction")
async def api_gastos_delete_transaction(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    transaction_id = int(data.get("id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    transaction = row_to_dict(conn.execute("""
        SELECT t.* FROM transactions t
        JOIN friendships f ON t.friendship_id = f.id
        WHERE t.id = ? AND (f.requester_id = ? OR f.addressee_id = ?)
    """, (transaction_id, user_id, user_id)).fetchone())
    if not transaction:
        conn.close()
        return JSONResponse({"error": "Transacción no encontrada"}, status_code=404)
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


# ── CALENDAR ─────────────────────────────────────────────────────────────────

@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    events = rows_to_list(conn.execute(
        "SELECT * FROM calendar_events WHERE user_id = ? ORDER BY month ASC, day ASC",
        (user_id,)
    ).fetchall())
    conn.close()
    today = date.today()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "calendar.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "events": events, "csrf_token": csrf,
         "today_day": today.day, "today_month": today.month, "today_year": today.year}
    )


def _parse_event_extras(data):
    description = (data.get("description") or "").strip()
    if len(description) > 1000:
        description = description[:1000]
    is_all_day = 1 if bool(data.get("is_all_day", True)) else 0
    start_time = (data.get("start_time") or "").strip() or None
    end_time = (data.get("end_time") or "").strip() or None
    time_re = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    if is_all_day:
        start_time = None
        end_time = None
    else:
        if not start_time or not time_re.match(start_time):
            return None, "Hora de inicio inválida"
        if end_time and not time_re.match(end_time):
            return None, "Hora de fin inválida"
        if end_time and end_time <= start_time:
            return None, "La hora de fin debe ser posterior a la de inicio"
    return (description, is_all_day, start_time, end_time), None


@app.post("/api/calendar/add")
async def api_calendar_add(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    title = (data.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "El título es obligatorio"})
    if len(title) > 100:
        return JSONResponse({"error": "Título demasiado largo"})
    try:
        day = int(data.get("day") or 0)
        month = int(data.get("month") or 0)
        color = (data.get("color") or "#06b6d4").strip()
    except (ValueError, TypeError):
        return JSONResponse({"error": "Datos inválidos"})
    if not (1 <= day <= 31) or not (1 <= month <= 12):
        return JSONResponse({"error": "Fecha inválida"})
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = "#06b6d4"
    extras, err = _parse_event_extras(data)
    if err:
        return JSONResponse({"error": err})
    description, is_all_day, start_time, end_time = extras
    user_id = session["user_id"]
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO calendar_events (user_id, title, day, month, color, description, is_all_day, start_time, end_time) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, title, day, month, color, description, is_all_day, start_time, end_time)
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return JSONResponse({"success": True, "id": event_id})


@app.post("/api/calendar/delete")
async def api_calendar_delete(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    event_id = int(data.get("id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("DELETE FROM calendar_events WHERE id = ? AND user_id = ?", (event_id, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/calendar/update")
async def api_calendar_update(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    event_id = int(data.get("id") or 0)
    title = (data.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "El título es obligatorio"})
    try:
        day = int(data.get("day") or 0)
        month = int(data.get("month") or 0)
        color = (data.get("color") or "#06b6d4").strip()
    except (ValueError, TypeError):
        return JSONResponse({"error": "Datos inválidos"})
    if not (1 <= day <= 31) or not (1 <= month <= 12):
        return JSONResponse({"error": "Fecha inválida"})
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = "#06b6d4"
    extras, err = _parse_event_extras(data)
    if err:
        return JSONResponse({"error": err})
    description, is_all_day, start_time, end_time = extras
    user_id = session["user_id"]
    conn = get_db()
    conn.execute(
        "UPDATE calendar_events SET title=?, day=?, month=?, color=?, description=?, is_all_day=?, start_time=?, end_time=? "
        "WHERE id=? AND user_id=?",
        (title, day, month, color, description, is_all_day, start_time, end_time, event_id, user_id)
    )
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})




# ── FILES (Drive-like app) ───────────────────────────────────────────────────

FILES_ROOT = Path("uploads/files")
ALLOWED_FOLDER_COLORS = {
    "#06b6d4", "#22c55e", "#eab308", "#f97316",
    "#ef4444", "#a855f7", "#ec4899", "#3b82f6",
    "#6b7280",
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per file


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[\x00-\x1f/\\]+", "", name)
    name = name[:200]
    return name or "Sin nombre"


def _files_get_node(conn, user_id: int, node_id: int):
    if not node_id:
        return None
    row = conn.execute(
        "SELECT * FROM files WHERE id = ? AND user_id = ?",
        (node_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def _files_ancestors(conn, user_id: int, node_id: int):
    """List of ancestor folders (root → current). Excludes the node itself."""
    chain = []
    current = _files_get_node(conn, user_id, node_id)
    while current and current.get("parent_id"):
        parent = _files_get_node(conn, user_id, current["parent_id"])
        if not parent:
            break
        chain.append({"id": parent["id"], "name": parent["name"]})
        current = parent
    chain.reverse()
    return chain


def _files_is_descendant(conn, user_id: int, candidate_id: int, ancestor_id: int) -> bool:
    """True if candidate_id is ancestor_id or a descendant of it."""
    if candidate_id == ancestor_id:
        return True
    current = _files_get_node(conn, user_id, candidate_id)
    while current and current.get("parent_id"):
        if current["parent_id"] == ancestor_id:
            return True
        current = _files_get_node(conn, user_id, current["parent_id"])
    return False


def _files_collect_storage(conn, user_id: int, node_id: int):
    """Return list of storage_path strings for node and all descendants (files only)."""
    paths = []
    stack = [node_id]
    while stack:
        cur = stack.pop()
        node = _files_get_node(conn, user_id, cur)
        if not node:
            continue
        if node["is_folder"]:
            children = conn.execute(
                "SELECT id FROM files WHERE user_id = ? AND parent_id = ?",
                (user_id, cur)
            ).fetchall()
            stack.extend(c["id"] for c in children)
        elif node.get("storage_path"):
            paths.append(node["storage_path"])
    return paths


@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "files.html",
        {"username": request.session["username"],
         "user_role": request.session["user_role"],
         "csrf_token": csrf}
    )


@app.get("/api/files/list")
async def api_files_list(request: Request, parent_id: int = 0):
    session = get_session(request)
    user_id = session["user_id"]
    conn = get_db()
    parent = None
    if parent_id:
        parent = _files_get_node(conn, user_id, parent_id)
        if not parent or not parent["is_folder"]:
            conn.close()
            return JSONResponse({"error": "Carpeta no encontrada"}, status_code=404)
    if parent_id:
        rows = conn.execute(
            "SELECT * FROM files WHERE user_id = ? AND parent_id = ? "
            "ORDER BY is_folder DESC, name COLLATE NOCASE ASC",
            (user_id, parent_id)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM files WHERE user_id = ? AND parent_id IS NULL "
            "ORDER BY is_folder DESC, name COLLATE NOCASE ASC",
            (user_id,)
        ).fetchall()
    ancestors = _files_ancestors(conn, user_id, parent_id) if parent_id else []
    conn.close()
    items = [dict(r) for r in rows]
    return JSONResponse({
        "success": True,
        "parent": {"id": parent["id"], "name": parent["name"]} if parent else None,
        "ancestors": ancestors,
        "items": items,
    })


@app.post("/api/files/create-folder")
async def api_files_create_folder(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    name = _safe_name(data.get("name") or "")
    color = (data.get("color") or "").strip()
    if color and color not in ALLOWED_FOLDER_COLORS:
        color = ""
    parent_id = int(data.get("parent_id") or 0) or None
    conn = get_db()
    if parent_id:
        parent = _files_get_node(conn, user_id, parent_id)
        if not parent or not parent["is_folder"]:
            conn.close()
            return JSONResponse({"error": "Carpeta padre inválida"}, status_code=400)
    cur = conn.execute(
        "INSERT INTO files (user_id, parent_id, name, is_folder, color) VALUES (?, ?, ?, 1, ?)",
        (user_id, parent_id, name, color or None)
    )
    new_id = cur.lastrowid
    conn.commit()
    row = dict(conn.execute("SELECT * FROM files WHERE id = ?", (new_id,)).fetchone())
    conn.close()
    return JSONResponse({"success": True, "item": row})


@app.post("/api/files/upload")
async def api_files_upload(
    request: Request,
    file: UploadFile = File(...),
    parent_id: int = Form(0),
    csrf_token: str = Form(""),
):
    session = get_session(request)
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    parent_db_id = parent_id or None
    conn = get_db()
    if parent_db_id:
        parent = _files_get_node(conn, user_id, parent_db_id)
        if not parent or not parent["is_folder"]:
            conn.close()
            return JSONResponse({"error": "Carpeta padre inválida"}, status_code=400)

    contents = await file.read()
    if len(contents) == 0:
        conn.close()
        return JSONResponse({"error": "Archivo vacío"}, status_code=400)
    if len(contents) > MAX_FILE_SIZE:
        conn.close()
        return JSONResponse({"error": f"Máximo {MAX_FILE_SIZE // (1024*1024)} MB por archivo"}, status_code=400)

    name = _safe_name(file.filename or "archivo")
    mime = file.content_type or "application/octet-stream"

    cur = conn.execute(
        "INSERT INTO files (user_id, parent_id, name, is_folder, mime_type, size) "
        "VALUES (?, ?, ?, 0, ?, ?)",
        (user_id, parent_db_id, name, mime, len(contents))
    )
    new_id = cur.lastrowid

    user_dir = FILES_ROOT / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    storage_path = user_dir / str(new_id)
    try:
        storage_path.write_bytes(contents)
    except Exception as e:
        conn.execute("DELETE FROM files WHERE id = ?", (new_id,))
        conn.commit()
        conn.close()
        return JSONResponse({"error": f"Error guardando archivo: {e}"}, status_code=500)

    conn.execute("UPDATE files SET storage_path = ? WHERE id = ?", (str(storage_path), new_id))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM files WHERE id = ?", (new_id,)).fetchone())
    conn.close()
    return JSONResponse({"success": True, "item": row})


@app.post("/api/files/rename")
async def api_files_rename(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    node_id = int(data.get("id") or 0)
    new_name = _safe_name(data.get("name") or "")
    if not node_id or not new_name:
        return JSONResponse({"error": "Datos inválidos"}, status_code=400)
    conn = get_db()
    node = _files_get_node(conn, user_id, node_id)
    if not node:
        conn.close()
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    conn.execute("UPDATE files SET name = ? WHERE id = ? AND user_id = ?",
                 (new_name, node_id, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True, "name": new_name})


@app.post("/api/files/set-color")
async def api_files_set_color(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    node_id = int(data.get("id") or 0)
    color = (data.get("color") or "").strip()
    if color and color not in ALLOWED_FOLDER_COLORS:
        return JSONResponse({"error": "Color no permitido"}, status_code=400)
    conn = get_db()
    node = _files_get_node(conn, user_id, node_id)
    if not node or not node["is_folder"]:
        conn.close()
        return JSONResponse({"error": "Solo carpetas"}, status_code=400)
    conn.execute("UPDATE files SET color = ? WHERE id = ? AND user_id = ?",
                 (color or None, node_id, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True, "color": color or None})


@app.post("/api/files/move")
async def api_files_move(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    node_id = int(data.get("id") or 0)
    new_parent = int(data.get("parent_id") or 0) or None
    conn = get_db()
    node = _files_get_node(conn, user_id, node_id)
    if not node:
        conn.close()
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    if new_parent:
        parent = _files_get_node(conn, user_id, new_parent)
        if not parent or not parent["is_folder"]:
            conn.close()
            return JSONResponse({"error": "Destino inválido"}, status_code=400)
        if node["is_folder"] and _files_is_descendant(conn, user_id, new_parent, node_id):
            conn.close()
            return JSONResponse({"error": "No puedes mover una carpeta dentro de sí misma"}, status_code=400)
    conn.execute("UPDATE files SET parent_id = ? WHERE id = ? AND user_id = ?",
                 (new_parent, node_id, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/files/delete")
async def api_files_delete(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    node_id = int(data.get("id") or 0)
    conn = get_db()
    node = _files_get_node(conn, user_id, node_id)
    if not node:
        conn.close()
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    storage_paths = _files_collect_storage(conn, user_id, node_id)
    conn.execute("DELETE FROM files WHERE id = ? AND user_id = ?", (node_id, user_id))
    conn.commit()
    conn.close()
    for p in storage_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    return JSONResponse({"success": True})


@app.get("/api/files/download/{file_id}")
async def api_files_download(request: Request, file_id: int):
    if "user_id" not in request.session:
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    user_id = request.session["user_id"]
    conn = get_db()
    node = _files_get_node(conn, user_id, file_id)
    conn.close()
    if not node or node["is_folder"] or not node.get("storage_path"):
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    path = Path(node["storage_path"])
    if not path.exists():
        return JSONResponse({"error": "Archivo no encontrado en disco"}, status_code=404)
    quoted = urllib.parse.quote(node["name"])
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
        "Content-Length": str(path.stat().st_size),
    }
    def iterfile():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
    return StreamingResponse(iterfile(), media_type=node.get("mime_type") or "application/octet-stream", headers=headers)


# ── PROFILE ──────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    user = row_to_dict(conn.execute(
        "SELECT id, username, theme FROM users WHERE id = ?", (user_id,)
    ).fetchone())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "settings.html",
        {"username": request.session["username"],
         "user_role": request.session["user_role"],
         "user": user, "csrf_token": csrf}
    )


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    user = row_to_dict(conn.execute(
        "SELECT id, username, role, created_at, profile_picture, theme FROM users WHERE id = ?",
        (user_id,)
    ).fetchone())
    # Friends data (same logic as /friends)
    all_users = rows_to_list(conn.execute(
        "SELECT id, username FROM users WHERE id != ? ORDER BY username", (user_id,)
    ).fetchall())
    friendships = rows_to_list(conn.execute(
        "SELECT id, requester_id, addressee_id, status FROM friendships WHERE requester_id=? OR addressee_id=?",
        (user_id, user_id)
    ).fetchall())
    conn.close()
    friendship_map = {}
    for f in friendships:
        other = f["addressee_id"] if f["requester_id"] == user_id else f["requester_id"]
        friendship_map[other] = f
    users_with_status = []
    for u in all_users:
        f = friendship_map.get(u["id"])
        if f:
            if f["status"] == "accepted":
                status = "friend"
            elif f["requester_id"] == user_id:
                status = "sent"
            else:
                status = "received"
            fid = f["id"]
        else:
            status = "none"
            fid = None
        users_with_status.append({"id": u["id"], "username": u["username"],
                                   "status": status, "friendship_id": fid})
    pending_count = sum(1 for u in users_with_status if u["status"] == "received")
    csrf = get_or_create_csrf(request.session)
    has_avatar = Path(f"static/avatars/{user_id}.jpg").exists()
    return templates.TemplateResponse(
        request, "profile.html",
        {"user": user, "csrf_token": csrf, "has_avatar": has_avatar,
         "user_role": user["role"], "username": user["username"],
         "friend_users": users_with_status, "pending_count": pending_count}
    )


@app.post("/api/profile/upload-picture")
async def api_profile_upload_picture(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(""),
):
    session = get_session(request)
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    if not file.content_type or not file.content_type.startswith("image/"):
        return JSONResponse({"error": "Solo se permiten imágenes"})
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        return JSONResponse({"error": "La imagen no puede superar 5 MB"})
    try:
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        img.thumbnail((256, 256), Image.LANCZOS)
        avatar_dir = Path("static/avatars")
        avatar_dir.mkdir(parents=True, exist_ok=True)
        out_path = avatar_dir / f"{user_id}.jpg"
        img.save(str(out_path), "JPEG", quality=88, optimize=True)
        rel_url = f"/static/avatars/{user_id}.jpg"
        conn = get_db()
        conn.execute("UPDATE users SET profile_picture = ? WHERE id = ?",
                     (rel_url, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        return JSONResponse({"error": f"Error procesando imagen: {e}"})
    return JSONResponse({"success": True, "url": rel_url})


@app.post("/api/profile/change-username")
async def api_profile_change_username(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    new_username = (data.get("username") or "").strip()
    if len(new_username) < 3 or len(new_username) > 30:
        return JSONResponse({"error": "El usuario debe tener entre 3 y 30 caracteres"})
    if not re.match(r"^[a-zA-Z0-9_.-]+$", new_username):
        return JSONResponse({"error": "Solo letras, números, _, . y -"})
    user_id = session["user_id"]
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE AND id != ?",
        (new_username, user_id)
    ).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"error": "Ese nombre de usuario ya existe"})
    conn.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user_id))
    conn.commit()
    conn.close()
    session["username"] = new_username
    return JSONResponse({"success": True, "username": new_username})


@app.post("/api/profile/set-theme")
async def api_profile_set_theme(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    theme = (data.get("theme") or "dark").strip().lower()
    if theme not in ("light", "dark", "dracula", "pink"):
        return JSONResponse({"error": "Tema inválido"}, status_code=400)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user_id))
    conn.commit()
    conn.close()
    resp = JSONResponse({"success": True, "theme": theme})
    resp.set_cookie("bh_theme", theme, max_age=2*365*86400, samesite="lax", path="/")
    return resp


@app.post("/api/profile/change-password")
async def api_profile_change_password(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    current_pw = data.get("current_password", "")
    new_pw     = data.get("new_password", "")
    if len(new_pw) < 4:
        return JSONResponse({"error": "La contraseña debe tener al menos 4 caracteres"})
    user_id = session["user_id"]
    conn = get_db()
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not verify_password(current_pw, row["password_hash"]):
        conn.close()
        return JSONResponse({"error": "Contraseña actual incorrecta"})
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (hash_password(new_pw), user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


# ── FRIENDS ──────────────────────────────────────────────────────────────────

@app.get("/friends", response_class=HTMLResponse)
async def friends_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    all_users = rows_to_list(conn.execute(
        "SELECT id, username FROM users WHERE id != ? ORDER BY username", (user_id,)
    ).fetchall())
    friendships = rows_to_list(conn.execute(
        "SELECT id, requester_id, addressee_id, status FROM friendships WHERE requester_id=? OR addressee_id=?",
        (user_id, user_id)
    ).fetchall())
    conn.close()
    friendship_map = {}
    for f in friendships:
        other = f["addressee_id"] if f["requester_id"] == user_id else f["requester_id"]
        friendship_map[other] = f
    users_with_status = []
    for u in all_users:
        f = friendship_map.get(u["id"])
        if f:
            if f["status"] == "accepted":
                status = "friend"
            elif f["requester_id"] == user_id:
                status = "sent"
            else:
                status = "received"
            fid = f["id"]
        else:
            status = "none"
            fid = None
        users_with_status.append({"id": u["id"], "username": u["username"],
                                   "status": status, "friendship_id": fid})
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "friends.html",
        {"users": users_with_status, "csrf_token": csrf,
         "username": request.session.get("username", ""),
         "user_role": request.session.get("user_role", "")}
    )



@app.get("/api/friends/list")
async def api_friends_list(request: Request):
    if "user_id" not in request.session:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    user_id = request.session["user_id"]
    conn = get_db()
    all_users = rows_to_list(conn.execute(
        "SELECT id, username FROM users WHERE id != ? ORDER BY username", (user_id,)
    ).fetchall())
    friendships = rows_to_list(conn.execute(
        "SELECT id, requester_id, addressee_id, status FROM friendships WHERE requester_id=? OR addressee_id=?",
        (user_id, user_id)
    ).fetchall())
    conn.close()
    friendship_map = {}
    for f in friendships:
        other = f["addressee_id"] if f["requester_id"] == user_id else f["requester_id"]
        friendship_map[other] = f
    result = []
    for u in all_users:
        f = friendship_map.get(u["id"])
        if f:
            if f["status"] == "accepted":
                status = "friend"
            elif f["requester_id"] == user_id:
                status = "sent"
            else:
                status = "received"
            fid = f["id"]
        else:
            status = "none"
            fid = None
        result.append({"id": u["id"], "username": u["username"],
                        "status": status, "friendship_id": fid})
    return JSONResponse({"users": result})

@app.get("/api/friends/pending-count")
async def api_friends_pending_count(request: Request):
    if "user_id" not in request.session:
        return JSONResponse({"count": 0})
    user_id = request.session["user_id"]
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM friendships WHERE addressee_id = ? AND status = 'pending'",
        (user_id,)
    ).fetchone()[0]
    conn.close()
    return JSONResponse({"count": count})


@app.post("/api/friends/send")
async def api_friends_send(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    target_id = int(data.get("user_id") or 0)
    conn = get_db()
    target = row_to_dict(conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (target_id,)
    ).fetchone())
    if not target:
        conn.close()
        return JSONResponse({"error": "Usuario no encontrado"})
    if target["id"] == user_id:
        conn.close()
        return JSONResponse({"error": "No puedes añadirte a ti mismo"})
    existing = row_to_dict(conn.execute(
        "SELECT * FROM friendships WHERE (requester_id=? AND addressee_id=?) OR (requester_id=? AND addressee_id=?)",
        (user_id, target_id, target_id, user_id)
    ).fetchone())
    if existing:
        conn.close()
        if existing["status"] == "accepted":
            return JSONResponse({"error": "Ya sois amigos"})
        return JSONResponse({"error": "Ya existe una solicitud pendiente"})
    conn.execute("INSERT INTO friendships (requester_id, addressee_id) VALUES (?, ?)",
                 (user_id, target_id))
    conn.commit()
    conn.close()
    log_activity(user_id, session.get("username","?"), "friend_request",
                 f"Envió solicitud de amistad a {target['username']}")
    return JSONResponse({"success": True})


@app.post("/api/friends/accept")
async def api_friends_accept(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    friendship_id = int(data.get("friendship_id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute(
        "UPDATE friendships SET status='accepted' WHERE id=? AND addressee_id=? AND status='pending'",
        (friendship_id, user_id)
    )
    conn.commit()
    conn.close()
    log_activity(user_id, session.get("username","?"), "friend_accept", "Aceptó una solicitud de amistad")
    return JSONResponse({"success": True})


@app.post("/api/friends/reject")
async def api_friends_reject(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    friendship_id = int(data.get("friendship_id") or 0)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute(
        "DELETE FROM friendships WHERE id=? AND (addressee_id=? OR requester_id=?)",
        (friendship_id, user_id, user_id)
    )
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


# ── CHAT ─────────────────────────────────────────────────────────────────────

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    # Only show users who are friends (accepted)
    all_users = rows_to_list(conn.execute("""
        SELECT CASE WHEN f.requester_id=? THEN u2.id ELSE u1.id END AS id,
               CASE WHEN f.requester_id=? THEN u2.username ELSE u1.username END AS username
        FROM friendships f
        JOIN users u1 ON f.requester_id=u1.id
        JOIN users u2 ON f.addressee_id=u2.id
        WHERE (f.requester_id=? OR f.addressee_id=?) AND f.status='accepted'
        ORDER BY username
    """, (user_id, user_id, user_id, user_id)).fetchall())
    conversations = []
    for u in all_users:
        last_msg = row_to_dict(conn.execute("""
            SELECT m.*,
                   CASE WHEN m.sender_id = ? THEN 'me' ELSE 'them' END AS direction
            FROM messages m
            WHERE (m.sender_id = ? AND m.receiver_id = ?)
               OR (m.sender_id = ? AND m.receiver_id = ?)
            ORDER BY m.created_at DESC LIMIT 1
        """, (user_id, user_id, u["id"], u["id"], user_id)).fetchone())
        unread = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender_id = ? AND receiver_id = ? AND read_at IS NULL",
            (u["id"], user_id)
        ).fetchone()[0]
        conversations.append({
            "user": u,
            "last_msg": last_msg,
            "unread": unread
        })
    conversations.sort(key=lambda x: (
        x["last_msg"]["created_at"] if x["last_msg"] else "0000"
    ), reverse=True)
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "chat.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "conversations": conversations, "csrf_token": csrf}
    )


@app.get("/chat/{other_id}", response_class=HTMLResponse)
async def chat_detail_page(request: Request, other_id: int):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    if other_id == user_id:
        return RedirectResponse("/chat", status_code=302)
    conn = get_db()
    other_user = row_to_dict(conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (other_id,)
    ).fetchone())
    if not other_user:
        conn.close()
        return RedirectResponse("/chat", status_code=302)
    conn.execute(
        "UPDATE messages SET read_at = CURRENT_TIMESTAMP WHERE sender_id = ? AND receiver_id = ? AND read_at IS NULL",
        (other_id, user_id)
    )
    conn.commit()
    messages = rows_to_list(conn.execute("""
        SELECT m.*, u.username AS sender_name
        FROM messages m JOIN users u ON m.sender_id = u.id
        WHERE (m.sender_id = ? AND m.receiver_id = ?)
           OR (m.sender_id = ? AND m.receiver_id = ?)
        ORDER BY m.created_at ASC
    """, (user_id, other_id, other_id, user_id)).fetchall())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "chat_detail.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "other_user": other_user, "messages": messages,
         "csrf_token": csrf, "user_id": user_id}
    )


@app.post("/api/chat/send")
async def api_chat_send(request: Request):
    session = get_session(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    receiver_id = int(data.get("receiver_id") or 0)
    content = (data.get("content") or "").strip()
    if not content:
        return JSONResponse({"error": "El mensaje no puede estar vacío"})
    if len(content) > 1000:
        return JSONResponse({"error": "Mensaje demasiado largo"})
    user_id = session["user_id"]
    if receiver_id == user_id:
        return JSONResponse({"error": "No puedes enviarte mensajes a ti mismo"})
    conn = get_db()
    receiver = conn.execute("SELECT id FROM users WHERE id = ?", (receiver_id,)).fetchone()
    if not receiver:
        conn.close()
        return JSONResponse({"error": "Usuario no encontrado"}, status_code=404)
    cur = conn.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
        (user_id, receiver_id, content)
    )
    msg_id = cur.lastrowid
    conn.commit()
    conn.close()
    # Push notification to receiver (fire-and-forget)
    asyncio.get_running_loop().run_in_executor(
        None,
        _send_push_sync_to_user,
        receiver_id,
        f"💬 {session.get('username', 'Alguien')}",
        content[:100],
        f"/chat/{session['user_id']}"
    )
    return JSONResponse({"success": True, "id": msg_id})


@app.get("/api/chat/messages/{other_id}")
async def api_chat_messages(request: Request, other_id: int, since: int = 0):
    if "user_id" not in request.session:
        raise HTTPException(status_code=401)
    user_id = request.session["user_id"]
    conn = get_db()
    conn.execute(
        "UPDATE messages SET read_at = CURRENT_TIMESTAMP WHERE sender_id = ? AND receiver_id = ? AND read_at IS NULL",
        (other_id, user_id)
    )
    conn.commit()
    messages = rows_to_list(conn.execute("""
        SELECT m.id, m.sender_id, m.content, m.created_at
        FROM messages m
        WHERE ((m.sender_id = ? AND m.receiver_id = ?)
            OR (m.sender_id = ? AND m.receiver_id = ?))
        AND m.id > ?
        ORDER BY m.created_at ASC
    """, (user_id, other_id, other_id, user_id, since)).fetchall())
    conn.close()
    return JSONResponse({"messages": messages, "user_id": user_id})


# ── ADMIN PANEL ───────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    if request.session.get("user_role") != "admin":
        return RedirectResponse("/home", status_code=302)
    conn = get_db()
    users = rows_to_list(conn.execute("""
        SELECT u.id, u.username, u.role, u.created_at,
               COUNT(DISTINCT s.id) AS song_count
        FROM users u LEFT JOIN songs s ON s.user_id = u.id
        GROUP BY u.id ORDER BY u.created_at ASC
    """).fetchall())
    invites = rows_to_list(conn.execute("""
        SELECT i.*, u1.username AS creator, u2.username AS used_by_name
        FROM invite_tokens i JOIN users u1 ON i.created_by = u1.id
        LEFT JOIN users u2 ON i.used_by = u2.id
        ORDER BY i.created_at DESC LIMIT 50
    """).fetchall())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "admin_panel.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "users": users, "invites": invites, "csrf_token": csrf,
         "current_user_id": request.session["user_id"]}
    )


@app.post("/api/admin/delete-user")
async def api_admin_delete_user(request: Request):
    session = get_admin_session(get_session(request))
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    target_id = int(data.get("user_id") or 0)
    if target_id == session["user_id"]:
        return JSONResponse({"error": "No puedes eliminarte a ti mismo"})
    conn = get_db()
    try:
        songs = rows_to_list(conn.execute("SELECT file_path FROM songs WHERE user_id = ?", (target_id,)).fetchall())
        for song in songs:
            try:
                Path(song["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
        # invite_tokens has no ON DELETE CASCADE — clear refs manually
        conn.execute("UPDATE invite_tokens SET used_by = NULL WHERE used_by = ?", (target_id,))
        conn.execute("DELETE FROM invite_tokens WHERE created_by = ?", (target_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (target_id,))
        conn.commit()
        log_activity(session["user_id"], session.get("username","?"), "admin_delete_user",
                     f"Eliminó usuario ID {target_id}")
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": f"Error al eliminar: {e}"})
    finally:
        conn.close()
    return JSONResponse({"success": True})


@app.post("/api/admin/change-user-password")
async def api_admin_change_password(request: Request):
    session = get_admin_session(get_session(request))
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    target_id = int(data.get("user_id") or 0)
    new_password = data.get("new_password", "")
    if len(new_password) < 4:
        return JSONResponse({"error": "La contraseña debe tener al menos 4 caracteres"})
    conn = get_db()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (hash_password(new_password), target_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/admin/change-user-username")
async def api_admin_change_username(request: Request):
    session = get_admin_session(get_session(request))
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    target_id = int(data.get("user_id") or 0)
    new_username = (data.get("new_username") or "").strip()
    if len(new_username) < 3 or len(new_username) > 30:
        return JSONResponse({"error": "El usuario debe tener entre 3 y 30 caracteres"})
    if not re.match(r"^[a-zA-Z0-9_.-]+$", new_username):
        return JSONResponse({"error": "Caracteres no válidos"})
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE AND id != ?",
        (new_username, target_id)
    ).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"error": "Ese nombre de usuario ya existe"})
    conn.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, target_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True, "new_username": new_username})


@app.post("/api/admin/change-user-role")
async def api_admin_change_role(request: Request):
    session = get_admin_session(get_session(request))
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    target_id = int(data.get("user_id") or 0)
    new_role = data.get("new_role", "")
    if new_role not in ("admin", "user"):
        return JSONResponse({"error": "Rol inválido"})
    if target_id == session["user_id"]:
        return JSONResponse({"error": "No puedes cambiar tu propio rol"})
    conn = get_db()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, target_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/admin/create-invite")
async def api_admin_create_invite(request: Request):
    session = get_admin_session(get_session(request))
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    token = secrets.token_hex(24)
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("INSERT INTO invite_tokens (token, created_by) VALUES (?, ?)", (token, user_id))
    conn.commit()
    conn.close()
    link = _make_invite_link(request, token)
    return JSONResponse({"success": True, "link": link, "token": token})


@app.post("/api/admin/create-user")
async def api_admin_create_user(request: Request):
    session = get_admin_session(get_session(request))
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    username = (data.get("username") or "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")
    if len(username) < 3 or len(username) > 30:
        return JSONResponse({"error": "El usuario debe tener entre 3 y 30 caracteres"})
    if not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        return JSONResponse({"error": "El usuario solo puede contener letras, números, _, . y -"})
    if len(password) < 4:
        return JSONResponse({"error": "La contraseña debe tener al menos 4 caracteres"})
    if role not in ("admin", "user"):
        role = "user"
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"error": "Ese nombre de usuario ya existe"})
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hash_password(password), role)
        )
        new_id = cur.lastrowid
        conn.commit()
        log_activity(session["user_id"], session.get("username","?"), "admin_create_user",
                     f"Creó usuario {username} con rol {role}")
    except Exception as e:
        conn.rollback()
        conn.close()
        return JSONResponse({"error": f"Error al crear: {e}"})
    conn.close()
    return JSONResponse({"success": True, "id": new_id, "username": username, "role": role})


@app.get("/api/admin/disk-usage")
async def api_admin_disk_usage(request: Request):
    session = get_admin_session(get_session(request))
    # VPS total / used
    df = subprocess.check_output(["df", "-B1", "/"], text=True).splitlines()[1].split()
    total_bytes = int(df[1])
    used_bytes  = int(df[2])
    # App usage
    try:
        app_bytes = int(subprocess.check_output(["du", "-sb", "/var/www/balusong"], text=True).split()[0])
    except Exception:
        app_bytes = 0
    def fmt(b):
        for unit in ("B","KB","MB","GB"):
            if b < 1024: return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"
    return JSONResponse({
        "success": True,
        "total": total_bytes,
        "used": used_bytes,
        "app": app_bytes,
        "total_fmt": fmt(total_bytes),
        "used_fmt": fmt(used_bytes),
        "app_fmt": fmt(app_bytes),
        "used_pct": round(used_bytes / total_bytes * 100, 1),
        "app_pct": round(app_bytes / total_bytes * 100, 2),
    })

# ── PWA ───────────────────────────────────────────────────────────────────────

@app.get("/manifest.json")
async def serve_manifest():
    manifest = {
        "name": "BaluHome",
        "short_name": "BaluHome",
        "description": "Tu plataforma personal",
        "start_url": "/home",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "orientation": "portrait",
        "background_color": "#0a0a0f",
        "theme_color": "#0a0a0f",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "categories": ["utilities", "entertainment"],
        "lang": "es",
        "dir": "ltr",
        "prefer_related_applications": False,
        "shortcuts": [
            {"name": "Calendario", "url": "/calendar", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
            {"name": "Archivos", "url": "/files", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
            {"name": "Chat", "url": "/chat", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
        ],
    }
    return JSONResponse(manifest)


@app.get("/sw.js")
async def serve_sw():
    sw_content = """/* BaluHome Service Worker v11 */
const CACHE = 'baluhome-v11';

self.addEventListener('install', e => {
  e.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/fragments/')) return;

  // HTML + CSS: always network first, fall back to cache only if offline
  const isHtml = e.request.headers.get('Accept')?.includes('text/html');
  const isCss = url.pathname.endsWith('.css');
  if (isHtml || isCss) {
    e.respondWith(
      fetch(e.request, { cache: 'no-store' })
        .then(res => {
          if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Other assets: stale-while-revalidate
  e.respondWith(caches.match(e.request).then(cached => {
    const network = fetch(e.request).then(res => {
      if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
      return res;
    }).catch(() => cached);
    return cached || network;
  }));
});

// ── PUSH NOTIFICATIONS ────────────────────────────────────────────────────────
self.addEventListener('push', e => {
  let data = { title: 'BaluHome', body: 'Nueva notificación', url: '/' };
  try { data = { ...data, ...e.data.json() }; } catch {}
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      tag: data.url,
      renotify: true,
      data: { url: data.url },
      vibrate: [100, 50, 100],
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      const match = list.find(c => c.url.includes(self.location.origin) && 'focus' in c);
      if (match) return match.focus().then(c => c.navigate(url));
      return clients.openWindow(url);
    })
  );
});
"""
    return Response(content=sw_content, media_type="application/javascript")


@app.get("/api/admin/activity")
async def api_admin_activity(request: Request):
    if "user_id" not in request.session:
        raise HTTPException(status_code=401)
    if request.session.get("user_role") != "admin":
        raise HTTPException(status_code=403)
    conn = get_db()
    logs = rows_to_list(conn.execute(
        "SELECT id, username, action, detail, created_at FROM activity_logs ORDER BY created_at DESC LIMIT 100"
    ).fetchall())
    conn.close()
    return JSONResponse({"logs": logs})


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO APP
# ══════════════════════════════════════════════════════════════════════════════

VIDEOS_PATH = Path("uploads/videos")
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mpg', '.mpeg'}
VIDEO_MIMES = {
    '.mp4': 'video/mp4', '.mkv': 'video/x-matroska', '.webm': 'video/webm',
    '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
    '.wmv': 'video/x-ms-wmv', '.flv': 'video/x-flv', '.m4v': 'video/mp4',
    '.ts': 'video/mp2t', '.mpg': 'video/mpeg', '.mpeg': 'video/mpeg',
}


def _find_video_file(directory: Path) -> Optional[Path]:
    candidates = [p for p in directory.rglob("*")
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
                  and not p.name.startswith('.')]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def _extract_magnet_title(magnet: str) -> str:
    m = re.search(r'[&?]dn=([^&]+)', magnet)
    if m:
        return urllib.parse.unquote_plus(m.group(1))
    return "Vídeo"


def _get_video_duration(file_path: Path) -> int:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(file_path)],
            capture_output=True, text=True, timeout=30
        )
        if probe.returncode == 0:
            info = json.loads(probe.stdout)
            return int(float(info.get("format", {}).get("duration", 0)))
    except Exception:
        pass
    return 0



async def _fetch_torrent_url(url: str) -> dict:
    """Try to download a .torrent file from an HTTP URL. Returns {path, title} or {error}."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_torrent_url_sync, url)

def _fetch_torrent_url_sync(url: str) -> dict:
    TORRENT_MAGIC = b"d8:announce"  # .torrent files start with a bencode dict
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/x-bittorrent,application/octet-stream,*/*",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True, stream=True)
        if r.status_code == 403:
            return {"error": "El sitio bloquea descargas directas (error 403). Opciones:\n• Copia el enlace magnet del sitio web\n• Descarga el .torrent en tu navegador y súbelo aquí con 'Archivo .torrent'"}
        if r.status_code != 200:
            return {"error": f"Error al descargar el .torrent (HTTP {r.status_code})"}
        data = b"".join(r.iter_content(1024 * 256))
        # Verify it looks like a torrent file
        if not (data[:10] == TORRENT_MAGIC or data[:1] == b"d"):
            ct = r.headers.get("content-type", "")
            if "html" in ct or "text" in ct:
                return {"error": "El sitio devuelve una página web, no un .torrent. Usa el enlace magnet o sube el archivo .torrent manualmente."}
        tmp_path = VIDEOS_PATH / f"tmp_{secrets.token_hex(8)}.torrent"
        tmp_path.write_bytes(data)
        # Extract title from URL
        title = Path(url.split("?")[0].split("/")[-1]).stem.replace("_", " ").replace("+", " ")
        return {"path": tmp_path, "title": title or "Video"}
    except requests.exceptions.ConnectionError:
        return {"error": "No se pudo conectar al servidor. Verifica la URL."}
    except requests.exceptions.Timeout:
        return {"error": "Tiempo de espera agotado al descargar el .torrent."}
    except Exception as e:
        return {"error": f"Error al descargar: {str(e)[:100]}"}
def _do_torrent_download(video_id: int, source: str, is_file_path: bool):
    download_dir = VIDEOS_PATH / str(video_id)
    download_dir.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    try:
        args = [
            "aria2c",
            f"--dir={download_dir}",
            "--seed-time=0",
            "--file-allocation=none",
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=1M",
            "--enable-dht=true",
            "--enable-peer-exchange=true",
            "--follow-torrent=true",
            "--bt-stop-timeout=300",
            "--bt-tracker-timeout=60",
            "--bt-max-peers=100",
            "--piece-length=1M",
            "--quiet=true",
            "--summary-interval=0",
            "--max-overall-download-limit=0",
            source,
        ]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            err = err.split("\n")[-1][:300] if err else "Error desconocido"
            conn.execute("UPDATE videos SET status='error', error_msg=? WHERE id=?", (err, video_id))
            conn.commit()
            return
        video_file = _find_video_file(download_dir)
        if not video_file:
            conn.execute("UPDATE videos SET status='error', error_msg='No se encontró archivo de vídeo' WHERE id=?", (video_id,))
            conn.commit()
            return
        duration = _get_video_duration(video_file)
        size = video_file.stat().st_size
        title = video_file.stem.replace('.', ' ').replace('_', ' ')
        conn.execute(
            "UPDATE videos SET status='ready', file_path=?, title=?, duration=?, size=? WHERE id=?",
            (str(video_file), title, duration, size, video_id)
        )
        conn.commit()
    except subprocess.TimeoutExpired:
        conn.execute("UPDATE videos SET status='error', error_msg='Tiempo de descarga agotado' WHERE id=?", (video_id,))
        conn.commit()
    except Exception as e:
        conn.execute("UPDATE videos SET status='error', error_msg=? WHERE id=?", (str(e)[:300], video_id))
        conn.commit()
    finally:
        if is_file_path and Path(source).exists():
            Path(source).unlink(missing_ok=True)
        conn.close()


@app.get("/videos", response_class=HTMLResponse)
async def videos_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    videos = rows_to_list(conn.execute(
        "SELECT * FROM videos WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "videos.html",
        {"username": request.session["username"],
         "user_role": request.session["user_role"],
         "csrf_token": csrf, "videos": videos}
    )


@app.get("/api/videos/list")
async def api_videos_list(request: Request):
    session = get_session(request)
    user_id = session["user_id"]
    conn = get_db()
    videos = rows_to_list(conn.execute(
        "SELECT id, title, status, duration, size, error_msg, created_at FROM videos WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall())
    conn.close()
    return JSONResponse({"videos": videos})


@app.get("/api/videos/status/{video_id}")
async def api_video_status(video_id: int, request: Request):
    session = get_session(request)
    user_id = session["user_id"]
    conn = get_db()
    row = row_to_dict(conn.execute(
        "SELECT id, title, status, duration, size, error_msg FROM videos WHERE id=? AND user_id=?",
        (video_id, user_id)
    ).fetchone())
    conn.close()
    if not row:
        raise HTTPException(status_code=404)
    # Estimate download progress by checking dir size vs. nothing (simple ready/not ready)
    if row["status"] == "downloading":
        dl_dir = VIDEOS_PATH / str(video_id)
        downloaded = sum(f.stat().st_size for f in dl_dir.rglob("*") if f.is_file()) if dl_dir.exists() else 0
        row["downloaded_bytes"] = downloaded
    return JSONResponse(row)


@app.post("/api/videos/add")
async def api_videos_add(request: Request, torrent_file: Optional[UploadFile] = File(None)):
    session = get_session(request)
    user_id = session["user_id"]

    magnet = None
    csrf_token = None
    is_file_path = False
    source = None
    title = "Vídeo"

    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type:
        form = await request.form()
        magnet = (form.get("magnet") or "").strip()
        csrf_token = form.get("csrf_token", "")
        torrent_file = form.get("torrent_file")
    else:
        data = await request.json()
        magnet = (data.get("magnet") or "").strip()
        csrf_token = data.get("csrf_token", "")

    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)

    VIDEOS_PATH.mkdir(parents=True, exist_ok=True)

    if torrent_file and hasattr(torrent_file, "filename") and torrent_file.filename:
        # Save .torrent file temporarily
        tmp_path = VIDEOS_PATH / f"tmp_{secrets.token_hex(8)}.torrent"
        content = await torrent_file.read()
        tmp_path.write_bytes(content)
        source = str(tmp_path)
        is_file_path = True
        title = Path(torrent_file.filename).stem
    elif magnet:
        if not (magnet.startswith("magnet:") or magnet.startswith("http://") or magnet.startswith("https://")):
            return JSONResponse({"error": "URL inválida. Usa un enlace magnet: o URL de .torrent"})
        if magnet.startswith("magnet:"):
            source = magnet
            title = _extract_magnet_title(magnet)
        else:
            fetch_result = await _fetch_torrent_url(magnet)
            if "error" in fetch_result:
                return JSONResponse(fetch_result)
            source = str(fetch_result["path"])
            is_file_path = True
            title = fetch_result.get("title", "Video")
    else:
        return JSONResponse({"error": "Proporciona un enlace magnet o un archivo .torrent"})

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO videos (user_id, title, status, torrent_source) VALUES (?, ?, 'downloading', ?)",
        (user_id, title, magnet or Path(source).name)
    )
    video_id = cur.lastrowid
    conn.commit()
    conn.close()

    asyncio.get_running_loop().run_in_executor(_executor, _do_torrent_download, video_id, source, is_file_path)

    return JSONResponse({"success": True, "video_id": video_id, "title": title})


@app.get("/api/videos/stream/{video_id}")
async def api_video_stream(video_id: int, request: Request, token: str = ""):
    # Accept session cookie OR temporary stream token (for cast/AirPlay)
    user_id = None
    if token:
        entry = _stream_tokens.get(token)
        if entry and entry[0] == video_id and entry[2] > _time.time():
            user_id = entry[1]
    if user_id is None:
        if "user_id" not in request.session:
            raise HTTPException(status_code=401)
        user_id = request.session["user_id"]
    conn = get_db()
    video = row_to_dict(conn.execute(
        "SELECT * FROM videos WHERE id=? AND user_id=?", (video_id, user_id)
    ).fetchone())
    conn.close()
    if not video:
        raise HTTPException(status_code=404)
    if video["status"] != "ready" or not video["file_path"]:
        raise HTTPException(status_code=409, detail="El vídeo aún no está listo")
    file_path = Path(video["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    suffix = file_path.suffix.lower()
    mime = VIDEO_MIMES.get(suffix, "video/mp4")
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
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

    chunk_size = 1024 * 512  # 512 KB chunks

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

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "no-store",
        "Content-Type": mime,
    }
    return StreamingResponse(iterfile(), status_code=status_code, headers=headers, media_type=mime)


@app.post("/api/videos/delete")
async def api_videos_delete(request: Request):
    session = get_session(request)
    data = await request.json()
    video_id = int(data.get("video_id") or 0)
    csrf_token = data.get("csrf_token", "")
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    user_id = session["user_id"]
    conn = get_db()
    video = row_to_dict(conn.execute(
        "SELECT * FROM videos WHERE id=? AND user_id=?", (video_id, user_id)
    ).fetchone())
    if not video:
        conn.close()
        return JSONResponse({"error": "Vídeo no encontrado"}, status_code=404)
    # Delete the entire download directory
    dl_dir = VIDEOS_PATH / str(video_id)
    if dl_dir.exists():
        shutil.rmtree(dl_dir, ignore_errors=True)
    conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/videos/rename")
async def api_videos_rename(request: Request):
    session = get_session(request)
    data = await request.json()
    video_id = int(data.get("video_id") or 0)
    new_title = (data.get("title") or "").strip()[:200]
    csrf_token = data.get("csrf_token", "")
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    if not new_title:
        return JSONResponse({"error": "Título vacío"})
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("UPDATE videos SET title=? WHERE id=? AND user_id=?", (new_title, video_id, user_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


# ══════════════════════════════════════════════════════════════════════════════
# PUSH NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

# In-memory stream tokens: token → (video_id, user_id, expires_epoch)
import time as _time
_stream_tokens: dict = {}

def _make_stream_token(video_id: int, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = _time.time() + 4 * 3600  # 4 hours
    _stream_tokens[token] = (video_id, user_id, expires)
    # Clean up expired tokens
    now = _time.time()
    expired = [k for k, v in _stream_tokens.items() if v[2] < now]
    for k in expired:
        del _stream_tokens[k]
    return token


def _load_vapid():
    try:
        keys = json.loads(Path("data/vapid_keys.json").read_text())
        return keys["private_pem"], keys["public_key"]
    except Exception:
        return None, None

VAPID_PRIVATE_PEM, VAPID_PUBLIC_KEY = _load_vapid()
VAPID_CLAIMS = {"sub": "mailto:admin@baluhome.app"}


def _send_push_sync(endpoint: str, p256dh: str, auth: str, title: str, body: str, url: str):
    if not VAPID_PRIVATE_PEM:
        return
    try:
        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE_PEM,
            vapid_claims=VAPID_CLAIMS,
        )
    except Exception:
        pass


def _send_push_sync_to_user(user_id: int, title: str, body: str, url: str = "/"):
    """Synchronous wrapper — safe to call from a thread pool executor."""
    if not VAPID_PRIVATE_PEM:
        return
    conn = get_db()
    subs = rows_to_list(conn.execute(
        "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?", (user_id,)
    ).fetchall())
    conn.close()
    for s in subs:
        _send_push_sync(s["endpoint"], s["p256dh"], s["auth"], title, body, url)


async def push_to_user(user_id: int, title: str, body: str, url: str = "/"):
    if not VAPID_PRIVATE_PEM:
        return
    conn = get_db()
    subs = rows_to_list(conn.execute(
        "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?", (user_id,)
    ).fetchall())
    conn.close()
    loop = asyncio.get_running_loop()
    for s in subs:
        loop.run_in_executor(_executor, _send_push_sync,
                             s["endpoint"], s["p256dh"], s["auth"], title, body, url)


@app.get("/api/videos/token/{video_id}")
async def api_video_token(video_id: int, request: Request):
    session = get_session(request)
    user_id = session["user_id"]
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM videos WHERE id=? AND user_id=? AND status='ready'",
        (video_id, user_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404)
    token = _make_stream_token(video_id, user_id)
    return JSONResponse({"token": token})


@app.get("/api/push/vapid-key")
async def api_push_vapid_key():
    return JSONResponse({"key": VAPID_PUBLIC_KEY or ""})


@app.post("/api/push/subscribe")
async def api_push_subscribe(request: Request):
    session = get_session(request)
    data = await request.json()
    endpoint = (data.get("endpoint") or "").strip()
    p256dh = (data.get("p256dh") or "").strip()
    auth = (data.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return JSONResponse({"error": "Datos incompletos"})
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("""
        INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,
            p256dh=excluded.p256dh, auth=excluded.auth
    """, (user_id, endpoint, p256dh, auth))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


@app.post("/api/push/unsubscribe")
async def api_push_unsubscribe(request: Request):
    session = get_session(request)
    data = await request.json()
    endpoint = (data.get("endpoint") or "").strip()
    user_id = session["user_id"]
    conn = get_db()
    conn.execute("DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user_id, endpoint))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})



# ══════════════════════════════════════════════════════════════════════════════
# NOTES — Obsidian-style markdown vault (PRIVATE per user)
# Each user has their own vault at data/vault/<user_id>/ containing real files
# (.md notes + attachments). Fully compatible with Obsidian via zip export/import.
# ══════════════════════════════════════════════════════════════════════════════

VAULT_BASE = Path("data/vault")

# Non-markdown files we surface in the tree as attachments
ATTACHMENT_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".pdf", ".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".webm", ".mov",
    ".txt", ".csv", ".json", ".canvas", ".excalidraw",
}


def _user_vault_root(request: Request) -> Path:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    root = VAULT_BASE / str(uid)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_vault_path(root: Path, rel: str) -> Path:
    """Resolve a vault-relative path safely, blocking traversal outside the user's vault."""
    rel = (rel or "").strip().lstrip("/")
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(status_code=400, detail="Ruta inválida")
    target = (root / Path(*parts)).resolve() if parts else root
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="Ruta fuera del vault")
    return target


def _sanitize_name(name: str) -> str:
    name = (name or "").strip().replace("/", "-").replace("\\", "-")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name).strip(". ")
    return name[:120]


def _rel_of(root: Path, p: Path) -> str:
    return p.resolve().relative_to(root).as_posix()


def _build_tree(root: Path, directory: Path) -> list:
    """Recursively build a sorted folder/note/attachment tree."""
    items = []
    try:
        entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except FileNotFoundError:
        return items
    for entry in entries:
        if entry.name.startswith("."):
            continue  # hide .obsidian etc. from the tree (still exported/imported)
        if entry.is_dir():
            items.append({
                "type": "folder",
                "name": entry.name,
                "path": _rel_of(root, entry),
                "children": _build_tree(root, entry),
            })
        elif entry.suffix.lower() == ".md":
            items.append({
                "type": "note",
                "name": entry.stem,
                "path": _rel_of(root, entry),
                "updated": int(entry.stat().st_mtime),
            })
        else:
            items.append({
                "type": "file",
                "name": entry.name,
                "ext": entry.suffix.lower().lstrip("."),
                "path": _rel_of(root, entry),
                "updated": int(entry.stat().st_mtime),
            })
    return items


@app.get("/notes", response_class=HTMLResponse)
async def notes_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    _user_vault_root(request)
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "notes.html",
        {"username": request.session["username"],
         "user_role": request.session["user_role"],
         "csrf_token": csrf,
         "user_id": request.session["user_id"]}
    )


@app.get("/api/vault/tree")
async def api_vault_tree(request: Request):
    root = _user_vault_root(request)
    return JSONResponse({"tree": _build_tree(root, root)})


@app.get("/api/vault/search")
async def api_vault_search(request: Request, q: str = ""):
    """Full-text search across the user's .md notes. Case-insensitive, all terms
    must appear (AND). Returns a short snippet around the first match per note."""
    root = _user_vault_root(request)
    terms = [t.lower() for t in (q or "").split() if t]
    if not terms or len("".join(terms)) < 2:
        return JSONResponse({"results": []})

    results = []
    for p in root.rglob("*.md"):
        if len(results) >= 100:
            break
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue  # skip .obsidian/.trash etc.
        try:
            if p.stat().st_size > 2_000_000:
                with p.open("r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read(2_000_000)
            else:
                text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        low = text.lower()
        if not all(t in low for t in terms):
            continue
        idx = low.find(terms[0])
        start, end = max(0, idx - 40), min(len(text), idx + len(terms[0]) + 90)
        snippet = " ".join(text[start:end].split())
        results.append({
            "path": rel.as_posix(),
            "name": p.stem,
            "snippet": ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else ""),
        })
    return JSONResponse({"results": results})


@app.get("/api/vault/file")
async def api_vault_file(request: Request, path: str = ""):
    root = _user_vault_root(request)
    target = _safe_vault_path(root, path)
    if not target.is_file() or target.suffix.lower() != ".md":
        return JSONResponse({"error": "Nota no encontrada"}, status_code=404)
    try:
        content = target.read_text(encoding="utf-8")
    except Exception:
        content = ""
    return JSONResponse({
        "path": _rel_of(root, target),
        "name": target.stem,
        "content": content,
        "updated": int(target.stat().st_mtime),
    })


@app.get("/api/vault/asset")
async def api_vault_asset(request: Request, path: str = ""):
    """Serve an attachment (image/pdf/etc.) from the user's vault for embeds."""
    root = _user_vault_root(request)
    target = _safe_vault_path(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404)
    mime, _ = mimetypes.guess_type(target.name)
    return Response(
        content=target.read_bytes(),
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.post("/api/vault/save")
async def api_vault_save(request: Request):
    session = get_session(request)
    root = _user_vault_root(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    path = (data.get("path") or "").strip()
    content = data.get("content")
    if content is None:
        content = ""
    if len(content) > 5_000_000:
        return JSONResponse({"error": "Nota demasiado grande"}, status_code=400)
    target = _safe_vault_path(root, path)
    if target.suffix.lower() != ".md":
        return JSONResponse({"error": "Solo se permiten archivos .md"}, status_code=400)
    if target == root:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return JSONResponse({"success": True, "path": _rel_of(root, target), "updated": int(target.stat().st_mtime)})


@app.post("/api/vault/create")
async def api_vault_create(request: Request):
    session = get_session(request)
    root = _user_vault_root(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    parent = (data.get("parent") or "").strip()
    name = _sanitize_name(data.get("name") or "")
    kind = data.get("type") or "note"
    if not name:
        return JSONResponse({"error": "Nombre vacío"}, status_code=400)
    parent_dir = _safe_vault_path(root, parent)
    if parent_dir.exists() and not parent_dir.is_dir():
        parent_dir = parent_dir.parent
    prefix = (_rel_of(root, parent_dir) + "/") if parent_dir != root else ""
    if kind == "folder":
        target = _safe_vault_path(root, prefix + name)
        if target.exists():
            return JSONResponse({"error": "Ya existe una carpeta con ese nombre"}, status_code=400)
        target.mkdir(parents=True, exist_ok=True)
        return JSONResponse({"success": True, "type": "folder", "path": _rel_of(root, target)})
    else:
        if not name.lower().endswith(".md"):
            name += ".md"
        target = _safe_vault_path(root, prefix + name)
        if target.exists():
            return JSONResponse({"error": "Ya existe una nota con ese nombre"}, status_code=400)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# " + target.stem + "\n\n", encoding="utf-8")
        return JSONResponse({"success": True, "type": "note", "path": _rel_of(root, target)})


@app.post("/api/vault/rename")
async def api_vault_rename(request: Request):
    session = get_session(request)
    root = _user_vault_root(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    src = _safe_vault_path(root, data.get("path") or "")
    new_name = _sanitize_name(data.get("name") or "")
    if not new_name or src == root or not src.exists():
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)
    if src.is_file() and src.suffix.lower() == ".md" and not new_name.lower().endswith(".md"):
        new_name += ".md"
    dst = src.parent / new_name
    if dst.resolve() == src.resolve():
        return JSONResponse({"success": True, "path": _rel_of(root, src)})
    if dst.exists():
        return JSONResponse({"error": "Ya existe un elemento con ese nombre"}, status_code=400)
    src.rename(dst)
    return JSONResponse({"success": True, "path": _rel_of(root, dst)})


@app.post("/api/vault/delete")
async def api_vault_delete(request: Request):
    session = get_session(request)
    root = _user_vault_root(request)
    data = await request.json()
    if not ensure_csrf(session, data.get("csrf_token", "")):
        return JSONResponse({"error": "Token inválido"}, status_code=403)
    target = _safe_vault_path(root, data.get("path") or "")
    if target == root or not target.exists():
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)
    return JSONResponse({"success": True})


@app.get("/api/vault/export")
async def api_vault_export(request: Request):
    """Download the whole private vault as a .zip — open it directly in Obsidian."""
    root = _user_vault_root(request)
    username = request.session.get("username", "vault")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        empty = True
        for p in sorted(root.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(root).as_posix())
                empty = False
        if empty:
            # Keep a valid, openable vault even when empty
            zf.writestr("Bienvenida.md", "# Bienvenida\n\nEsta es tu bóveda exportada.\n")
    buf.seek(0)
    fname = f"vault-{_sanitize_name(username) or 'baluhome'}-{date.today().isoformat()}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/vault/import")
async def api_vault_import(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("merge"),
    csrf_token: str = Form(""),
):
    session = get_session(request)
    root = _user_vault_root(request)
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)

    # Stream from the on-disk temp file (SpooledTemporaryFile) — never load the
    # whole upload into RAM, so a ~1 GB vault import stays memory-safe.
    upload = file.file
    upload.seek(0, os.SEEK_END)
    size = upload.tell()
    upload.seek(0)
    if size > 1100 * 1024 * 1024:
        return JSONResponse({"error": "Archivo demasiado grande (máx 1 GB)"}, status_code=400)
    fname = (file.filename or "").lower()

    # Single .md file import
    if fname.endswith(".md"):
        if mode == "replace":
            for child in root.iterdir():
                (shutil.rmtree if child.is_dir() else (lambda c: c.unlink()))(child)
        safe_name = _sanitize_name(Path(file.filename).stem) + ".md"
        with open(root / safe_name, "wb") as out:
            shutil.copyfileobj(upload, out)
        return JSONResponse({"success": True, "imported": 1})

    if not fname.endswith(".zip"):
        return JSONResponse({"error": "Sube un .zip de bóveda o un archivo .md"}, status_code=400)

    try:
        zf = zipfile.ZipFile(upload)
    except zipfile.BadZipFile:
        return JSONResponse({"error": "ZIP inválido"}, status_code=400)

    names = [n for n in zf.namelist() if not n.endswith("/")]
    if not names:
        return JSONResponse({"error": "El ZIP está vacío"}, status_code=400)

    # If everything lives under a single top-level folder, strip it so the vault
    # contents land at the root (matches zipping an Obsidian vault folder).
    tops = {n.replace("\\", "/").split("/", 1)[0] for n in names}
    strip = ""
    if len(tops) == 1:
        only = tops.pop()
        if any(n.replace("\\", "/").startswith(only + "/") for n in names):
            strip = only + "/"

    if mode == "replace":
        for child in root.iterdir():
            (shutil.rmtree if child.is_dir() else (lambda c: c.unlink()))(child)

    imported = 0
    for n in names:
        norm = n.replace("\\", "/")
        if strip and norm.startswith(strip):
            norm = norm[len(strip):]
        if not norm or norm.startswith("__MACOSX/"):
            continue
        try:
            target = _safe_vault_path(root, norm)  # zip-slip guarded
        except HTTPException:
            continue
        if target == root:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(n) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        imported += 1

    return JSONResponse({"success": True, "imported": imported})


# Image/attachment extensions accepted when pasting or uploading into a note
_ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
              ".bmp", ".ico", ".avif", ".heic", ".heif"}


def _best_image_dir(root: Path):
    """Folder in the vault that already holds the most images — that's where the
    user keeps attachments (e.g. `images/`), so new pasted images go there too.
    Returns None if the vault has no images yet."""
    counts = {}
    for p in root.rglob("*"):
        if p.suffix.lower() not in _ASSET_EXT:
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue  # ignore .obsidian/.trash etc.
        if not p.is_file():
            continue
        counts[p.parent] = counts.get(p.parent, 0) + 1
    if not counts:
        return None
    # most images wins; ties → shallowest folder (closer to vault root)
    return max(counts.items(), key=lambda kv: (kv[1], -len(kv[0].parts)))[0]


def _optimize_image(raw: bytes, ext: str):
    """Re-encode a pasted/uploaded image to WebP (great size savings at high
    quality). Returns (bytes, ext). Leaves vectors (.svg) and animations alone,
    and never produces a file larger than the original."""
    if ext == ".svg":
        return raw, ext
    try:
        from PIL import ImageOps
        im = Image.open(io.BytesIO(raw))
        if getattr(im, "is_animated", False):   # animated gif/webp → keep as-is
            return raw, ext
        im = ImageOps.exif_transpose(im)         # respeta la orientación (fotos de móvil)
        if max(im.size) > 3000:                  # limita dimensiones absurdas
            im.thumbnail((3000, 3000))
        if im.mode in ("P", "LA"):
            im = im.convert("RGBA")
        elif im.mode == "CMYK":
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=85, method=6)
        webp = buf.getvalue()
        if webp and len(webp) < len(raw):
            return webp, ".webp"
    except Exception:
        pass
    return raw, ext


@app.post("/api/vault/upload-asset")
async def api_vault_upload_asset(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(""),
):
    """Store an image (pasted/dropped/picked in the editor) inside the user's
    vault, in whichever folder already holds the most images (falling back to
    `attachments/`), using an Obsidian-style name. Returns its path so the client
    can insert an ![[embed]]."""
    session = get_session(request)
    root = _user_vault_root(request)
    if not ensure_csrf(session, csrf_token):
        return JSONResponse({"error": "Token inválido"}, status_code=403)

    raw = await file.read()
    if not raw:
        return JSONResponse({"error": "Archivo vacío"}, status_code=400)
    if len(raw) > 25 * 1024 * 1024:
        return JSONResponse({"error": "Imagen demasiado grande (máx 25 MB)"}, status_code=400)

    orig = file.filename or ""
    ext = Path(orig).suffix.lower()
    if ext in ("", ".jpe"):
        guessed = mimetypes.guess_extension(file.content_type or "") or ""
        ext = ".jpg" if (ext == ".jpe" or guessed == ".jpe") else (guessed.lower() or ext)
    if ext not in _ASSET_EXT:
        return JSONResponse({"error": "Formato de imagen no admitido"}, status_code=400)

    # Optimiza el formato (→ WebP) para ahorrar almacenamiento manteniendo calidad.
    raw, ext = _optimize_image(raw, ext)

    attach = _best_image_dir(root) or (root / "attachments")
    attach.mkdir(parents=True, exist_ok=True)

    base = _sanitize_name(Path(orig).stem) if orig else ""
    low = base.lower()
    if not base or low in ("image", "blob", "imagen") or low.startswith(("image.", "screenshot")):
        base = "Pasted image " + datetime.now().strftime("%Y%m%d%H%M%S")

    name = base + ext
    target = attach / name
    i = 1
    while target.exists():
        name = f"{base} {i}{ext}"
        target = attach / name
        i += 1
    target.write_bytes(raw)

    return JSONResponse({"success": True, "path": _rel_of(root, target), "name": name})
