"""Mi nube — archivos y carpetas del usuario."""
from django.conf import settings
from django.db import models


class FileNode(models.Model):
    """Nodo de árbol: archivo o carpeta."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="files")
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    name = models.CharField(max_length=255)
    is_folder = models.BooleanField(default=False)
    color = models.CharField(max_length=16, blank=True, null=True)
    storage_path = models.CharField(max_length=500, blank=True, null=True)
    mime_type = models.CharField(max_length=120, blank=True, null=True)
    size = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "files_node"
        ordering = ("-is_folder", "name")
        indexes = [models.Index(fields=["user", "parent"])]

    def __str__(self) -> str:
        return ("[D] " if self.is_folder else "") + self.name
