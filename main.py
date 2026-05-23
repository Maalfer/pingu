"""ASGI shim. Cargado por uvicorn (systemd: balusong.service) y delega en Django."""
import os
import sys

DJANGO_DIR = "/var/www/balusong/django_app"
sys.path.insert(0, DJANGO_DIR)
os.chdir(DJANGO_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "baluhome.fatimaymariosecasan.es,127.0.0.1,localhost")

from config.asgi import application

# Uvicorn busca `app` (de "main:app" en el ExecStart).
app = application
