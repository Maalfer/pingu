"""User account model."""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user — extiende AbstractUser para añadir role y theme.

    Conserva los nombres de columna (`role`, `theme`) del proyecto original
    para que la migración de datos sea directa.
    """

    ROLE_CHOICES = (
        ("user", "Usuario"),
        ("admin", "Admin"),
    )
    THEME_CHOICES = (
        ("dark", "Oscuro"),
        ("light", "Claro"),
        ("dracula", "Drácula"),
        ("pink", "Rosa"),
        ("aqua", "Aqua"),
    )

    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default="user")
    theme = models.CharField(max_length=16, choices=THEME_CHOICES, default="dark")

    class Meta:
        db_table = "auth_user_custom"
        ordering = ("username",)

    def __str__(self) -> str:
        return self.username

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class InviteToken(models.Model):
    """Token de invitación para registrar nuevos usuarios."""

    token = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="invites_created"
    )
    used_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invite_used",
    )
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"InviteToken({self.token[:8]}…, used={self.is_used})"


class ActivityLog(models.Model):
    """Registro de acciones administrativas."""

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity"
    )
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=64)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
