"""
main.py — FastAPI application for BaluHome
"""
import os
import re
import sys
import json
import secrets
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from db import (
    get_db, init_db, verify_password, hash_password,
    UPLOADS_PATH, DB_PATH
)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="baluhome-secret-change-in-prod", max_age=60*60*24*365*2, https_only=True, same_site="lax")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

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


@app.on_event("startup")
async def startup_event():
    init_db()


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
            return RedirectResponse("/home", status_code=302)
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

    pending_gastos = conn.execute(
        "SELECT COUNT(*) FROM friendships WHERE addressee_id = ? AND status = 'pending'",
        (user_id,)
    ).fetchone()[0]

    shopping_count = conn.execute(
        "SELECT COUNT(*) FROM shopping_items WHERE user_id = ? AND done = 0",
        (user_id,)
    ).fetchone()[0]

    unread_msgs = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE receiver_id = ? AND read_at IS NULL",
        (user_id,)
    ).fetchone()[0]

    todos_count = conn.execute(
        "SELECT COUNT(*) FROM todos WHERE user_id = ? AND done = 0",
        (user_id,)
    ).fetchone()[0]

    conn.close()

    csrf = get_or_create_csrf(request.session)

    return templates.TemplateResponse(
        request, "home.html",
        {
            "username": request.session["username"],
            "user_role": request.session["user_role"],
            "pending_friends": pending_gastos,
            "shopping_count": shopping_count,
            "unread_msgs": unread_msgs,
            "todos_count": todos_count,
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


@app.get("/fragments/dashboard", response_class=HTMLResponse)
async def fragment_dashboard(request: Request):
    if "user_id" not in request.session:
        if not request.headers.get("X-Fragment"):
            return RedirectResponse("/app", status_code=302)
        return Response(status_code=401)
    if not request.headers.get("X-Fragment"):
        return RedirectResponse("/app", status_code=302)

    user_id = request.session["user_id"]
    conn = get_db()
    user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    song_count = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "fragments/dashboard.html",
        {"user": dict(user_row), "song_count": song_count, "csrf_token": csrf},
    )


@app.get("/fragments/admin", response_class=HTMLResponse)
async def fragment_admin(request: Request):
    if "user_id" not in request.session:
        if not request.headers.get("X-Fragment"):
            return RedirectResponse("/app", status_code=302)
        return Response(status_code=401)
    if not request.headers.get("X-Fragment"):
        return RedirectResponse("/app", status_code=302)
    if request.session.get("user_role") != "admin":
        return Response(status_code=403)

    conn = get_db()
    invites = rows_to_list(conn.execute(
        """SELECT i.*, u1.username AS creator, u2.username AS used_by_name
           FROM invite_tokens i
           JOIN users u1 ON i.created_by = u1.id
           LEFT JOIN users u2 ON i.used_by = u2.id
           ORDER BY i.created_at DESC LIMIT 50"""
    ).fetchall())
    users = rows_to_list(conn.execute(
        """SELECT u.id, u.username, u.role, u.created_at, COUNT(s.id) AS song_count
           FROM users u LEFT JOIN songs s ON s.user_id = u.id
           GROUP BY u.id ORDER BY u.created_at ASC"""
    ).fetchall())
    all_songs = rows_to_list(conn.execute(
        """SELECT s.*, u.username AS added_by
           FROM songs s JOIN users u ON s.user_id = u.id
           ORDER BY s.created_at DESC"""
    ).fetchall())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    songs_json = json.dumps(all_songs)
    return templates.TemplateResponse(
        request, "fragments/admin.html",
        {"users": users, "invites": invites, "all_songs": all_songs,
         "csrf_token": csrf, "songs_json": songs_json},
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
    if song["user_id"] != user_id and user_role != "admin":
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
    import shutil
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
    import asyncio
    loop = asyncio.get_event_loop()
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
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", str(request.base_url.hostname))
    base_url = f"{proto}://{host}"
    link = f"{base_url}/register?token={token}"
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
        "SELECT * FROM shopping_items WHERE user_id = ? ORDER BY done ASC, created_at DESC",
        (user_id,)
    ).fetchall())
    conn.close()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "shopping.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "items": items, "csrf_token": csrf}
    )


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
    cur = conn.execute("INSERT INTO shopping_items (user_id, text) VALUES (?, ?)", (user_id, text))
    item_id = cur.lastrowid
    conn.commit()
    conn.close()
    log_activity(user_id, session.get("username","?"), "shopping_add", f"Añadió '{text}' a la lista de la compra")
    return JSONResponse({"success": True, "id": item_id, "text": text})


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
        "SELECT * FROM shopping_items WHERE id = ? AND user_id = ?", (item_id, user_id)
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
    conn.execute("DELETE FROM shopping_items WHERE id = ? AND user_id = ?", (item_id, user_id))
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
    conn.execute("DELETE FROM shopping_items WHERE user_id = ? AND done = 1", (user_id,))
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

from PIL import Image as _PILImage
import io as _io
import calendar as _calendar
from datetime import date as _date

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
    today = _date.today()
    csrf = get_or_create_csrf(request.session)
    return templates.TemplateResponse(
        request, "calendar.html",
        {"username": request.session["username"], "user_role": request.session["user_role"],
         "events": events, "csrf_token": csrf,
         "today_day": today.day, "today_month": today.month, "today_year": today.year}
    )


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
    user_id = session["user_id"]
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO calendar_events (user_id, title, day, month, color) VALUES (?, ?, ?, ?, ?)",
        (user_id, title, day, month, color)
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
    user_id = session["user_id"]
    conn = get_db()
    conn.execute(
        "UPDATE calendar_events SET title=?, day=?, month=?, color=? WHERE id=? AND user_id=?",
        (title, day, month, color, event_id, user_id)
    )
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})




# ── PROFILE ──────────────────────────────────────────────────────────────────

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse("/", status_code=302)
    user_id = request.session["user_id"]
    conn = get_db()
    user = row_to_dict(conn.execute(
        "SELECT id, username, role, created_at, profile_picture FROM users WHERE id = ?",
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
    has_avatar = Path(f"{UPLOADS_PATH.parent}/static/avatars/{user_id}.jpg").exists()
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
        img = _PILImage.open(_io.BytesIO(contents)).convert("RGB")
        img.thumbnail((256, 256), _PILImage.LANCZOS)
        avatar_dir = Path(f"{UPLOADS_PATH.parent}/static/avatars")
        avatar_dir.mkdir(exist_ok=True)
        out_path = avatar_dir / f"{user_id}.jpg"
        img.save(str(out_path), "JPEG", quality=88, optimize=True)
        conn = get_db()
        conn.execute("UPDATE users SET profile_picture = ? WHERE id = ?",
                     (str(out_path), user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        return JSONResponse({"error": f"Error procesando imagen: {e}"})
    return JSONResponse({"success": True, "url": f"/static/avatars/{user_id}.jpg"})


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
    return RedirectResponse("/profile", status_code=302)



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
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", str(request.base_url.hostname))
    base_url = f"{proto}://{host}"
    link = f"{base_url}/register?token={token}"
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
    import subprocess as _sp, shutil as _sh
    # VPS total / used
    df = _sp.check_output(["df", "-B1", "/"], text=True).splitlines()[1].split()
    total_bytes = int(df[1])
    used_bytes  = int(df[2])
    # App usage
    try:
        app_bytes = int(_sp.check_output(["du", "-sb", "/var/www/balusong"], text=True).split()[0])
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
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0a0a0f",
        "theme_color": "#0a0a0f",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "categories": ["utilities", "entertainment"],
    }
    return JSONResponse(manifest)


@app.get("/sw.js")
async def serve_sw():
    sw_content = """/* BaluHome Service Worker v3 */
const CACHE = 'baluhome-v3';

self.addEventListener('install', e => {
  e.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/fragments/')) return;

  // Network-first for HTML and CSS so updates are always live
  if (e.request.headers.get('Accept')?.includes('text/html') || url.pathname.endsWith('.css')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Cache-first for other static assets (icons, fonts, images)
  e.respondWith(caches.match(e.request).then(cached => {
    if (cached) return cached;
    return fetch(e.request).then(res => {
      if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
      return res;
    });
  }));
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

