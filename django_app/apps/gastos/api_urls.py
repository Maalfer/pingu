from django.urls import path
from . import views
app_name = "gastos_api"
urlpatterns = [
    path("add-transaction",    views.api_add, name="add"),
    path("delete-transaction", views.api_delete_legacy, name="delete"),
]
