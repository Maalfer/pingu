"""Hasher compatible con los bcrypt hashes del proyecto FastAPI antiguo.

Los usuarios actuales tienen passwords almacenados como bcrypt (formato `$2b$…`).
Django no los entiende por defecto: con este hasher Django puede verificar
contraseñas existentes. Al primer login exitoso, Django re-hashea con su algoritmo
por defecto (pbkdf2_sha256), migrando gradualmente sin que el usuario note nada.
"""
import bcrypt
from django.contrib.auth.hashers import BasePasswordHasher


class BCryptLegacyHasher(BasePasswordHasher):
    """Acepta hashes bcrypt sin prefijo Django (como vienen del FastAPI antiguo)."""

    algorithm = "bcrypt_legacy"

    def encode(self, password, salt):
        # No se usa para hashear nuevas contraseñas; sólo para verificar las
        # heredadas. Devolvemos formato `bcrypt_legacy$<hash>`.
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        return f"{self.algorithm}${hashed.decode('utf-8')}"

    def verify(self, password, encoded):
        # encoded puede venir como "bcrypt_legacy$$2b$..." o directamente "$2b$..."
        if encoded.startswith(self.algorithm + "$"):
            stored = encoded.split("$", 1)[1]
        else:
            stored = encoded
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except (ValueError, TypeError):
            return False

    def safe_summary(self, encoded):
        return {"algorithm": self.algorithm, "hash": encoded[:6] + "…"}

    def must_update(self, encoded):
        # Forzamos re-hash al algoritmo Django por defecto en el siguiente login.
        return True

    def harden_runtime(self, password, encoded):
        pass
