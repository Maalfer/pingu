"""Videoteca personal (descargas de torrents)."""
from django.conf import settings
from django.db import models


class Video(models.Model):
    STATUS_CHOICES = (
        ("downloading", "Descargando"),
        ("ready", "Listo"),
        ("error", "Error"),
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="videos")
    title = models.CharField(max_length=300, default="Video")
    file_path = models.CharField(max_length=500, blank=True, null=True)
    duration = models.PositiveIntegerField(default=0)
    size = models.BigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="downloading")
    error_msg = models.TextField(blank=True, null=True)
    torrent_source = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return self.title
