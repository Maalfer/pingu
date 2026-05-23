"""Cuentas / gastos compartidos con amigos."""
from django.conf import settings
from django.db import models

from apps.friends.models import Friendship


class Transaction(models.Model):
    """Cargo / abono dentro de una amistad. Importe positivo o negativo."""

    friendship = models.ForeignKey(
        Friendship, on_delete=models.CASCADE, related_name="transactions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=400)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.amount}€ — {self.description}"
