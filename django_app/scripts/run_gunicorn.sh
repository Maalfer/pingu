#!/bin/bash
cd /home/mario/balusong_django
exec /home/mario/balusong_django/venv/bin/gunicorn \
    --workers 2 --threads 2 \
    --timeout 120 \
    --bind 127.0.0.1:8002 \
    --access-logfile - \
    --error-logfile - \
    config.wsgi:application
