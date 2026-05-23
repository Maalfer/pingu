from django.urls import path
from . import views

app_name = "push_notif"

urlpatterns = [
    path("subscribe", views.subscribe, name="subscribe"),
    path("unsubscribe", views.unsubscribe, name="unsubscribe"),
    path("vapid-key", views.vapid_key, name="vapid_key"),
]
