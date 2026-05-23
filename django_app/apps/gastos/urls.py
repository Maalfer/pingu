from django.urls import path
from . import views
app_name = "gastos"
urlpatterns = [
    path("", views.index, name="index"),
    path("<int:friend_id>", views.detail, name="detail"),
]
