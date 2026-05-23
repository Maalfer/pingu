"""ASGI shim. Cargado por gunicorn/uvicorn (systemd: baluhome.service)."""
import os
import sys

DJANGO_DIR = "/var/www/balusong/django_app"
sys.path.insert(0, DJANGO_DIR)
os.chdir(DJANGO_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "baluhome.fatimaymariosecasan.es,127.0.0.1,localhost")

from config.asgi import application

app = application
