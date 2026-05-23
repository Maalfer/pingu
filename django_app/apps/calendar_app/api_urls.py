from django.urls import path
from . import views
app_name = "calendar_api"
urlpatterns = [
    path("add",    views.api_add,    name="add"),
    path("update", views.api_update_legacy, name="update"),
    path("delete", views.api_delete_legacy, name="delete"),
    path("events", views.api_events, name="events"),
]
