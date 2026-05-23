from django.urls import path
from . import views

app_name = "files_api"

urlpatterns = [
    path("list", views.api_list, name="list"),
    path("create-folder", views.api_create_folder, name="create_folder"),
    path("upload", views.api_upload, name="upload"),
    path("rename", views.api_rename, name="rename"),
    path("set-color", views.api_set_color, name="set_color"),
    path("move", views.api_move, name="move"),
    path("delete", views.api_delete, name="delete"),
    path("download/<int:file_id>", views.api_download, name="download"),
]
