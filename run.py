import sys
import os
import subprocess
from pathlib import Path

ROOT   = Path(__file__).parent.resolve()
VENV   = ROOT / "venv"
PYTHON = VENV / "bin" / "python"
PIP    = VENV / "bin" / "pip"

# Prefer Python 3.12+ to avoid yt-dlp deprecation warnings
def _find_python() -> str:
    for candidate in ("python3.12", "python3.11", "python3.10", "python3"):
        path = subprocess.run(["which", candidate], capture_output=True, text=True).stdout.strip()
        if path:
            return path
    return sys.executable


def bootstrap():
    base_python = _find_python()

    # If venv exists but was built with Python 3.9, rebuild it with the better version
    marker = VENV / "pyvenv.cfg"
    if marker.exists():
        cfg = marker.read_text()
        if "3.9" in cfg and base_python != sys.executable:
            print(f"Actualizando venv de Python 3.9 → {base_python} ...")
            import shutil
            shutil.rmtree(VENV)

    if not VENV.exists():
        print(f"Creando entorno virtual ({base_python}) ...")
        subprocess.run([base_python, "-m", "venv", str(VENV)], check=True)

    print("Instalando dependencias...")
    subprocess.run([str(PIP), "install", "-q", "--upgrade", "pip"], check=True)
    subprocess.run([str(PIP), "install", "-q", "-r", str(ROOT / "requirements.txt")], check=True)

    os.chdir(ROOT)
    os.execv(str(PYTHON), [str(PYTHON), __file__] + sys.argv[1:])


def main():
    import uvicorn

    os.chdir(ROOT)
    dev = "--dev" in sys.argv

    print("🎵 Balusong → http://localhost:8000")
    print("   Usuario: admin  /  Contraseña: admin")
    if dev:
        print("   Modo: desarrollo (reload activado)")
    print("   Ctrl+C para cerrar")
    print()

    try:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=dev)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nBalusong cerrado.")


if __name__ == "__main__":
    if not str(sys.executable).startswith(str(VENV)):
        bootstrap()
    else:
        main()
