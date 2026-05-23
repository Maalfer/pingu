"""Lista de tareas pendientes (To-Do List)."""
from django.conf import settings
from django.db import models


class Todo(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="todos")
    title = models.CharField(max_length=500)
    done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("done", "-created_at")
        indexes = [
            models.Index(fields=["user", "done"]),
        ]

    def __str__(self) -> str:
        return self.title
