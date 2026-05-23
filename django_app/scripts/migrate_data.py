"""Migración de datos: SQLite FastAPI antiguo → DB Django nueva.

Uso:
    cd /home/mario/balusong_django
    venv/bin/python scripts/migrate_data.py

Lee /var/www/balusong/data/balusong.db (proyecto FastAPI antiguo) y crea
los registros equivalentes en la base de datos Django, preservando IDs
de usuario (importantes para que los paths físicos de notas/uploads
sigan funcionando).
"""
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# Carga Django para usar el ORM.
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402
django.setup()

from django.db import transaction  # noqa: E402
from django.utils.dateparse import parse_datetime  # noqa: E402

from apps.accounts.models import User, InviteToken, ActivityLog  # noqa: E402
from apps.music.models import Song  # noqa: E402
from apps.shopping.models import ShoppingItem  # noqa: E402
from apps.todos.models import Todo  # noqa: E402
from apps.calendar_app.models import CalendarEvent  # noqa: E402
from apps.friends.models import Friendship  # noqa: E402
from apps.gastos.models import Transaction as GastoTx  # noqa: E402
from apps.chat.models import Message  # noqa: E402
from apps.files_app.models import FileNode  # noqa: E402
from apps.videos.models import Video  # noqa: E402
from apps.push_notif.models import PushSubscription  # noqa: E402

SRC_DB = Path("/var/www/balusong/data/balusong.db")


def parse_dt(s):
    if not s:
        return None
    try:
        return parse_datetime(s) or datetime.fromisoformat(s)
    except Exception:
        return None


def main():
    if not SRC_DB.exists():
        print(f"FATAL: source DB not found: {SRC_DB}")
        sys.exit(1)
    src = sqlite3.connect(str(SRC_DB))
    src.row_factory = sqlite3.Row

    with transaction.atomic():
        # 1. Users — preservamos ID original (importante para paths físicos).
        print("Migrating users...")
        for r in src.execute("SELECT * FROM users"):
            u, _ = User.objects.update_or_create(
                pk=r["id"],
                defaults=dict(
                    username=r["username"],
                    password="bcrypt_legacy$" + r["password_hash"],  # bcrypt hash; Django lo verá como hash desconocido.
                    role=r["role"] or "user",
                    theme=(dict(r).get("theme") or "dark"),
                    date_joined=parse_dt(r["created_at"]) or datetime.now(),
                ),
            )
        print(f"  → {User.objects.count()} users")

        print("Migrating invite_tokens...")
        for r in src.execute("SELECT * FROM invite_tokens"):
            creator = User.objects.filter(pk=r["created_by"]).first()
            if not creator:
                continue
            user_used = User.objects.filter(pk=r["used_by"]).first() if r["used_by"] else None
            InviteToken.objects.update_or_create(
                token=r["token"],
                defaults=dict(
                    created_by=creator, used_by=user_used,
                    is_used=bool(r["is_used"]),
                    created_at=parse_dt(r["created_at"]) or datetime.now(),
                    used_at=parse_dt(r["used_at"]),
                ),
            )

        print("Migrating songs...")
        for r in src.execute("SELECT * FROM songs"):
            user = User.objects.filter(pk=r["user_id"]).first()
            if not user: continue
            Song.objects.update_or_create(
                youtube_id=r["youtube_id"],
                defaults=dict(
                    user=user, title=r["title"], artist=r["artist"] or "Unknown",
                    youtube_url=r["youtube_url"], file_path=r["file_path"],
                    thumbnail=r["thumbnail"] or "", duration=r["duration"] or 0,
                ),
            )
        print(f"  → {Song.objects.count()} songs")

        print("Migrating shopping_items...")
        ShoppingItem.objects.all().delete()
        for r in src.execute("SELECT * FROM shopping_items"):
            u = User.objects.filter(pk=r["user_id"]).first()
            if not u: continue
            ShoppingItem.objects.create(
                user=u, text=r["text"], done=bool(r["done"]),
                added_by_name=dict(r).get("added_by_name") or "",
            )

        print("Migrating todos...")
        Todo.objects.all().delete()
        for r in src.execute("SELECT * FROM todos"):
            u = User.objects.filter(pk=r["user_id"]).first()
            if not u: continue
            Todo.objects.create(user=u, title=r["title"], done=bool(r["done"]))

        print("Migrating calendar_events...")
        CalendarEvent.objects.all().delete()
        for r in src.execute("SELECT * FROM calendar_events"):
            u = User.objects.filter(pk=r["user_id"]).first()
            if not u: continue
            CalendarEvent.objects.create(
                user=u, title=r["title"], day=r["day"], month=r["month"],
                color=r["color"] or "#06b6d4",
                description=dict(r).get("description") or "",
                is_all_day=bool(dict(r).get("is_all_day") or 1),
                start_time=dict(r).get("start_time"),
                end_time=dict(r).get("end_time"),
            )

        print("Migrating friendships...")
        Friendship.objects.all().delete()
        for r in src.execute("SELECT * FROM friendships"):
            req = User.objects.filter(pk=r["requester_id"]).first()
            adr = User.objects.filter(pk=r["addressee_id"]).first()
            if not (req and adr): continue
            Friendship.objects.create(
                pk=r["id"], requester=req, addressee=adr, status=r["status"],
            )

        print("Migrating transactions (gastos)...")
        GastoTx.objects.all().delete()
        for r in src.execute("SELECT * FROM transactions"):
            f = Friendship.objects.filter(pk=r["friendship_id"]).first()
            u = User.objects.filter(pk=r["user_id"]).first()
            if not (f and u): continue
            GastoTx.objects.create(
                friendship=f, user=u, amount=r["amount"], description=r["description"],
            )

        print("Migrating messages...")
        Message.objects.all().delete()
        for r in src.execute("SELECT * FROM messages"):
            s = User.objects.filter(pk=r["sender_id"]).first()
            re = User.objects.filter(pk=r["receiver_id"]).first()
            if not (s and re): continue
            Message.objects.create(
                sender=s, receiver=re, content=r["content"],
                read_at=parse_dt(r["read_at"]),
            )

        print("Migrating files...")
        FileNode.objects.all().delete()
        # Pasada en dos rondas para resolver parent_id correctamente.
        rows = list(src.execute("SELECT * FROM files ORDER BY parent_id IS NOT NULL, id"))
        id_map = {}
        for r in rows:
            u = User.objects.filter(pk=r["user_id"]).first()
            if not u: continue
            parent = id_map.get(r["parent_id"]) if r["parent_id"] else None
            node = FileNode.objects.create(
                user=u, parent=parent, name=r["name"],
                is_folder=bool(r["is_folder"]),
                color=r["color"], storage_path=r["storage_path"],
                mime_type=r["mime_type"], size=r["size"] or 0,
            )
            id_map[r["id"]] = node

        print("Migrating videos...")
        Video.objects.all().delete()
        for r in src.execute("SELECT * FROM videos"):
            u = User.objects.filter(pk=r["user_id"]).first()
            if not u: continue
            Video.objects.create(
                user=u, title=r["title"], file_path=r["file_path"],
                duration=r["duration"] or 0, size=r["size"] or 0,
                status=r["status"], error_msg=r["error_msg"],
                torrent_source=r["torrent_source"],
            )

        print("Migrating push_subscriptions...")
        PushSubscription.objects.all().delete()
        for r in src.execute("SELECT * FROM push_subscriptions"):
            u = User.objects.filter(pk=r["user_id"]).first()
            if not u: continue
            PushSubscription.objects.update_or_create(
                endpoint=r["endpoint"],
                defaults=dict(user=u, p256dh=r["p256dh"], auth=r["auth"]),
            )

        print("Migrating activity_logs...")
        ActivityLog.objects.all().delete()
        for r in src.execute("SELECT * FROM activity_logs LIMIT 500"):
            u = User.objects.filter(pk=r["user_id"]).first() if r["user_id"] else None
            ActivityLog.objects.create(
                user=u, username=r["username"] or "",
                action=r["action"], detail=r["detail"] or "",
            )

    src.close()
    print()
    print("=== Migración completada ===")
    print(f"Users:      {User.objects.count()}")
    print(f"Songs:      {Song.objects.count()}")
    print(f"Shopping:   {ShoppingItem.objects.count()}")
    print(f"Todos:      {Todo.objects.count()}")
    print(f"Events:     {CalendarEvent.objects.count()}")
    print(f"Friends:    {Friendship.objects.count()}")
    print(f"GastosTx:   {GastoTx.objects.count()}")
    print(f"Messages:   {Message.objects.count()}")
    print(f"FileNodes:  {FileNode.objects.count()}")
    print(f"Videos:     {Video.objects.count()}")
    print(f"PushSubs:   {PushSubscription.objects.count()}")
    print()
    print("NOTA: las contraseñas bcrypt no son verificables por Django nativo.")
    print("Cada usuario tendrá que resetear su contraseña, o ejecuta este script")
    print("una vez los usuarios estén creados y luego pídeles que la cambien.")


if __name__ == "__main__":
    main()
