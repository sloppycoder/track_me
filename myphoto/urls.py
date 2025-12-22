from django.urls import path

from . import views

app_name = "myphoto"

urlpatterns = [
    path("hello/", views.hello, name="hello"),
]
