from django.urls import path
from . import views

app_name = "todos_api"
urlpatterns = [
    path("list", views.api_list, name="list"),
    path("add", views.api_add, name="add"),
    path("toggle", views.api_toggle_legacy, name="toggle"),
    path("delete", views.api_delete_legacy, name="delete"),
    path("clear-done", views.api_clear, name="clear"),
]
