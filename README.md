# BaluHome

Plataforma personal privada para el hogar, accesible como PWA. Incluye múltiples mini-aplicaciones integradas bajo una sola interfaz con diseño oscuro optimizado para móvil.

## Características

- Lista de la compra, Tareas, Chat, Calendario
- Gastos compartidos con balance y deshacer
- Canciones (yt-dlp), Perfil, Amigos, Panel Admin
- PWA instalable (Android/iOS), recuerda la última página

## Stack

FastAPI + Jinja2 + SQLite + bcrypt + Pillow + yt-dlp + nginx + Cloudflare

## Instalación

```bash
git clone git@github.com:Maalfer/BaluHome.git
cd BaluHome
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001
```

## Docker

```bash
docker build -t baluhome .
docker run -d -p 8001:8001 -v $(pwd)/data:/app/data -v $(pwd)/uploads:/app/uploads baluhome
```

## Despliegue

nginx proxy → uvicorn en 127.0.0.1:8001, cert wildcard Cloudflare  
URL: https://baluhome.fatimaymariosecasan.es

systemctl restart balusong
