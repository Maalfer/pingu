from django.urls import path
from . import views
app_name = "chat_api"
urlpatterns = [
    path("send", views.api_send, name="send"),
    path("messages/<int:friend_id>", views.api_messages, name="messages"),
]
