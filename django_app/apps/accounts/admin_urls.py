from django.urls import path
from . import admin_views

app_name = "admin_api"

urlpatterns = [
    path("delete-user", admin_views.delete_user, name="delete_user"),
    path("change-user-password", admin_views.change_user_password, name="change_user_password"),
    path("change-user-username", admin_views.change_user_username, name="change_user_username"),
    path("change-user-role", admin_views.change_user_role, name="change_user_role"),
    path("create-invite", admin_views.create_invite, name="create_invite"),
    path("create-user", admin_views.create_user, name="create_user"),
    path("disk-usage", admin_views.disk_usage, name="disk_usage"),
    path("activity", admin_views.activity, name="activity"),
]
