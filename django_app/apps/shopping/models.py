"""Lista de la compra compartida entre amigos."""
from django.conf import settings
from django.db import models


class ShoppingItem(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shopping_items")
    text = models.CharField(max_length=500)
    done = models.BooleanField(default=False)
    added_by_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("done", "-created_at")
        indexes = [
            models.Index(fields=["user", "done"]),
        ]

    def __str__(self) -> str:
        return self.text
