from django.urls import path
from . import views

app_name = "notes_api"

urlpatterns = [
    path("tree", views.tree, name="tree"),
    path("file", views.file_get, name="file_get"),
    path("save", views.file_save, name="file_save"),
    path("create", views.create, name="create"),
    path("rename", views.rename, name="rename"),
    path("delete", views.delete, name="delete"),
    path("search", views.search, name="search"),
    path("upload", views.upload, name="upload"),
    path("asset", views.asset, name="asset"),
    path("export", views.export_vault, name="export"),
    path("import", views.import_vault, name="import"),
]
