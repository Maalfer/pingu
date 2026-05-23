from django.urls import path
from . import views

app_name = "videos_api"

urlpatterns = [
    path("list", views.list_videos, name="list"),
    path("status/<int:video_id>", views.status, name="status"),
    path("add", views.add, name="add"),
    path("delete", views.delete_video, name="delete"),
    path("rename", views.rename_video, name="rename"),
    path("stream/<int:video_id>", views.stream, name="stream"),
    path("token/<int:video_id>", views.get_stream_token, name="token"),
]
