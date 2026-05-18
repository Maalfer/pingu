#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv if missing
if [ ! -d "venv" ]; then
  echo "Creando entorno virtual..."
  python3 -m venv venv
fi

# Install/upgrade dependencies
echo "Instalando dependencias..."
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

# Check yt-dlp
if ! command -v yt-dlp &>/dev/null && ! venv/bin/python -c "import yt_dlp" &>/dev/null; then
  echo ""
  echo "⚠  yt-dlp no encontrado en el PATH del sistema."
  echo "   Instala con:  brew install yt-dlp  (recomendado)"
  echo "   o:            pip install yt-dlp"
  echo ""
fi

echo ""
echo "🎵 Balusong arrancando en http://localhost:8000"
echo "   Usuario: admin  /  Contraseña: admin"
echo ""

venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
