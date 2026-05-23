"""Hasher compatible con los bcrypt hashes heredados (formato `$2b$…`).

Django no los reconoce por defecto. Con este hasher puede verificar contraseñas
existentes y, al primer login con éxito, Django las re-hashea con su algoritmo
por defecto (pbkdf2_sha256) — migración gradual transparente para el usuario.
"""
import bcrypt
from django.contrib.auth.hashers import BasePasswordHasher


class BCryptLegacyHasher(BasePasswordHasher):
    """Acepta hashes bcrypt sin el prefijo de algoritmo que Django añade por defecto."""

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
