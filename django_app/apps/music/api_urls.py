"""Endpoints raíz que el JS frontend espera:
  - /api/stream/<id>
  - /api/download
  - /api/delete
  - /fragments/library
"""
from django.urls import path
from . import views

app_name = "music_api"
urlpatterns = [
    path("stream/<int:song_id>", views.stream, name="stream"),
    path("download",             views.download, name="download"),
    path("delete",               views.delete_song, name="delete"),
]
