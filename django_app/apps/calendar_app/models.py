"""Eventos del calendario."""
from django.conf import settings
from django.db import models


class CalendarEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_events")
    title = models.CharField(max_length=200)
    day = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    color = models.CharField(max_length=16, default="#06b6d4")
    description = models.TextField(blank=True, default="")
    is_all_day = models.BooleanField(default=True)
    start_time = models.CharField(max_length=5, blank=True, null=True, help_text="HH:MM")
    end_time = models.CharField(max_length=5, blank=True, null=True, help_text="HH:MM")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("month", "day", "start_time")

    def __str__(self) -> str:
        return f"{self.day}/{self.month} — {self.title}"
