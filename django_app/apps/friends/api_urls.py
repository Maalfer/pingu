from django.urls import path
from . import views
app_name = "friends_api"
urlpatterns = [
    path("send",   views.api_send_legacy,   name="send"),
    path("accept", views.api_accept_legacy, name="accept"),
    path("reject", views.api_reject_legacy, name="reject"),
    path("list",   views.api_list_legacy,   name="list"),
    path("pending-count", views.api_pending_count, name="pending_count"),
]
