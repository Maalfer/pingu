"""Amistades entre usuarios."""
from django.conf import settings
from django.db import models


class Friendship(models.Model):
    """Relación dirigida entre dos usuarios. status = pending|accepted."""

    STATUS_CHOICES = (
        ("pending", "Pendiente"),
        ("accepted", "Aceptada"),
    )

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friendships_sent"
    )
    addressee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friendships_received"
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("requester", "addressee")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.requester} → {self.addressee} ({self.status})"
